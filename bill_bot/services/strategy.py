from __future__ import annotations

import json
from dataclasses import dataclass

import redis.asyncio as redis

from bill_bot.services.candle_store import Candle
from bill_bot.services.fractals import Fractal
from bill_bot.services.indicators import alligator_ema, atr, is_alligator_tangled, is_sleep, spread_lines


@dataclass(frozen=True)
class SignalCandidate:
    pair: str
    tf: str
    side: str  # LONG | SHORT
    cluster_t: int
    cluster_price: float
    entry_trigger: float
    stop_loss: float
    take_profit: float
    rr: float
    setup_probability: float = 0.5
    trailing_strategy: str = "TRENDING"
    suggested_risk_pct: float | None = None
    setup_features: dict | None = None

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "tf": self.tf,
            "side": self.side,
            "cluster_t": self.cluster_t,
            "cluster_price": self.cluster_price,
            "entry_trigger": self.entry_trigger,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "rr": self.rr,
            "setup_probability": self.setup_probability,
            "trailing_strategy": self.trailing_strategy,
            "suggested_risk_pct": self.suggested_risk_pct,
            "setup_features": self.setup_features,
        }


class StrategyEngine:
    def __init__(
        self,
        tf: str,
        sleep_window: int,
        sleep_k: float,
        rr: float = 1.5,
        sz_decimals: int | None = None,
        max_decimals: int = 6,
        tick_size_fallback: float = 0.01,
        atr_period: int = 14,
        cluster_spread_atr_max: float = 4.0,
    ):
        self.tf = tf
        self.sleep_window = sleep_window
        self.sleep_k = sleep_k
        self.rr = rr
        self.sz_decimals = sz_decimals
        self.max_decimals = max_decimals
        self.tick_size_fallback = tick_size_fallback
        self.atr_period = atr_period
        self.cluster_spread_atr_max = cluster_spread_atr_max

    def tick_size_for_price(self, price: float) -> float:
        if price <= 0:
            return self.tick_size_fallback
        if self.sz_decimals is None:
            return self.tick_size_fallback

        decimals_limit = self.max_decimals - int(self.sz_decimals)
        if decimals_limit < 0:
            decimals_limit = 0

        tick_by_decimals = 10 ** (-decimals_limit) if decimals_limit > 0 else 1.0

        import math

        exp = math.floor(math.log10(price)) - 4
        tick_by_sig = 10 ** exp

        return float(max(tick_by_decimals, tick_by_sig))

    def evaluate(
        self,
        pair: str,
        candles: list[Candle],
        fractals: list[Fractal],
    ) -> tuple[list[SignalCandidate], dict]:
        if len(candles) < max(self.sleep_window, 20, self.atr_period + 1):
            return [], {"reason": "not_enough_candles", "candles": len(candles)}

        closes = [c.c for c in candles]
        alli = alligator_ema(closes)
        atr_values = atr(candles, period=self.atr_period)
        spreads = spread_lines(alli["jaw"], alli["teeth"], alli["lips"])
        sleep, med_spread = is_sleep(spreads, last_close=closes[-1], window=self.sleep_window, k=self.sleep_k)
        tangled = is_alligator_tangled(alli["jaw"], alli["teeth"], alli["lips"], window=self.sleep_window)
        last_atr = float(atr_values[-1]) if atr_values else 0.0
        ctx: dict = {
            "sleep": sleep,
            "tangled": tangled,
            "sleep_med_spread": med_spread,
            "last_close": closes[-1],
            "atr": last_atr,
            "jaw": alli["jaw"][-1],
            "teeth": alli["teeth"][-1],
            "lips": alli["lips"][-1],
        }
        if not sleep:
            ctx["reason"] = "not_sleep"
            return [], ctx
        if not tangled:
            ctx["reason"] = "not_tangled"
            return [], ctx

        highs = [f for f in fractals if f.kind == "HIGH"]
        lows = [f for f in fractals if f.kind == "LOW"]
        if not highs or not lows:
            ctx["reason"] = "no_fractals"
            return [], ctx

        ctx["last_high_t"] = highs[-1].t
        ctx["last_low_t"] = lows[-1].t

        long_entry = highs[-1]
        short_entry = lows[-1]
        ctx["long_cluster_t"] = long_entry.t
        ctx["short_cluster_t"] = short_entry.t

        setup_anchor_t = min(int(long_entry.t), int(short_entry.t))
        setup_anchor_idx = self._find_candle_index(candles, setup_anchor_t)
        if setup_anchor_idx is None:
            ctx["reason"] = "setup_anchor_not_found"
            return [], ctx

        latest_crosses = {
            "jaw_teeth": self._latest_cross_index(alli["jaw"], alli["teeth"], setup_anchor_idx),
            "teeth_lips": self._latest_cross_index(alli["teeth"], alli["lips"], setup_anchor_idx),
            "jaw_lips": self._latest_cross_index(alli["jaw"], alli["lips"], setup_anchor_idx),
        }
        ctx["pre_setup_crosses"] = latest_crosses
        if any(v is None for v in latest_crosses.values()):
            ctx["reason"] = "no_pre_setup_cross"
            return [], ctx

        if last_atr <= 0:
            ctx["reason"] = "invalid_atr"
            return [], ctx

        cluster_spread = abs(float(long_entry.price) - float(short_entry.price))
        cluster_spread_atr = cluster_spread / last_atr
        ctx["cluster_spread"] = cluster_spread
        ctx["cluster_spread_atr"] = cluster_spread_atr
        if cluster_spread_atr > self.cluster_spread_atr_max:
            ctx["reason"] = "cluster_spread_too_wide"
            return [], ctx

        out: list[SignalCandidate] = []
        setup_features = {
            "atr": last_atr,
            "sleep_med_spread": float(med_spread),
            "cluster_spread": cluster_spread,
            "cluster_spread_atr": cluster_spread_atr,
            "cluster_spread_atr_bucket": self._spread_bucket(cluster_spread_atr),
            "sleep_window": int(self.sleep_window),
            "atr_period": int(self.atr_period),
            "long_cluster_t": int(long_entry.t),
            "short_cluster_t": int(short_entry.t),
            "pre_setup_crosses": latest_crosses,
        }

        tick = self.tick_size_for_price(max(float(long_entry.price), float(short_entry.price)))

        entry = float(long_entry.price) + tick
        stop = float(short_entry.price) - tick
        if stop < entry:
            tp = entry + self.rr * (entry - stop)
            out.append(
                SignalCandidate(
                pair=pair,
                tf=self.tf,
                side="LONG",
                cluster_t=long_entry.t,
                cluster_price=long_entry.price,
                entry_trigger=entry,
                stop_loss=stop,
                take_profit=tp,
                rr=self.rr,
                setup_features={**setup_features, "side": "LONG"},
                )
            )
        else:
            ctx["invalid_long_sl"] = {"entry": entry, "stop": stop}

        entry = float(short_entry.price) - tick
        stop = float(long_entry.price) + tick
        if stop > entry:
            tp = entry - self.rr * (stop - entry)
            out.append(
                SignalCandidate(
                pair=pair,
                tf=self.tf,
                side="SHORT",
                cluster_t=short_entry.t,
                cluster_price=short_entry.price,
                entry_trigger=entry,
                stop_loss=stop,
                take_profit=tp,
                rr=self.rr,
                setup_features={**setup_features, "side": "SHORT"},
                )
            )
        else:
            ctx["invalid_short_sl"] = {"entry": entry, "stop": stop}

        if not out:
            ctx["reason"] = "no_entry_cluster"
        return out, ctx

    @staticmethod
    def _find_candle_index(candles: list[Candle], fractal_t: int) -> int | None:
        for idx in range(len(candles) - 1, -1, -1):
            if int(candles[idx].t) == int(fractal_t):
                return idx
        return None

    @staticmethod
    def _latest_cross_index(a: list[float], b: list[float], end_idx: int) -> int | None:
        if end_idx <= 0:
            return None
        eps = 1e-12
        latest: int | None = None
        limit = min(end_idx, len(a) - 1, len(b) - 1)
        for i in range(1, limit + 1):
            prev = float(a[i - 1]) - float(b[i - 1])
            cur = float(a[i]) - float(b[i])
            if abs(prev) <= eps or abs(cur) <= eps:
                latest = i
                continue
            if (prev < -eps and cur > eps) or (prev > eps and cur < -eps):
                latest = i
        return latest

    @staticmethod
    def _spread_bucket(cluster_spread_atr: float) -> str:
        if cluster_spread_atr < 1.5:
            return "tight"
        if cluster_spread_atr < 3.0:
            return "normal"
        return "wide"


class RedisSignalStore:
    def __init__(self, r: redis.Redis):
        self.r = r

    @staticmethod
    def key(pair: str, tf: str, side: str) -> str:
        return f"signal_candidate:last:{pair}:{tf}:{str(side).upper()}"

    async def get_last(self, pair: str, tf: str, side: str) -> dict | None:
        raw = await self.r.get(self.key(pair, tf, side))
        if not raw:
            return None
        return json.loads(raw)

    async def set_last(self, cand: SignalCandidate) -> None:
        await self.r.set(self.key(cand.pair, cand.tf, cand.side), json.dumps(cand.to_dict(), separators=(",", ":")))

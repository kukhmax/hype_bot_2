from __future__ import annotations

import json
from dataclasses import dataclass

import redis.asyncio as redis

from bill_bot.services.candle_store import Candle
from bill_bot.services.fractals import Fractal
from bill_bot.services.indicators import alligator_ema, is_sleep, spread_lines


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
        }


class StrategyEngine:
    def __init__(self, tf: str, sleep_window: int, sleep_k: float, tick_size: float, rr: float = 1.5):
        self.tf = tf
        self.sleep_window = sleep_window
        self.sleep_k = sleep_k
        self.tick_size = tick_size
        self.rr = rr

    def evaluate(
        self,
        pair: str,
        candles: list[Candle],
        fractals: list[Fractal],
    ) -> tuple[SignalCandidate | None, dict]:
        if len(candles) < max(self.sleep_window, 20):
            return None, {"reason": "not_enough_candles", "candles": len(candles)}

        closes = [c.c for c in candles]
        alli = alligator_ema(closes)
        spreads = spread_lines(alli["jaw"], alli["teeth"], alli["lips"])
        sleep, med_spread = is_sleep(spreads, last_close=closes[-1], window=self.sleep_window, k=self.sleep_k)
        ctx: dict = {
            "sleep": sleep,
            "sleep_med_spread": med_spread,
            "last_close": closes[-1],
            "jaw": alli["jaw"][-1],
            "teeth": alli["teeth"][-1],
            "lips": alli["lips"][-1],
        }
        if not sleep:
            ctx["reason"] = "not_sleep"
            return None, ctx

        highs = [f for f in fractals if f.kind == "HIGH"]
        lows = [f for f in fractals if f.kind == "LOW"]
        if not highs or not lows:
            ctx["reason"] = "no_fractals"
            return None, ctx

        last_high = highs[-1]
        last_low = lows[-1]
        ctx["last_high_t"] = last_high.t
        ctx["last_low_t"] = last_low.t

        long_ok = last_high.close > last_high.teeth
        short_ok = last_low.close < last_low.teeth

        if long_ok:
            sl = self._find_long_sl(lows, before_t=last_high.t)
            if sl is None:
                ctx["reason"] = "no_long_sl_cluster"
                return None, ctx
            entry = last_high.price + self.tick_size
            stop = sl.price
            if stop >= entry:
                ctx["reason"] = "invalid_long_sl"
                return None, ctx
            tp = entry + self.rr * (entry - stop)
            cand = SignalCandidate(
                pair=pair,
                tf=self.tf,
                side="LONG",
                cluster_t=last_high.t,
                cluster_price=last_high.price,
                entry_trigger=entry,
                stop_loss=stop,
                take_profit=tp,
                rr=self.rr,
            )
            return cand, ctx

        if short_ok:
            sl = self._find_short_sl(highs, before_t=last_low.t)
            if sl is None:
                ctx["reason"] = "no_short_sl_cluster"
                return None, ctx
            entry = last_low.price - self.tick_size
            stop = sl.price
            if stop <= entry:
                ctx["reason"] = "invalid_short_sl"
                return None, ctx
            tp = entry - self.rr * (stop - entry)
            cand = SignalCandidate(
                pair=pair,
                tf=self.tf,
                side="SHORT",
                cluster_t=last_low.t,
                cluster_price=last_low.price,
                entry_trigger=entry,
                stop_loss=stop,
                take_profit=tp,
                rr=self.rr,
            )
            return cand, ctx

        ctx["reason"] = "no_entry_cluster"
        return None, ctx

    @staticmethod
    def _find_long_sl(lows: list[Fractal], before_t: int) -> Fractal | None:
        for f in reversed(lows):
            if f.t >= before_t:
                continue
            if f.close < f.teeth:
                return f
        return None

    @staticmethod
    def _find_short_sl(highs: list[Fractal], before_t: int) -> Fractal | None:
        for f in reversed(highs):
            if f.t >= before_t:
                continue
            if f.close > f.teeth:
                return f
        return None


class RedisSignalStore:
    def __init__(self, r: redis.Redis):
        self.r = r

    @staticmethod
    def key(pair: str, tf: str) -> str:
        return f"signal_candidate:last:{pair}:{tf}"

    async def get_last(self, pair: str, tf: str) -> dict | None:
        raw = await self.r.get(self.key(pair, tf))
        if not raw:
            return None
        return json.loads(raw)

    async def set_last(self, cand: SignalCandidate) -> None:
        await self.r.set(self.key(cand.pair, cand.tf), json.dumps(cand.to_dict(), separators=(",", ":")))

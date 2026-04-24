from __future__ import annotations

import json
from dataclasses import dataclass

import redis.asyncio as redis

from bill_bot.services.candle_store import Candle
from bill_bot.services.strategy import SignalCandidate


@dataclass(frozen=True)
class PendingOrder:
    side: str
    trigger: float
    cluster_t: int
    stop_loss: float
    take_profit: float
    qty: float

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "trigger": self.trigger,
            "cluster_t": self.cluster_t,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "qty": self.qty,
        }

    @staticmethod
    def from_dict(d: dict) -> "PendingOrder":
        return PendingOrder(
            side=str(d["side"]),
            trigger=float(d["trigger"]),
            cluster_t=int(d["cluster_t"]),
            stop_loss=float(d["stop_loss"]),
            take_profit=float(d["take_profit"]),
            qty=float(d["qty"]),
        )


@dataclass(frozen=True)
class Position:
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    qty: float
    opened_t: int
    cluster_t: int

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "qty": self.qty,
            "opened_t": self.opened_t,
            "cluster_t": self.cluster_t,
        }

    @staticmethod
    def from_dict(d: dict) -> "Position":
        return Position(
            side=str(d["side"]),
            entry=float(d["entry"]),
            stop_loss=float(d["stop_loss"]),
            take_profit=float(d["take_profit"]),
            qty=float(d["qty"]),
            opened_t=int(d["opened_t"]),
            cluster_t=int(d["cluster_t"]),
        )


class RedisTradeState:
    def __init__(self, r: redis.Redis):
        self.r = r

    @staticmethod
    def state_key(pair: str, tf: str) -> str:
        return f"state:{pair}:{tf}"

    @staticmethod
    def pnl_key(pair: str, tf: str) -> str:
        return f"pnl:{pair}:{tf}"

    async def get(self, pair: str, tf: str) -> dict:
        raw = await self.r.get(self.state_key(pair, tf))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    async def set(self, pair: str, tf: str, state: dict) -> None:
        await self.r.set(self.state_key(pair, tf), json.dumps(state, separators=(",", ":")))

    async def set_pnl(self, pair: str, tf: str, payload: dict) -> None:
        await self.r.set(self.pnl_key(pair, tf), json.dumps(payload, separators=(",", ":")))


class ExecutionDryRun:
    def __init__(self, store: RedisTradeState, tf: str, virtual_equity: float, risk_pct: float):
        self.store = store
        self.tf = tf
        self.virtual_equity = float(virtual_equity)
        self.risk_pct = float(risk_pct)

    def _qty_from_risk(self, entry: float, sl: float) -> float:
        risk_amount = self.virtual_equity * (self.risk_pct / 100.0)
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.0
        return risk_amount / dist

    @staticmethod
    def _unrealized_pnl(pos: Position, mark: float) -> float:
        if pos.side == "LONG":
            return (mark - pos.entry) * pos.qty
        return (pos.entry - mark) * pos.qty

    @staticmethod
    def _realized_pnl(pos: Position, exit_price: float) -> float:
        if pos.side == "LONG":
            return (exit_price - pos.entry) * pos.qty
        return (pos.entry - exit_price) * pos.qty

    async def maybe_place_order(self, pair: str, candle: Candle, cand: SignalCandidate | None) -> PendingOrder | None:
        if cand is None:
            return None
        state = await self.store.get(pair, self.tf)
        if state.get("pos") or state.get("ord"):
            return None
        qty = self._qty_from_risk(cand.entry_trigger, cand.stop_loss)
        if qty <= 0:
            return None
        o = PendingOrder(
            side=cand.side,
            trigger=cand.entry_trigger,
            cluster_t=cand.cluster_t,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            qty=qty,
        )
        state["ord"] = o.to_dict()
        state["last_t"] = int(candle.t)
        await self.store.set(pair, self.tf, state)
        return o

    async def on_candle(self, pair: str, candle: Candle) -> dict:
        state = await self.store.get(pair, self.tf)
        last_t = int(state.get("last_t", 0))
        if candle.t <= last_t:
            return {"changed": False}

        ord_raw = state.get("ord")
        pos_raw = state.get("pos")

        changed = False
        event: dict = {"changed": False}

        if pos_raw:
            pos = Position.from_dict(pos_raw)
            hit_sl = candle.l <= pos.stop_loss if pos.side == "LONG" else candle.h >= pos.stop_loss
            hit_tp = candle.h >= pos.take_profit if pos.side == "LONG" else candle.l <= pos.take_profit

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    exit_price = pos.stop_loss
                    exit_reason = "SL_and_TP_same_candle_assume_SL_first"
                elif hit_sl:
                    exit_price = pos.stop_loss
                    exit_reason = "SL"
                else:
                    exit_price = pos.take_profit
                    exit_reason = "TP"

                pnl = self._realized_pnl(pos, exit_price)
                state["last_trade"] = {
                    "pair": pair,
                    "tf": self.tf,
                    "side": pos.side,
                    "entry": pos.entry,
                    "exit": exit_price,
                    "qty": pos.qty,
                    "pnl": pnl,
                    "opened_t": pos.opened_t,
                    "closed_t": candle.t,
                    "reason": exit_reason,
                    "cluster_t": pos.cluster_t,
                }
                state.pop("pos", None)
                changed = True
                event = {"changed": True, "event": "position_closed", "reason": exit_reason, "pnl": pnl}
            else:
                mark = candle.c
                upnl = self._unrealized_pnl(pos, mark)
                await self.store.set_pnl(pair, self.tf, {"unrealized": upnl, "realized": float(state.get("realized", 0.0))})

        elif ord_raw:
            o = PendingOrder.from_dict(ord_raw)
            triggered = candle.h >= o.trigger if o.side == "LONG" else candle.l <= o.trigger
            if triggered:
                pos = Position(
                    side=o.side,
                    entry=o.trigger,
                    stop_loss=o.stop_loss,
                    take_profit=o.take_profit,
                    qty=o.qty,
                    opened_t=candle.t,
                    cluster_t=o.cluster_t,
                )
                state["pos"] = pos.to_dict()
                state.pop("ord", None)
                changed = True
                event = {"changed": True, "event": "position_opened"}

        state["last_t"] = int(candle.t)
        if changed:
            await self.store.set(pair, self.tf, state)
        return event

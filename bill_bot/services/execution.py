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
    def state_key(user_id: int, pair: str, tf: str) -> str:
        return f"state:{user_id}:{pair}:{tf}"

    @staticmethod
    def pnl_key(user_id: int, pair: str, tf: str) -> str:
        return f"pnl:{user_id}:{pair}:{tf}"

    @staticmethod
    def trades_key(user_id: int, pair: str, tf: str) -> str:
        return f"trades:{user_id}:{pair}:{tf}"

    async def get(self, user_id: int, pair: str, tf: str) -> dict:
        raw = await self.r.get(self.state_key(user_id, pair, tf))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    async def set(self, user_id: int, pair: str, tf: str, state: dict) -> None:
        await self.r.set(self.state_key(user_id, pair, tf), json.dumps(state, separators=(",", ":")))

    async def get_pnl(self, user_id: int, pair: str, tf: str) -> dict:
        raw = await self.r.get(self.pnl_key(user_id, pair, tf))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    async def set_pnl(self, user_id: int, pair: str, tf: str, payload: dict) -> None:
        await self.r.set(self.pnl_key(user_id, pair, tf), json.dumps(payload, separators=(",", ":")))

    async def append_trade(self, user_id: int, pair: str, tf: str, trade: dict, max_len: int = 50) -> None:
        key = self.trades_key(user_id, pair, tf)
        await self.r.lpush(key, json.dumps(trade, separators=(",", ":")))
        await self.r.ltrim(key, 0, int(max_len) - 1)

    async def get_trades(self, user_id: int, pair: str, tf: str, limit: int = 10) -> list[dict]:
        raw = await self.r.lrange(self.trades_key(user_id, pair, tf), 0, int(limit) - 1)
        out: list[dict] = []
        for x in raw:
            try:
                d = json.loads(x)
            except Exception:
                continue
            if isinstance(d, dict):
                out.append(d)
        return out


class ExecutionDryRun:
    def __init__(self, store: RedisTradeState, tf: str, virtual_equity: float):
        self.store = store
        self.tf = tf
        self.virtual_equity = float(virtual_equity)

    def _qty_from_risk(self, entry: float, sl: float, risk_pct: float) -> float:
        risk_amount = self.virtual_equity * (float(risk_pct) / 100.0)
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

    @staticmethod
    def _get_orders(state: dict) -> list[PendingOrder]:
        if "ords" in state and isinstance(state["ords"], list):
            out: list[PendingOrder] = []
            for x in state["ords"]:
                if isinstance(x, dict):
                    try:
                        out.append(PendingOrder.from_dict(x))
                    except Exception:
                        pass
            return out
        if "ord" in state and isinstance(state["ord"], dict):
            try:
                return [PendingOrder.from_dict(state["ord"])]
            except Exception:
                return []
        return []

    @staticmethod
    def _set_orders(state: dict, orders: list[PendingOrder]) -> None:
        state.pop("ord", None)
        if not orders:
            state.pop("ords", None)
            return
        state["ords"] = [o.to_dict() for o in orders]

    async def maybe_place_order(
        self,
        user_id: int,
        pair: str,
        candle: Candle,
        cand: SignalCandidate | None,
        risk_pct: float,
    ) -> tuple[str, PendingOrder | None, PendingOrder | None]:
        if cand is None:
            return "skipped", None, None
        state = await self.store.get(user_id, pair, self.tf)
        if state.get("pos"):
            return "skipped", None, None

        orders = self._get_orders(state)
        existing_same: PendingOrder | None = None
        for o in orders:
            if str(o.side).upper() == str(cand.side).upper():
                existing_same = o
                break
        if existing_same and int(existing_same.cluster_t) == int(cand.cluster_t):
            return "unchanged", existing_same, None

        qty = self._qty_from_risk(cand.entry_trigger, cand.stop_loss, risk_pct=risk_pct)
        if qty <= 0:
            return "skipped", None, None
        o = PendingOrder(
            side=cand.side,
            trigger=cand.entry_trigger,
            cluster_t=cand.cluster_t,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            qty=qty,
        )
        canceled: PendingOrder | None = None
        if existing_same:
            canceled = existing_same
            orders = [x for x in orders if str(x.side).upper() != str(cand.side).upper()]
            orders.append(o)
            self._set_orders(state, orders)
            action = "replaced"
        else:
            orders.append(o)
            self._set_orders(state, orders)
            action = "placed"
        state["risk_pct"] = float(risk_pct)
        state["last_t"] = int(candle.t)
        await self.store.set(user_id, pair, self.tf, state)
        return action, o, canceled

    async def on_candle(self, user_id: int, pair: str, candle: Candle) -> dict:
        state = await self.store.get(user_id, pair, self.tf)
        last_t = int(state.get("last_t", 0))
        if candle.t <= last_t:
            return {"changed": False}

        orders = self._get_orders(state)
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
                realized = float(state.get("realized", 0.0)) + float(pnl)
                state["realized"] = realized
                trade = {
                    "user_id": user_id,
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
                state["last_trade"] = trade
                state.pop("pos", None)
                changed = True
                event = {
                    "changed": True,
                    "event": "position_closed",
                    "pair": pair,
                    "tf": self.tf,
                    "side": pos.side,
                    "entry": pos.entry,
                    "exit": exit_price,
                    "qty": pos.qty,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "opened_t": pos.opened_t,
                    "closed_t": candle.t,
                    "reason": exit_reason,
                    "pnl": pnl,
                    "realized_total": realized,
                    "cluster_t": pos.cluster_t,
                }
                await self.store.append_trade(user_id, pair, self.tf, trade)
                await self.store.set_pnl(user_id, pair, self.tf, {"unrealized": 0.0, "realized": realized})
            else:
                mark = candle.c
                upnl = self._unrealized_pnl(pos, mark)
                await self.store.set_pnl(user_id, pair, self.tf, {"unrealized": upnl, "realized": float(state.get("realized", 0.0))})

        elif orders:
            triggered_order: PendingOrder | None = None
            for o in orders:
                side_u = str(o.side).upper()
                triggered = candle.h >= o.trigger if side_u == "LONG" else candle.l <= o.trigger
                if triggered:
                    triggered_order = o
                    break
            if triggered_order:
                pos = Position(
                    side=str(triggered_order.side).upper(),
                    entry=triggered_order.trigger,
                    stop_loss=triggered_order.stop_loss,
                    take_profit=triggered_order.take_profit,
                    qty=triggered_order.qty,
                    opened_t=candle.t,
                    cluster_t=triggered_order.cluster_t,
                )
                state["pos"] = pos.to_dict()
                self._set_orders(state, [])
                changed = True
                event = {
                    "changed": True,
                    "event": "position_opened",
                    "pair": pair,
                    "tf": self.tf,
                    "side": pos.side,
                    "entry": pos.entry,
                    "qty": pos.qty,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "opened_t": pos.opened_t,
                    "cluster_t": pos.cluster_t,
                }

        state["last_t"] = int(candle.t)
        if changed:
            await self.store.set(user_id, pair, self.tf, state)
        return event

import logging
from typing import Tuple, Optional

from bill_bot.services.candle_store import Candle
from bill_bot.services.strategy import SignalCandidate
from bill_bot.services.execution import RedisTradeState, PendingOrder, Position
from bill_bot.services.hyperliquid_api import HyperliquidExchangeClient, HyperliquidInfoClient

logger = logging.getLogger(__name__)

class ExecutionLiveRun:
    def __init__(self, store: RedisTradeState, tf: str, hl_client: HyperliquidExchangeClient, hl_info: HyperliquidInfoClient):
        self.store = store
        self.tf = tf
        self.hl_client = hl_client
        self.hl_info = hl_info

    def _get_orders(self, state: dict) -> list[PendingOrder]:
        if "ords" in state and isinstance(state["ords"], list):
            out = []
            for x in state["ords"]:
                if isinstance(x, dict):
                    try: out.append(PendingOrder.from_dict(x))
                    except Exception: pass
            return out
        if "ord" in state and isinstance(state["ord"], dict):
            try: return [PendingOrder.from_dict(state["ord"])]
            except Exception: return []
        return []

    def _set_orders(self, state: dict, orders: list[PendingOrder]) -> None:
        state.pop("ord", None)
        if not orders:
            state.pop("ords", None)
            return
        state["ords"] = [o.to_dict() for o in orders]

    async def _get_real_balance(self) -> float:
        total = 0.0
        try:
            st = await self.hl_info.user_state(self.hl_client.wallet)
            total += float(st.get("marginSummary", {}).get("accountValue", 0.0))
        except Exception as e:
            logger.error(f"LiveRun: Failed to get perps balance: {e}")
        try:
            spot_st = await self.hl_info.spot_user_state(self.hl_client.wallet)
            for b in spot_st.get("balances", []):
                if str(b.get("coin", "")).upper() in ("USDC", "USDT"):
                    total += float(b.get("total", 0.0))
        except Exception as e:
            logger.error(f"LiveRun: Failed to get spot balance: {e}")
        logger.info(f"LiveRun: Real balance (perps+spot) = {total:.2f}")
        return total

    async def maybe_place_order(
        self,
        user_id: int,
        pair: str,
        candle: Candle,
        cand: SignalCandidate | None,
        risk_pct: float,
        margin_pct: float = 100.0,
    ) -> Tuple[str, Optional[PendingOrder], Optional[PendingOrder]]:
        if cand is None:
            return "skipped", None, None
            
        state = await self.store.get(user_id, pair, self.tf)
        if state.get("pos"):
            return "skipped", None, None

        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
            positions = ustate.get("assetPositions", [])
            for p in positions:
                if str(p.get("position", {}).get("coin", "")).upper() == pair.upper():
                    sz = float(p.get("position", {}).get("szi", "0.0"))
                    if abs(sz) > 1e-9:
                        return "skipped", None, None
        except Exception as e:
            logger.error(f"LiveRun: failed to check exchange position for {pair}: {e}")
            return "skipped", None, None

        old_orders = self._get_orders(state)
        canceled_order = old_orders[0] if old_orders else None
        
        try:
            # ВАЖНО: используем hl_client.open_orders(), который видит триггерные ордера (Stop Entry)
            open_orders = await self.hl_client.open_orders()
            await self.hl_client.cancel_all_orders(pair, open_orders)
            logger.info(f"LiveRun: Canceled all exchange orders for {pair}")
        except Exception as e:
            logger.error(f"LiveRun: Failed to cancel open orders for {pair}: {e}")
        
        balance = await self._get_real_balance()
        if balance <= 0:
            logger.warning("LiveRun: Real balance is 0 or failed to load")
            return "skipped", None, None

        margin_amount = balance * (margin_pct / 100.0)
        risk_amount = margin_amount * (risk_pct / 100.0)
        dist = abs(cand.entry_trigger - cand.stop_loss)
        if dist <= 0:
            return "skipped", None, None

        sz = risk_amount / dist
        sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
        sz = round(sz, sz_decimals)

        if sz <= 0:
            logger.warning(f"LiveRun: sz=0 after rounding for {pair}")
            return "skipped", None, None

        is_buy = cand.side == "LONG"
        # Округляем цену триггера
        trigger_px = round(float(cand.entry_trigger), 6)
        order_type = {"trigger": {"isMarket": True, "triggerPx": trigger_px, "tpsl": "sl"}}
        
        try:
            logger.info(f"LiveRun: Placing Stop Entry on {pair}: buy={is_buy} sz={sz} px={trigger_px}")
            res = await self.hl_client.place_order(
                coin=pair,
                is_buy=is_buy,
                sz=sz,
                limit_px=trigger_px,
                order_type=order_type
            )
            logger.info(f"LiveRun: Place Order response: {res}")
            
            po = PendingOrder(
                side=cand.side,
                trigger=cand.entry_trigger,
                cluster_t=cand.cluster_t,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                qty=sz
            )
            self._set_orders(state, [po])
            await self.store.set(user_id, pair, self.tf, state)
            return "replaced" if canceled_order else "placed", po, canceled_order

        except Exception as e:
            logger.error(f"LiveRun: Place Order Failed for {pair}: {e}")
            return "skipped", None, None

    async def on_candle(self, user_id: int, pair: str, candle: Candle) -> dict:
        state = await self.store.get(user_id, pair, self.tf)
        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
        except Exception as e:
            logger.error(f"LiveRun on_candle API error: {e}")
            return {"changed": False}

        # В ответе assetPositions монета может быть "LTC"
        positions = ustate.get("assetPositions", [])
        real_sz = 0.0
        real_entry = 0.0
        for p in positions:
            pos_data = p.get("position", {})
            p_coin = str(pos_data.get("coin", "")).upper()
            if p_coin == pair.upper():
                real_sz = float(pos_data.get("szi", "0.0"))
                real_entry = float(pos_data.get("entryPx", "0.0"))
                break

        changed = False
        events = []
        local_pos = state.get("pos")
        local_ords = self._get_orders(state)

        if not local_pos and abs(real_sz) > 1e-9:
            po = local_ords[0] if local_ords else None
            if po:
                logger.info(f"LiveRun: Detected new position on {pair}, placing TP/SL.")
                try:
                    # Округляем цены SL/TP
                    tp_px = round(float(po.take_profit), 6)
                    sl_px = round(float(po.stop_loss), 6)
                    
                    tp_type = {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}}
                    sl_type = {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}}
                    is_buy_close = not (real_sz > 0) # Если real_sz > 0 (LONG), то закрываем продажей (is_buy=False)
                    
                    # Отменяем старые ордера (напр. Stop Entry) перед постановкой SL/TP
                    cur_orders = await self.hl_client.open_orders()
                    await self.hl_client.cancel_all_orders(pair, cur_orders)
                    
                    await self.hl_client.place_order(pair, is_buy_close, abs(real_sz), tp_px, tp_type, reduce_only=True)
                    await self.hl_client.place_order(pair, is_buy_close, abs(real_sz), sl_px, sl_type, reduce_only=True)
                    
                    state["pos"] = Position(
                        side="LONG" if real_sz > 0 else "SHORT", entry=real_entry, stop_loss=sl_px, take_profit=tp_px,
                        tp0=tp_px, tr1_done=False, tr2_done=False, tr_steps=0,
                        qty=abs(real_sz), opened_t=candle.t, cluster_t=po.cluster_t
                    ).to_dict()
                    self._set_orders(state, [])
                    changed = True
                    events.append({
                        "event": "position_opened", "pair": pair, "side": state["pos"]["side"], "entry": real_entry,
                        "stop_loss": sl_px, "take_profit": tp_px, "qty": abs(real_sz),
                        "opened_t": candle.t
                    })
                except Exception as e:
                    logger.error(f"LiveRun: Failed to place TP/SL on entry: {e}")

        elif local_pos and abs(real_sz) <= 1e-9:
            logger.info(f"LiveRun: Detected closed position on {pair}.")
            state.pop("pos", None)
            changed = True
            events.append({
                "event": "position_closed", "side": local_pos.get("side"), "entry": local_pos.get("entry"),
                "exit": candle.c, "stop_loss": local_pos.get("stop_loss"), "take_profit": local_pos.get("take_profit"),
                "qty": local_pos.get("qty"), "pnl": 0.0, "reason": "Closed on exchange", "closed_t": candle.t
            })

        elif local_pos and abs(real_sz) > 1e-9:
            pos_obj = Position.from_dict(local_pos)
            new_pos = self._apply_trailing(pos_obj, candle)
            
            if new_pos.stop_loss != pos_obj.stop_loss or new_pos.take_profit != pos_obj.take_profit:
                logger.info(f"LiveRun: Trailing update on {pair}. New SL: {new_pos.stop_loss}")
                try:
                    is_buy_close = not (new_pos.side == "LONG")
                    
                    open_orders = await self.hl_info.open_orders(self.hl_client.wallet)
                    await self.hl_client.cancel_all_orders(pair, open_orders)
                    
                    sl_type = {"trigger": {"isMarket": True, "triggerPx": float(new_pos.stop_loss), "tpsl": "sl"}}
                    tp_type = {"trigger": {"isMarket": True, "triggerPx": float(new_pos.take_profit), "tpsl": "tp"}}
                    await self.hl_client.place_order(pair, is_buy_close, abs(real_sz), float(new_pos.stop_loss), sl_type, reduce_only=True)
                    await self.hl_client.place_order(pair, is_buy_close, abs(real_sz), float(new_pos.take_profit), tp_type, reduce_only=True)
                except Exception as e:
                    logger.error(f"LiveRun: Trailing SL update failed: {e}")

                state["pos"] = new_pos.to_dict()
                changed = True

        if changed:
            await self.store.set(user_id, pair, self.tf, state)

        return {"changed": changed, "events": events}

    def _apply_trailing(self, pos: Position, candle: Candle) -> Position:
        side = str(pos.side).upper()
        dist = abs(float(pos.take_profit) - float(pos.entry))
        if dist <= 0:
            return pos
        max_steps_per_candle = 10

        if side == "LONG":
            h = float(candle.h)
            cur = pos
            if (not cur.tr1_done) and (h >= float(cur.entry) + 0.60 * dist):
                cur = Position(
                    side=cur.side, entry=cur.entry, stop_loss=max(float(cur.stop_loss), float(cur.entry)),
                    take_profit=cur.take_profit, tp0=cur.tp0, tr1_done=True, tr2_done=cur.tr2_done,
                    tr_steps=cur.tr_steps, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t
                )

            steps = 0
            while steps < max_steps_per_candle:
                dist2 = abs(float(cur.take_profit) - float(cur.entry))
                if dist2 <= 0 or h < float(cur.entry) + 0.95 * dist2: break
                new_sl = float(cur.entry) + 0.85 * dist2
                new_tp = float(cur.entry) + 1.50 * dist2
                cur = Position(
                    side=cur.side, entry=cur.entry, stop_loss=max(float(cur.stop_loss), new_sl),
                    take_profit=max(float(cur.take_profit), new_tp), tp0=cur.tp0, tr1_done=True, tr2_done=True,
                    tr_steps=int(cur.tr_steps) + 1, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t
                )
                steps += 1
            return cur

        l = float(candle.l)
        cur = pos
        if (not cur.tr1_done) and (l <= float(cur.entry) - 0.60 * dist):
            cur = Position(
                side=cur.side, entry=cur.entry, stop_loss=min(float(cur.stop_loss), float(cur.entry)),
                take_profit=cur.take_profit, tp0=cur.tp0, tr1_done=True, tr2_done=cur.tr2_done,
                tr_steps=cur.tr_steps, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t
            )

        steps = 0
        while steps < max_steps_per_candle:
            dist2 = abs(float(cur.take_profit) - float(cur.entry))
            if dist2 <= 0 or l > float(cur.entry) - 0.95 * dist2: break
            new_sl = float(cur.entry) - 0.85 * dist2
            new_tp = float(cur.entry) - 1.50 * dist2
            cur = Position(
                side=cur.side, entry=cur.entry, stop_loss=min(float(cur.stop_loss), new_sl),
                take_profit=min(float(cur.take_profit), new_tp), tp0=cur.tp0, tr1_done=True, tr2_done=True,
                tr_steps=int(cur.tr_steps) + 1, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t
            )
            steps += 1
        return cur

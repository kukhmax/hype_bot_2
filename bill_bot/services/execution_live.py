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
            # ВАЖНО: используем hl_info.open_orders(), который видит триггерные ордера (frontendOpenOrders)
            open_orders = await self.hl_info.open_orders(self.hl_client.wallet)
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
        # Регулярная проверка по завершению свечи
        return await self.sync_state(user_id, pair, float(candle.c), int(candle.t))

    async def sync_state(self, user_id: int, pair: str, cur_px: float | None, cur_t: int) -> dict:
        state = await self.store.get(user_id, pair, self.tf)
        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
        except Exception as e:
            logger.error(f"LiveRun sync_state API error for {pair}: {e}")
            return {"changed": False}

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

        # ПРИНЦИП: Биржа — единственный источник правды для позиций
        if abs(real_sz) > 1e-9:
            current_side = "LONG" if real_sz > 0 else "SHORT"
            
            # Если позиции в боте нет ИЛИ она другого направления/объема — синхронизируем
            needs_update = False
            if not local_pos:
                needs_update = True
            elif local_pos.get("side") != current_side:
                needs_update = True
            elif abs(float(local_pos.get("qty", 0)) - abs(real_sz)) > 1e-6:
                needs_update = True

            if needs_update:
                logger.info(f"LiveRun: Syncing POS for {pair}. Exchange side={current_side} sz={abs(real_sz)}")
                
                # Ищем параметры SL/TP (сначала из локального ордера, потом с биржи)
                po = local_ords[0] if local_ords else None
                sl_px = round(float(po.stop_loss), 6) if po else 0.0
                tp_px = round(float(po.take_profit), 6) if po else 0.0
                
                # Если локально нет цен, пробуем найти открытые триггерные ордера на бирже
                open_ords = await self.hl_info.open_orders(self.hl_client.wallet)
                if sl_px == 0 or tp_px == 0:
                    for eo in open_ords:
                        if str(eo.get("coin")).upper() == pair.upper() and eo.get("isTrigger"):
                            px = round(float(eo.get("triggerPx", 0)), 6)
                            if eo.get("tpsl") == "tp": tp_px = px
                            if eo.get("tpsl") == "sl": sl_px = px

                # Сохраняем новое состояние
                state["pos"] = Position(
                    side=current_side, entry=real_entry, stop_loss=sl_px, take_profit=tp_px,
                    tp0=tp_px, tr1_done=False, tr2_done=False, tr_steps=0,
                    qty=abs(real_sz), opened_t=cur_t, cluster_t=po.cluster_t if po else cur_t
                ).to_dict()
                self._set_orders(state, []) # Очищаем ордера
                changed = True
                
                # Если мы нашли параметры, но на бирже нет SL/TP — выставляем их
                if sl_px > 0 and tp_px > 0:
                    # Проверяем, есть ли уже эти ордера на бирже
                    has_sl = any(o.get("tpsl") == "sl" and str(o.get("coin")).upper() == pair.upper() for o in open_ords)
                    has_tp = any(o.get("tpsl") == "tp" and str(o.get("coin")).upper() == pair.upper() for o in open_ords)
                    
                    if not has_sl or not has_tp:
                        try:
                            # Перед выставлением новых SL/TP отменяем все старые по этой монете
                            await self.hl_client.cancel_all_orders(pair, open_ords)
                            
                            is_buy_close = (current_side == "SHORT")
                            tp_type = {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}}
                            sl_type = {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}}
                            
                            sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
                            final_sz = round(abs(real_sz), sz_decimals)
                            
                            logger.info(f"LiveRun: Placing missing TP/SL for {pair}: TP={tp_px} SL={sl_px}")
                            await self.hl_client.place_order(pair, is_buy_close, final_sz, tp_px, tp_type, reduce_only=True)
                            await self.hl_client.place_order(pair, is_buy_close, final_sz, sl_px, sl_type, reduce_only=True)
                        except Exception as e:
                            logger.error(f"LiveRun: Failed to place missing TP/SL on sync: {e}")

                events.append({
                    "event": "position_opened", "pair": pair, "side": current_side, "entry": real_entry,
                    "stop_loss": sl_px, "take_profit": tp_px, "qty": abs(real_sz), "opened_t": cur_t
                })

        elif local_pos and abs(real_sz) <= 1e-9:
            # На бирже позиции нет, а в боте есть — закрываем в боте
            logger.info(f"LiveRun: Exchange POS closed for {pair}. Syncing local state.")
            state.pop("pos", None)
            changed = True
            events.append({
                "event": "position_closed", "side": local_pos.get("side"), "entry": local_pos.get("entry"),
                "exit": cur_px if cur_px else local_pos.get("entry"), "reason": "Sync: Closed on exchange", "closed_t": cur_t,
                "qty": local_pos.get("qty"), "pnl": 0.0
            })

        if changed:
            await self.store.set(user_id, pair, self.tf, state)
        return {"changed": changed, "events": events}

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

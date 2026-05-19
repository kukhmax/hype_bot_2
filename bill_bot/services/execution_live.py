import logging
from typing import Tuple, Optional

from bill_bot.services.candle_store import Candle
from bill_bot.services.strategy import SignalCandidate
from bill_bot.services.execution import RedisTradeState, PendingOrder, Position
from bill_bot.services.hyperliquid_api import HyperliquidExchangeClient, HyperliquidInfoClient
from bill_bot.services.optimizer_agent import TradeOptimizerAgent

logger = logging.getLogger(__name__)

class ExecutionLiveRun:
    def __init__(
        self,
        store: RedisTradeState,
        tf: str,
        hl_client: HyperliquidExchangeClient,
        hl_info: HyperliquidInfoClient,
        fee_rate: float = 0.000456,
        optimizer: TradeOptimizerAgent | None = None
    ):
        self.store = store
        self.tf = tf
        self.hl_client = hl_client
        self.hl_info = hl_info
        self.fee_rate = fee_rate  # Taker fee rate (0.0456% default)
        self.optimizer = optimizer

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

    def _calc_fee(self, entry_px: float, exit_px: float, qty: float) -> float:
        """Рассчитывает суммарную комиссию за открытие и закрытие позиции"""
        fee_open = abs(entry_px * qty) * self.fee_rate
        fee_close = abs(exit_px * qty) * self.fee_rate
        return fee_open + fee_close

    async def _get_real_balance(self) -> float:
        """Возвращает доступный для торговли баланс (withdrawable) с биржи"""
        total = 0.0
        try:
            st = await self.hl_info.user_state(self.hl_client.wallet)
            # withdrawable = реальный доступный баланс ("Available to Trade" на бирже)
            total += float(st.get("withdrawable", 0.0))
        except Exception as e:
            logger.error(f"LiveRun: Failed to get perps balance: {e}")
        try:
            spot_st = await self.hl_info.spot_user_state(self.hl_client.wallet)
            for b in spot_st.get("balances", []):
                if str(b.get("coin", "")).upper() in ("USDC", "USDT"):
                    total += float(b.get("total", 0.0))
        except Exception as e:
            logger.error(f"LiveRun: Failed to get spot balance: {e}")
        logger.info(f"LiveRun: Available balance (withdrawable+spot) = {total:.2f}")
        return total

    def round_price(self, price: float) -> float:
        """Округление цены согласно правилам Hyperliquid (5 значимых цифр)"""
        if not price or price <= 0:
            return price
        import math
        try:
            # 5 значимых цифр
            precision = 5
            order = math.floor(math.log10(abs(price)))
            factor = 10 ** (precision - 1 - order)
            rounded = round(price * factor) / factor
            # Ограничиваем 6 знаками после запятой (лимит протокола)
            return round(rounded, 6)
        except Exception:
            return round(price, 6)

    async def maybe_place_order(
        self,
        user_id: int,
        pair: str,
        candle: Candle,
        cand: SignalCandidate | None,
        risk_pct: float,
        margin_pct: float = 100.0,
    ) -> dict:
        if cand is None:
            return {"changed": False}
            
        state = await self.store.get(user_id, pair, self.tf)
        if state.get("pos"):
            return {"changed": False}

        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
            positions = ustate.get("assetPositions", [])
            for p in positions:
                if str(p.get("position", {}).get("coin", "")).upper() == pair.upper():
                    sz = float(p.get("position", {}).get("szi", "0.0"))
                    if abs(sz) > 1e-9:
                        return {"changed": False}
        except Exception as e:
            logger.error(f"LiveRun: failed to check exchange position for {pair}: {e}")
            return {"changed": False}

        old_orders = self._get_orders(state)
        canceled_order = next((o for o in old_orders if o.side == cand.side), None)
        
        try:
            # ВАЖНО: используем hl_info.open_orders(), который видит триггерные ордера (frontendOpenOrders)
            open_orders = await self.hl_info.open_orders(self.hl_client.wallet)
            # Отменяем только ордера той же стороны (LONG -> B, SHORT -> S), 
            # чтобы позволить одновременное нахождение противоположных стоп-ордеров.
            side_to_cancel = "B" if cand.side == "LONG" else "S"
            
            logger.info(f"LiveRun: Found {len(open_orders)} open orders. Checking for {pair} {cand.side} (side {side_to_cancel})")
            
            orders_to_cancel = []
            for o in open_orders:
                o_coin = str(o.get("coin", "")).upper()
                o_side = str(o.get("side", ""))
                o_is_trigger = bool(o.get("isTrigger", False))
                o_reduce = bool(o.get("reduceOnly", False))
                o_oid = o.get("oid")
                
                # Логируем каждый ордер по этой паре для диагностики
                if o_coin == pair.upper():
                    logger.info(f"LiveRun: Debug Order: oid={o_oid} side={o_side} isTrigger={o_is_trigger} reduceOnly={o_reduce}")

                # Stop Entry - это триггерный ордер, который НЕ является reduceOnly
                if o_coin == pair.upper() and o_side == side_to_cancel and o_is_trigger and not o_reduce:
                    orders_to_cancel.append(o)
                elif o_coin == pair.upper() and o_side == side_to_cancel:
                    logger.info(f"LiveRun: Skipping same-side order {o_oid} (isTrigger={o_is_trigger}, reduceOnly={o_reduce})")

            if orders_to_cancel:
                logger.info(f" ✅ LiveRun: Canceling {len(orders_to_cancel)} existing {cand.side} stop orders for {pair}: {[o.get('oid') for o in orders_to_cancel]}")
                await self.hl_client.cancel_all_orders(pair, orders_to_cancel)
            else:
                logger.info(f"LiveRun: No matching orders to cancel for {pair} {cand.side}")
        except Exception as e:
            logger.error(f"LiveRun: Failed to cancel open orders for {pair}: {e}")
        
        balance = await self._get_real_balance()
        if balance <= 0:
            logger.warning("LiveRun: Real balance is 0 or failed to load")
            return {"changed": False}

        # Вызов оптимизатора
        setup_probability = 0.5
        trailing_strategy = "TRENDING"
        suggested_risk_pct = risk_pct

        if self.optimizer and cand:
            try:
                opt_res = await self.optimizer.predict_probability(
                    user_id=user_id,
                    pair=pair,
                    tf=self.tf,
                    side=cand.side,
                    default_risk_pct=risk_pct
                )
                setup_probability = opt_res["probability"]
                trailing_strategy = opt_res["trailing_strategy"]
                suggested_risk_pct = opt_res["suggested_risk_pct"]
                logger.info(
                    f" 🧠  OptimizerAgent: Setup scored {setup_probability:.2f} ({opt_res['source']}), "
                    f"strategy={trailing_strategy}, risk={suggested_risk_pct:.2f}%"
                )
            except Exception as e:
                logger.error(f"OptimizerAgent: Error predicting probability for {pair}: {e}")

        margin_amount = balance * (margin_pct / 100.0)
        risk_amount = margin_amount * (suggested_risk_pct / 100.0)
        dist = abs(cand.entry_trigger - cand.stop_loss)
        if dist <= 0:
            return {"changed": False}

        sz = risk_amount / dist
        sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
        sz = round(sz, sz_decimals)

        if sz <= 0:
            logger.warning(f"LiveRun: sz=0 after rounding for {pair}")
            return {"changed": False}

        is_buy = cand.side == "LONG"
        # Округляем цену триггера
        trigger_px = self.round_price(float(cand.entry_trigger))
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
                qty=sz,
                rr=cand.rr,
                setup_probability=setup_probability,
                trailing_strategy=trailing_strategy,
                suggested_risk_pct=suggested_risk_pct
            )
            # Сохраняем, оставляя ордера другой стороны
            new_ords = [o for o in old_orders if o.side != po.side]
            new_ords.append(po)
            self._set_orders(state, new_ords)
            await self.store.set(user_id, pair, self.tf, state)
            
            evt_type = "order_replaced" if canceled_order else "order_placed"
            return {
                "changed": True,
                "events": [{
                    "event": evt_type,
                    "pair": pair,
                    "tf": self.tf,
                    "side": po.side,
                    "trigger": po.trigger,
                    "stop_loss": po.stop_loss,
                    "take_profit": po.take_profit,
                    "qty": po.qty
                }]
            }

        except Exception as e:
            logger.error(f" ✅ LiveRun: Place Order Failed for {pair}: {e}")
            return {"changed": False}

    async def on_candle(self, user_id: int, pair: str, candle: Candle) -> dict:
        # 1. Сначала синхронизируем состояние с биржей (Master Sync)
        sync_res = await self.sync_state(user_id, pair, float(candle.c), int(candle.t))
        
        # 2. Если позиция уже есть и она стабильна, проверяем трейлинг
        state = await self.store.get(user_id, pair, self.tf)
        pos_data = state.get("pos")
        if pos_data:
            pos = Position.from_dict(pos_data)
            new_pos = self._apply_trailing(pos, candle)
            
            # Если уровни изменились — обновляем на бирже
            if abs(new_pos.stop_loss - pos.stop_loss) > 1e-9 or abs(new_pos.take_profit - pos.take_profit) > 1e-9:
                logger.info(f" ✅  LiveRun: Trailing update for {pair}: SL {pos.stop_loss}->{new_pos.stop_loss} TP {pos.take_profit}->{new_pos.take_profit}")
                try:
                    open_ords = await self.hl_info.open_orders(self.hl_client.wallet)
                    # По просьбе пользователя: при трейлинге отменяем ВООБЩЕ ВСЕ ордера по этой паре
                    to_cancel = [o for o in open_ords if str(o.get("coin")).upper() == pair.upper()]
                    if to_cancel:
                        await self.hl_client.cancel_all_orders(pair, to_cancel)
                        logger.info(f"  ✅LiveRun: Canceled ALL {len(to_cancel)} orders for {pair} during trailing update")
                    
                    is_buy_close = (new_pos.side == "SHORT")
                    tp_px = self.round_price(new_pos.take_profit)
                    sl_px = self.round_price(new_pos.stop_loss)
                    tp_type = {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}}
                    sl_type = {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}}
                    
                    sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
                    final_sz = round(new_pos.qty, sz_decimals)
                    
                    logger.info(f"LiveRun: Placing trailing TP for {pair}: {tp_px} (sz={final_sz})")
                    try:
                        res_tp = await self.hl_client.place_order(pair, is_buy_close, final_sz, tp_px, tp_type, reduce_only=True)
                        logger.info(f"LiveRun: Trailing TP response: {res_tp}")
                    except Exception as e:
                        logger.error(f"LiveRun: Failed to place trailing TP for {pair}: {e}")

                    logger.info(f"LiveRun: Placing trailing SL for {pair}: {sl_px} (sz={final_sz})")
                    try:
                        res_sl = await self.hl_client.place_order(pair, is_buy_close, final_sz, sl_px, sl_type, reduce_only=True)
                        logger.info(f"LiveRun: Trailing SL response: {res_sl}")
                    except Exception as e:
                        logger.error(f"LiveRun: Failed to place trailing SL for {pair}: {e}")
                    
                    state["pos"] = new_pos.to_dict()
                    await self.store.set(user_id, pair, self.tf, state)
                    
                    # Генерируем событие обновления (для графика в ТГ)
                    upd_evt = {
                        "event": "position_updated",
                        "pair": pair, "side": new_pos.side, "entry": new_pos.entry,
                        "stop_loss": new_pos.stop_loss, "take_profit": new_pos.take_profit,
                        "qty": new_pos.qty
                    }
                    if "events" not in sync_res: sync_res["events"] = []
                    sync_res["events"].append(upd_evt)
                    sync_res["changed"] = True
                except Exception as e:
                    logger.error(f" ❌ LiveRun: Trailing update critical failure for {pair}: {e}")
                    
        return sync_res

    async def sync_state(self, user_id: int, pair: str, cur_px: float | None, cur_t: int) -> dict:
        state = await self.store.get(user_id, pair, self.tf)
        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
        except Exception as e:
            logger.error(f" ❌ LiveRun sync_state API error for {pair}: {e}")
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

            if not needs_update:
                # Позиция уже есть локально, просто обновляем нереализованный PnL
                unrealized = 0.0
                if cur_px:
                    if current_side == "LONG":
                        unrealized = (cur_px - real_entry) * abs(real_sz)
                    else:
                        unrealized = (real_entry - cur_px) * abs(real_sz)
                await self.store.set_pnl(user_id, pair, self.tf, {
                    "unrealized": unrealized,
                    "realized": float(state.get("realized", 0.0))
                })
            else:
                logger.info(f"✅ LiveRun: Syncing POS for {pair}. Exchange side={current_side} sz={abs(real_sz)}")
                
                # Ищем параметры SL/TP (сначала из локального ордера той же стороны, потом с биржи)
                po = next((o for o in local_ords if o.side == current_side), None)
                
                sl_px = self.round_price(float(po.stop_loss)) if po else 0.0
                tp_px = self.round_price(float(po.take_profit)) if po else 0.0
                
                # Если мы только что обнаружили открытие позиции (нет local_pos), 
                # пересчитываем Тейк-Профит от РЕАЛЬНОЙ точки входа, чтобы сохранить RR.
                if not local_pos and po and real_entry > 0:
                    rr = float(po.rr)
                    dist_sl = abs(real_entry - sl_px)
                    if current_side == "LONG":
                        tp_px = self.round_price(real_entry + rr * dist_sl)
                    else:
                        tp_px = self.round_price(real_entry - rr * dist_sl)
                    logger.info(f" ✅ LiveRun: Recalculated TP for new position {pair} based on REAL entry {real_entry}: TP={tp_px} (RR={rr})")

                # Если локально нет цен, пробуем найти открытые триггерные ордера на бирже
                open_ords = await self.hl_info.open_orders(self.hl_client.wallet)
                if sl_px == 0 or tp_px == 0:
                    for eo in open_ords:
                        if str(eo.get("coin")).upper() == pair.upper() and eo.get("isTrigger"):
                            px = self.round_price(float(eo.get("triggerPx", 0)))
                            if eo.get("tpsl") == "tp": tp_px = px
                            if eo.get("tpsl") == "sl": sl_px = px

                # Сохраняем новое состояние
                state["pos"] = Position(
                    side=current_side, entry=real_entry, stop_loss=sl_px, take_profit=tp_px,
                    tp0=tp_px, tr1_done=False, tr2_done=False, tr_steps=0,
                    qty=abs(real_sz), opened_t=cur_t, cluster_t=po.cluster_t if po else cur_t,
                    rr=po.rr if po else 1.5,
                    setup_probability=po.setup_probability if po else 0.5,
                    trailing_strategy=po.trailing_strategy if po else "TRENDING",
                    suggested_risk_pct=po.suggested_risk_pct if po else None
                ).to_dict()
                self._set_orders(state, []) # Очищаем ордера
                changed = True
                # Если мы нашли параметры, но на бирже нет SL/TP — выставляем их
                if sl_px > 0 and tp_px > 0:
                    has_sl = any(o.get("tpsl") == "sl" and str(o.get("coin")).upper() == pair.upper() for o in open_ords)
                    has_tp = any(o.get("tpsl") == "tp" and str(o.get("coin")).upper() == pair.upper() for o in open_ords)
                    logger.info(f"LiveRun: {pair} check: has_sl={has_sl}, has_tp={has_tp}, sl_px={sl_px}, tp_px={tp_px}")
                    if not has_sl or not has_tp:
                        try:
                            # По просьбе пользователя: при открытии позиции/синхронизации отменяем ВООБЩЕ ВСЕ ордера по этой паре
                            to_cancel = [o for o in open_ords if str(o.get("coin")).upper() == pair.upper()]
                            if to_cancel:
                                await self.hl_client.cancel_all_orders(pair, to_cancel)
                                logger.info(f"LiveRun: Canceled ALL {len(to_cancel)} old orders on sync for {pair}")
                            
                            is_buy_close = (current_side == "SHORT")
                            tp_type = {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}}
                            sl_type = {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}}
                            
                            sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
                            final_sz = round(abs(real_sz), sz_decimals)
                            
                            logger.info(f"LiveRun: Sync-placing TP for {pair}: {tp_px} (sz={final_sz})")
                            try:
                                res_tp = await self.hl_client.place_order(pair, is_buy_close, final_sz, tp_px, tp_type, reduce_only=True)
                                logger.info(f"LiveRun: Sync TP response: {res_tp}")
                            except Exception as e:
                                logger.error(f"LiveRun: Failed to place sync TP for {pair}: {e}")

                            logger.info(f"LiveRun: Sync-placing SL for {pair}: {sl_px} (sz={final_sz})")
                            try:
                                res_sl = await self.hl_client.place_order(pair, is_buy_close, final_sz, sl_px, sl_type, reduce_only=True)
                                logger.info(f"LiveRun: Sync SL response: {res_sl}")
                            except Exception as e:
                                logger.error(f"LiveRun: Failed to place sync SL for {pair}: {e}")
                        except Exception as e:
                            logger.error(f" ❌ LiveRun: Sync TP/SL critical failure for {pair}: {e}")

                # Расчет нереализованного PnL
                unrealized = 0.0
                if cur_px:
                    if current_side == "LONG":
                        unrealized = (cur_px - real_entry) * abs(real_sz)
                    else:
                        unrealized = (real_entry - cur_px) * abs(real_sz)
                
                await self.store.set_pnl(user_id, pair, self.tf, {
                    "unrealized": unrealized,
                    "realized": float(state.get("realized", 0.0))
                })

                events.append({
                    "event": "position_opened", "pair": pair, "side": current_side, "entry": real_entry,
                    "stop_loss": sl_px, "take_profit": tp_px, "qty": abs(real_sz), "opened_t": cur_t,
                    "unrealized": unrealized
                })

        elif local_pos and abs(real_sz) <= 1e-9:
            # На бирже позиции нет, а в боте есть — закрываем в боте
            logger.info(f"LiveRun: Exchange POS closed for {pair}. Syncing local state.")
            
            entry = float(local_pos.get("entry", 0.0))
            exit_px = float(cur_px) if cur_px is not None else entry
            qty = float(local_pos.get("qty", 0.0))
            side = str(local_pos.get("side", "")).upper()
            opened_t = int(local_pos.get("opened_t", cur_t))
            
            # Получаем реальные данные с биржи
            real_details = await self.hl_info.get_real_trade_details(
                user_address=self.hl_client.wallet,
                coin=pair,
                opened_t=opened_t,
                side=side,
                default_entry=entry,
                default_exit=exit_px,
                default_qty=qty,
                taker_fee_rate=self.fee_rate
            )
            
            entry = real_details["entry"]
            exit_px = real_details["exit"]
            qty = real_details["qty"]
            fee = real_details["fee"]
            pnl = real_details["pnl"]
            gross_pnl = real_details["gross_pnl"]
            
            # Получаем реальный баланс после закрытия
            balance_after = await self._get_real_balance()
            
            realized = float(state.get("realized", 0.0)) + float(pnl)
            state["realized"] = realized
            trade = {
                "user_id": user_id,
                "pair": pair,
                "tf": self.tf,
                "side": side,
                "entry": entry,
                "exit": exit_px,
                "qty": qty,
                "pnl": pnl,
                "gross_pnl": gross_pnl,
                "fee": fee,
                "balance_after": balance_after,
                "opened_t": local_pos.get("opened_t", cur_t),
                "closed_t": cur_t,
                "reason": "Sync: Closed on exchange",
                "cluster_t": local_pos.get("cluster_t", cur_t),
            }
            state["last_trade"] = trade
            state.pop("pos", None)
            changed = True
            events.append({
                "event": "position_closed", 
                "pair": pair,
                "side": side, 
                "entry": entry,
                "exit": exit_px, 
                "stop_loss": local_pos.get("stop_loss"),
                "take_profit": local_pos.get("take_profit"),
                "reason": "Sync: Closed on exchange", 
                "closed_t": cur_t,
                "qty": qty, 
                "pnl": pnl,
                "gross_pnl": gross_pnl,
                "fee": fee,
                "balance_after": balance_after,
                "realized_total": realized
            })
            await self.store.append_trade(user_id, pair, self.tf, trade)
            await self.store.set_pnl(user_id, pair, self.tf, {"unrealized": 0.0, "realized": realized})

        if changed:
            await self.store.set(user_id, pair, self.tf, state)
        return {"changed": changed, "events": events}

    async def cancel_order(self, user_id: int, pair: str, side: str) -> bool:
        """Отмена конкретного стоп-ордера на бирже и в локальном стейте"""
        state = await self.store.get(user_id, pair, self.tf)
        orders = self._get_orders(state)
        side_u = side.upper()
        
        # 1. Отмена на бирже
        try:
            open_orders = await self.hl_info.open_orders(self.hl_client.wallet)
            hl_side = "B" if side_u == "LONG" else "S"
            to_cancel = [o for o in open_orders if str(o.get("coin")).upper() == pair.upper() and o.get("side") == hl_side and o.get("isTrigger") and not o.get("reduceOnly")]
            if to_cancel:
                await self.hl_client.cancel_all_orders(pair, to_cancel)
                logger.info(f"LiveRun: Manually canceled {len(to_cancel)} {side_u} orders for {pair}")
        except Exception as e:
            logger.error(f"LiveRun: Failed to cancel orders on exchange for {pair}: {e}")
            return False

        # 2. Удаление из локального стейта
        new_ords = [o for o in orders if o.side.upper() != side_u]
        if len(new_ords) != len(orders):
            self._set_orders(state, new_ords)
            await self.store.set(user_id, pair, self.tf, state)
            return True
        return False

    async def close_position(self, user_id: int, pair: str) -> bool:
        """Рыночное закрытие позиции на бирже"""
        try:
            ustate = await self.hl_info.user_state(self.hl_client.wallet)
            positions = ustate.get("assetPositions", [])
            real_sz = 0.0
            for p in positions:
                pos_data = p.get("position", {})
                if str(pos_data.get("coin", "")).upper() == pair.upper():
                    real_sz = float(pos_data.get("szi", "0.0"))
                    break
            
            if abs(real_sz) < 1e-9:
                return False

            # Закрываем рыночным ордером
            is_buy_close = (real_sz < 0)
            sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
            final_sz = abs(round(real_sz, sz_decimals))
            
            # Получаем текущую цену для лимитного ордера (Hyperliquid требует цену даже для Market-like ордеров в некоторых случаях, или используем специальный тип)
            px_data = await self.hl_info.all_mids()
            cur_px = float(px_data.get(pair, 0))
            if cur_px <= 0: return False
            
            # Для надежности используем цену с запасом (slippage)
            slippage = 0.01 # 1%
            limit_px = self.round_price(cur_px * (1 + slippage if is_buy_close else 1 - slippage))
            
            logger.info(f"LiveRun: Manually closing {pair} position: sz={final_sz} buy={is_buy_close}")
            res = await self.hl_client.place_order(pair, is_buy_close, final_sz, limit_px, {"limit": {"tif": "Ioc"}}, reduce_only=True)
            logger.info(f"LiveRun: Manual close response: {res}")
            
            # Отменяем все оставшиеся ордера (SL/TP)
            open_ords = await self.hl_info.open_orders(self.hl_client.wallet)
            to_cancel = [o for o in open_ords if str(o.get("coin")).upper() == pair.upper()]
            if to_cancel:
                await self.hl_client.cancel_all_orders(pair, to_cancel)

            # Синхронизация стейта произойдет при следующем on_candle или вручную
            return True
        except Exception as e:
            logger.error(f"LiveRun: Manual close failed for {pair}: {e}")
            return False

    async def update_tpsl(self, user_id: int, pair: str, new_tp: float | None = None, new_sl: float | None = None) -> bool:
        """Обновление уровней TP/SL на бирже и в локальном стейте"""
        state = await self.store.get(user_id, pair, self.tf)
        pos_data = state.get("pos")
        if not pos_data:
            return False
        
        pos = Position.from_dict(pos_data)
        tp = new_tp if new_tp is not None else pos.take_profit
        sl = new_sl if new_sl is not None else pos.stop_loss
        
        try:
            # 1. Отменяем старые SL/TP на бирже
            open_ords = await self.hl_info.open_orders(self.hl_client.wallet)
            to_cancel = [o for o in open_ords if str(o.get("coin")).upper() == pair.upper() and o.get("isTrigger") and o.get("reduceOnly")]
            if to_cancel:
                await self.hl_client.cancel_all_orders(pair, to_cancel)
            
            # 2. Выставляем новые
            is_buy_close = (pos.side == "SHORT")
            sz_decimals = await self.hl_info.get_sz_decimals(pair) or 0
            final_sz = round(pos.qty, sz_decimals)
            
            tp_px = self.round_price(tp)
            sl_px = self.round_price(sl)
            
            if tp > 0:
                tp_type = {"trigger": {"isMarket": True, "triggerPx": tp_px, "tpsl": "tp"}}
                await self.hl_client.place_order(pair, is_buy_close, final_sz, tp_px, tp_type, reduce_only=True)
            
            if sl > 0:
                sl_type = {"trigger": {"isMarket": True, "triggerPx": sl_px, "tpsl": "sl"}}
                await self.hl_client.place_order(pair, is_buy_close, final_sz, sl_px, sl_type, reduce_only=True)
            
            # 3. Обновляем локально
            new_pos = Position(
                side=pos.side, entry=pos.entry, stop_loss=sl, take_profit=tp,
                tp0=pos.tp0, tr1_done=pos.tr1_done, tr2_done=pos.tr2_done,
                tr_steps=pos.tr_steps, qty=pos.qty, opened_t=pos.opened_t, cluster_t=pos.cluster_t, rr=pos.rr,
                setup_probability=pos.setup_probability, trailing_strategy=pos.trailing_strategy,
                suggested_risk_pct=pos.suggested_risk_pct
            )
            state["pos"] = new_pos.to_dict()
            await self.store.set(user_id, pair, self.tf, state)
            return True
        except Exception as e:
            logger.error(f"LiveRun: Failed to update TP/SL for {pair}: {e}")
            return False

    def _apply_trailing(self, pos: Position, candle: Candle) -> Position:
        strategy = str(pos.trailing_strategy).upper()
        if strategy == "FIXED":
            return pos

        side = str(pos.side).upper()
        dist = abs(float(pos.take_profit) - float(pos.entry))
        if dist <= 0:
            return pos

        # Параметры стратегии трейлинга
        if strategy == "SCALPING":
            tr1_mult = 0.40
            tr2_mult = 0.70
            new_sl_mult = 0.60
            new_tp_mult = 1.40
        else:  # TRENDING (default)
            tr1_mult = 0.70
            tr2_mult = 0.90
            new_sl_mult = 0.80
            new_tp_mult = 1.80

        max_steps_per_candle = 10

        if side == "LONG":
            h = float(candle.h)
            cur = pos
            if (not cur.tr1_done) and (h >= float(cur.entry) + tr1_mult * dist):
                cur = Position(
                    side=cur.side, entry=cur.entry, stop_loss=max(float(cur.stop_loss), float(cur.entry)),
                    take_profit=cur.take_profit, tp0=cur.tp0, tr1_done=True, tr2_done=cur.tr2_done,
                    tr_steps=cur.tr_steps, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t,
                    rr=cur.rr, setup_probability=cur.setup_probability, trailing_strategy=cur.trailing_strategy,
                    suggested_risk_pct=cur.suggested_risk_pct
                )

            steps = 0
            while steps < max_steps_per_candle:
                dist2 = abs(float(cur.take_profit) - float(cur.entry))
                if dist2 <= 0 or h < float(cur.entry) + tr2_mult * dist2:
                    break
                new_sl = float(cur.entry) + new_sl_mult * dist2
                new_tp = float(cur.entry) + new_tp_mult * dist2
                cur = Position(
                    side=cur.side, entry=cur.entry, stop_loss=max(float(cur.stop_loss), new_sl),
                    take_profit=max(float(cur.take_profit), new_tp), tp0=cur.tp0, tr1_done=True, tr2_done=True,
                    tr_steps=int(cur.tr_steps) + 1, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t,
                    rr=cur.rr, setup_probability=cur.setup_probability, trailing_strategy=cur.trailing_strategy,
                    suggested_risk_pct=cur.suggested_risk_pct
                )
                steps += 1
            return cur

        # SHORT side
        l = float(candle.l)
        cur = pos
        if (not cur.tr1_done) and (l <= float(cur.entry) - tr1_mult * dist):
            cur = Position(
                side=cur.side, entry=cur.entry, stop_loss=min(float(cur.stop_loss), float(cur.entry)),
                take_profit=cur.take_profit, tp0=cur.tp0, tr1_done=True, tr2_done=cur.tr2_done,
                tr_steps=cur.tr_steps, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t,
                rr=cur.rr, setup_probability=cur.setup_probability, trailing_strategy=cur.trailing_strategy,
                suggested_risk_pct=cur.suggested_risk_pct
            )

        steps = 0
        while steps < max_steps_per_candle:
            dist2 = abs(float(cur.take_profit) - float(cur.entry))
            if dist2 <= 0 or l > float(cur.entry) - tr2_mult * dist2:
                break
            new_sl = float(cur.entry) - new_sl_mult * dist2
            new_tp = float(cur.entry) - new_tp_mult * dist2
            cur = Position(
                side=cur.side, entry=cur.entry, stop_loss=min(float(cur.stop_loss), new_sl),
                take_profit=min(float(cur.take_profit), new_tp), tp0=cur.tp0, tr1_done=True, tr2_done=True,
                tr_steps=int(cur.tr_steps) + 1, qty=cur.qty, opened_t=cur.opened_t, cluster_t=cur.cluster_t,
                rr=cur.rr, setup_probability=cur.setup_probability, trailing_strategy=cur.trailing_strategy,
                suggested_risk_pct=cur.suggested_risk_pct
            )
            steps += 1
        return cur

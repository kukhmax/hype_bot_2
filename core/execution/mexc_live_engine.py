import asyncio
from typing import Dict, Any, Optional

from core.logger import setup_logger
from core.execution.live_engine import LiveEngine
from core.data.trade_logger import TradeLogger

logger = setup_logger("mexc_live_engine")

class MEXCLiveEngine(LiveEngine):
    """
    Специализированный движок для Live-торговли на бирже MEXC.
    - Открывает сделки рыночным ордером на 30% от депозита (с учетом плеча).
    - Выставляет физический LIMIT ордер для Take-Profit и ведет виртуальный Stop-Loss 
      (или пытается использовать OCO, если поддерживается).
    - Реализует трейлинг-стоп и тейк-профит с обновлением ордеров на бирже.
    - Поддерживает режим SIGNALS для ручного входа по кнопке в ТГ.
    """

    def __init__(self, symbol: str, timeframe_minutes: int, paper_trading: bool = False, leverage: int = 1,
                 tg_callback=None, tg_delete_callback=None, ws_client=None, strategy_params=None, max_daily_loss_percent=5.0, enable_gemini=True, tg_position_callback=None):
        super().__init__(symbol, timeframe_minutes, paper_trading, leverage,
                         tg_callback, tg_delete_callback, ws_client, strategy_params, max_daily_loss_percent, enable_gemini, tg_position_callback)
        self.executor = MEXCExecutor()
        self.pending_signal = None  # Для режима SIGNALS

    async def _execute_signal(self, signal: dict, current_price: float):
        """Отправка реального сигнала на исполнение на MEXC."""
        side = signal["signal"]
        stop_loss = signal["stop_loss"]
        take_profit = signal.get("take_profit", None)
        
        current_balance = await self.executor.get_balance(self.quote_asset)
        logger.info(f"[{self.symbol}] Обработка сигнала {side}. Текущий баланс: {current_balance} {self.quote_asset}. Плечо: {self.leverage}x")

        # Режим SIGNALS: Сохраняем сигнал и просим подтверждения
        from bot.settings import bot_settings
        bot_mode = bot_settings.get("mode", "paper").lower()

        if bot_mode == "signals":
            self.pending_signal = dict(signal)
            
            msg = (
                f"🔔 **СИГНАЛ: {side}** | `{self.symbol}`\n"
                f"Цена: `{current_price:.4f}`\n"
                f"SL: `{stop_loss:.4f}` | TP: `{take_profit:.4f}`\n\n"
                f"👇 _Нажмите кнопку ниже для реального входа на MEXC_"
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚡ Открыть позицию MEXC", callback_data=f"open_mexc_{self.symbol}")]
            ])
            
            bot = bot_settings.get("bot_instance")
            chat_id = bot_settings.get("chat_id")
            if bot and chat_id:
                try:
                    await bot.send_message(
                        chat_id=chat_id, 
                        text=msg, 
                        parse_mode="Markdown", 
                        reply_markup=kb
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки сигнала с кнопкой: {e}")
            return
            
        # LIVE режим начинается здесь:
        await self._do_execute_live(signal, current_price, current_balance)

    async def execute_pending_signal(self):
        """Вызывается по кнопке из Telegram в режиме SIGNALS."""
        if not self.pending_signal:
            raise ValueError("Нет сохраненного сигнала для этой пары.")
            
        if self.current_position:
            raise ValueError("Позиция уже открыта!")
            
        logger.info(f"Ручное исполнение сигнала для {self.symbol}...")
        signal_data = self.pending_signal
        
        try:
            current_price = self.get_current_price()
            current_balance = await self.executor.get_balance(self.quote_asset)
        except Exception as e:
            logger.error(f"Ошибка получения цены/баланса при ручном входе: {e}")
            return
            
        self.pending_signal = None  # Очищаем
        await self._do_execute_live(signal_data, current_price, current_balance)
        
    async def _do_execute_live(self, signal: dict, current_price: float, current_balance: float):
        """Фактическое исполнение на MEXC Futures."""
        side = signal["signal"]
        stop_loss = signal["stop_loss"]
        take_profit = signal.get("take_profit", None)
        
        # Требование: Открывать каждую сделку в 30% от депозита (с учетом плеча)
        # Игнорируем обычный risk_manager для расчета размера позиции по MEXC
        margin = current_balance * 0.3
        quote_qty = margin * self.leverage
        
        # Минимальный размер ордера на MEXC обычно 5 USDT
        if quote_qty < 5.0:
            logger.warning(f"[{self.symbol}] Размер позиции ({quote_qty:.2f} {self.quote_asset}) меньше минимального (5.0). Отмена.")
            await self._notify(f"🚫 **ОШИБКА ИСПОЛНЕНИЯ**\nРазмер позиции {quote_qty:.2f} меньше минимального 5.0 {self.quote_asset}.")
            return

        # Округление volume под фьючерсы (кол-во контрактов - для MEXC v1 Futures vol - это base_qty, обычно целое количество)
        base_qty = round(base_qty, 3)
        
        # Вход рыночным ордером
        result = await self.executor.place_market_order(self.symbol, side, base_qty)
        
        if result and result.get("code") == 0:
            order_id = result.get("data", {}).get("orderId", "unknown")
            logger.info(f"[{self.symbol}] Успешно открыта позиция {side}. Результат: {result}")
            
            # Сохраняем стейт позиции
            self.current_position = {
                "orderId": order_id,
                "side": side,
                "entry_price": current_price,
                "qty": base_qty,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "leverage": self.leverage,
                "initial_sl": stop_loss
            }
            
            # Выставляем LIMIT TP (SL ведется виртуально в _check_stops из-за особенностей MEXC Спот API с балансами)
            if take_profit:
                close_side = "SELL" if side == "BUY" else "BUY"
                tp_res = await self.executor.place_limit_order(self.symbol, close_side, base_qty, take_profit)
                if tp_res and tp_res.get("code") == 0:
                     order_id = tp_res.get("data", {}).get("orderId")
                     self.current_position["tp_order_id"] = order_id
                     logger.info(f"[{self.symbol}] Выставлен LIMIT TP ордер: {order_id}")
            
            free_balance = current_balance - margin
            sl_usdt = abs(current_price - stop_loss) * base_qty
            tp_usdt = abs(take_profit - current_price) * base_qty if take_profit else 0
            
            await self._notify_position(
                f"📈 *ПОЗИЦИЯ ОТКРЫТА [LIVE MEXC]*\n"
                f"Пара: `{self.symbol}`\n"
                f"Направление: `{'🟢LONG🟢' if side == 'BUY' else '🔴SHORT🔴'}`\n"
                f"Своб. баланс: `{free_balance:.2f} {self.quote_asset}`\n"
                f"Маржа (30%): `{margin:.2f} {self.quote_asset}` (Плечо {self.leverage}x)\n"
                f"Объем: `{quote_qty:.2f} {self.quote_asset}`\n"
                f"Вход: `{current_price:.10f}`\n"
                f"SL: `{stop_loss:.10f}` (`-{sl_usdt:.2f} {self.quote_asset}`) [Виртуальный]\n"
                f"TP: `{take_profit:.10f}` (`+{tp_usdt:.2f} {self.quote_asset}`) [Биржевой]",
                self.symbol
            )
        else:
            logger.error(f"[{self.symbol}] Ошибка открытия MARKET ордера: {result}")
            await self._notify(f"🚫 **ОШИБКА ИСПОЛНЕНИЯ MEXC**\nНе удалось открыть сделку:\n`{result}`")

    async def _check_stops(self, candle: dict):
        """Проверка закрытия сделки по SL (виртуально) / TP (реально, но проверяем факт достижения) 
           и логика трейлинг стопа/тейка с обновлением реальных ордеров на MEXC."""
        pos = self.current_position
        if not pos:
            return
            
        closed = False
        pnl = 0.0
        close_reason = ""
        close_price = 0.0
        
        try:
            current_price = self.get_current_price()
        except Exception as e:
            logger.error(f"[{self.symbol}] Ошибка получения текущей цены: {e}")
            current_price = 0.0
            
        if not current_price:
            return
            
        if "initial_sl" not in pos:
            pos["initial_sl"] = pos["stop_loss"]
            
        r_dist = abs(pos["entry_price"] - pos["initial_sl"])
        one_tenth_tp = abs((pos["take_profit"] - pos["entry_price"]) / 10) if pos.get("take_profit") else 0
        
        tp_changed = False
        
        if pos["side"] == "BUY":
            # Trailing логика LONG
            if r_dist > 0 and candle["high"] >= pos["entry_price"] + r_dist:
                if pos["stop_loss"] < pos["entry_price"]:
                    pos["stop_loss"] = pos["entry_price"] + one_tenth_tp
                    logger.info(f"[{self.symbol}] Trailing Stop (LONG): Стоп переведен в безубыток ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")
                
                if current_price >= pos["take_profit"] + one_tenth_tp:
                    pos["take_profit"] = pos["take_profit"] + r_dist
                    tp_changed = True
                    logger.info(f"[{self.symbol}] Trailing Take Profit (LONG): Новый тейк ({pos['take_profit']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG)\nНовый тейк: `{pos['take_profit']:.4f}`")

                if pos["stop_loss"] >= pos["entry_price"] + one_tenth_tp and current_price < pos["stop_loss"] + r_dist:
                    pos["stop_loss"] = current_price - r_dist
                    logger.info(f"[{self.symbol}] Trailing Stop (LONG): Стоп подтянут ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG) стоп подтянут!\nНовый стоп: `{pos['stop_loss']:.4f}`")

            # Проверка срабатывания
            if current_price <= pos["stop_loss"] or candle["low"] <= pos["stop_loss"]:
                close_price = pos["stop_loss"]
                close_reason = "SL"
                pnl = (close_price - pos["entry_price"]) * pos["qty"]
                closed = True
            elif pos.get("take_profit") and (current_price >= pos["take_profit"] or candle["high"] >= pos["take_profit"]):
                close_price = pos["take_profit"]
                close_reason = "TP"
                pnl = (close_price - pos["entry_price"]) * pos["qty"]
                closed = True
                
        elif pos["side"] == "SELL":
            # Trailing логика SHORT
            if r_dist > 0 and candle["low"] <= pos["entry_price"] - r_dist:
                if pos["stop_loss"] > pos["entry_price"]:
                    pos["stop_loss"] = pos["entry_price"] - one_tenth_tp
                    logger.info(f"[{self.symbol}] Trailing Stop (SHORT): Стоп переведен в безубыток ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")
                
                if current_price < pos["take_profit"] - one_tenth_tp:
                    pos["take_profit"] = pos["take_profit"] - r_dist
                    tp_changed = True
                    logger.info(f"[{self.symbol}] Trailing Take Profit (SHORT): Новый тейк ({pos['take_profit']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT)\nНовый тейк: `{pos['take_profit']:.4f}`")
                    
                if pos["stop_loss"] <= pos["entry_price"] - one_tenth_tp and current_price > pos["stop_loss"] - r_dist:
                    pos["stop_loss"] = current_price + r_dist
                    logger.info(f"[{self.symbol}] Trailing Stop (SHORT): Стоп подтянут ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT) стоп подтянут!\nНовый стоп: `{pos['stop_loss']:.4f}`")

            # Проверка срабатывания
            if current_price >= pos["stop_loss"] or candle["high"] >= pos["stop_loss"]:
                close_price = pos["stop_loss"]
                close_reason = "SL"
                pnl = (pos["entry_price"] - close_price) * pos["qty"]
                closed = True
            elif pos.get("take_profit") and (current_price <= pos["take_profit"] or candle["low"] <= pos["take_profit"]):
                close_price = pos["take_profit"]
                close_reason = "TP"
                pnl = (pos["entry_price"] - close_price) * pos["qty"]
                closed = True

        # Если TP изменился, перевыставляем лимитный ордер на бирже
        if tp_changed and not closed:
            await self._replace_tp_order(pos["take_profit"])

        if closed:
            await self._close_position_procedure(close_reason, close_price, pnl)

    async def _replace_tp_order(self, new_tp: float):
        """Отменяет старый TP и ставит новый LIMIT TP на бирже."""
        pos = self.current_position
        if not pos:
            return
            
        logger.info(f"[{self.symbol}] Замена TP ордера на бирже: новый TP = {new_tp}")
        if "tp_order_id" in pos:
            await self.executor.cancel_order(self.symbol, pos["tp_order_id"])
            
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        tp_res = await self.executor.place_limit_order(self.symbol, close_side, pos["qty"], new_tp)
        if tp_res and tp_res.get("code") == 0:
             order_id = tp_res.get("data", {}).get("orderId")
             self.current_position["tp_order_id"] = order_id

    async def _close_position_procedure(self, reason: str, close_price: float, pnl: float):
        """Процедура полного закрытия позиции (отправка маркет ордера, отмена TP, логирование)."""
        pos = self.current_position
        if not pos:
            return
            
        logger.info(f"[LIVE MEXC] Сработал {reason} по {pos['side']} ({close_price}). PnL: {pnl:.2f}")
        
        # 1. Снимаем остатки (отменяем TP лимитный ордер)
        if "tp_order_id" in pos:
            logger.info(f"[{self.symbol}] LIVE CLOSE: Отмена TP ордера {pos['tp_order_id']}")
            await self.executor.cancel_order(self.symbol, pos["tp_order_id"])
            
        # 2. Если закрываем по SL (виртуальному), кидаем маркет ордер
        # Или если мы перестраховываемся на случай если TP не исполнился полностью
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        logger.info(f"[{self.symbol}] LIVE CLOSE: Отправка MARKET {close_side} ордера для закрытия позииции. Qty: {pos['qty']}")
        await self.executor.place_market_order(self.symbol, close_side, pos["qty"])

        # 3. Логируем
        TradeLogger.log_trade(
            symbol=self.symbol,
            mode="LIVE MEXC",
            side=pos["side"],
            entry_price=pos["entry_price"],
            close_price=close_price,
            qty=pos["qty"],
            pnl=pnl,
            strategy_name=self.active_strategy.__class__.__name__,
            regime=self.current_regime,
            reason=reason
        )
        
        self.risk_manager.report_trade_result(pnl)
        
        result_emoji = "🟢" if pnl > 0 else "🔴"
        if self.ml_filter is not None and self._last_ml_score is not None:
            self.ml_filter.report_trade(
                self._last_ml_score, pnl,
                strategy=self.active_strategy.__class__.__name__,
                regime=self.current_regime,
                symbol=self.symbol,
                side=pos["side"],
                reason=reason
            )
            self._last_ml_score = None
            
        await self._notify(f"🏁 **СДЕЛКА ЗАКРЫТА [LIVE MEXC]**\nПара: `{self.symbol}`   {'🟢LONG🟢' if pos['side'] == 'BUY' else '🔴SHORT🔴'}\nЦена входа: `{pos['entry_price']:.4f}`\nЗакрыто по: `{close_price:.4f}`\nTP: `{pos['take_profit']:.4f}`\nSL: `{pos['stop_loss']:.4f}`\nПричина: `{reason}`\n{result_emoji}PnL: `{pnl:.2f} {self.quote_asset}`")
        
        self.current_position = None

    async def manual_close_position(self) -> bool:
        """Ручное закрытие позиции."""
        pos = self.current_position
        if not pos:
            logger.warning(f"[{self.symbol}] Попытка закрыть несуществующую позицию.")
            return False
            
        current_price = self.get_current_price()
        if not current_price:
            return False
            
        pnl = (current_price - pos["entry_price"]) * pos["qty"] if pos["side"] == "BUY" else (pos["entry_price"] - current_price) * pos["qty"]
        await self._close_position_procedure("MANUAL", current_price, pnl)
        return True

    async def update_sl(self, new_sl: float) -> bool:
        """Обновление SL (только виртуально)."""
        if not self.current_position:
            return False
        old_sl = self.current_position.get("stop_loss")
        self.current_position["stop_loss"] = new_sl
        await self._notify(f"🔄 **SL ИЗМЕНЁН [LIVE MEXC]**\nПара: `{self.symbol}`\nНовый стоп (Виртуальный): `{new_sl:.10f}`")
        logger.info(f"[{self.symbol}] Stop Loss изменён с {old_sl} на {new_sl} (виртуально)")
        return True

    async def update_tp(self, new_tp: float) -> bool:
        """Обновление TP (с заменой ордера на бирже)."""
        if not self.current_position:
            return False
        old_tp = self.current_position.get("take_profit")
        self.current_position["take_profit"] = new_tp
        
        await self._replace_tp_order(new_tp)
        
        await self._notify(f"🔄 **TP ИЗМЕНЁН [LIVE MEXC]**\nПара: `{self.symbol}`\nНовый тейк (Биржевой): `{new_tp:.10f}`")
        logger.info(f"[{self.symbol}] Take Profit изменён с {old_tp} на {new_tp} (на бирже)")
        return True

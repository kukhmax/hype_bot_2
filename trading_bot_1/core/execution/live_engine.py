import asyncio
import pandas as pd
from typing import Dict, Any, Optional

from core.logger import setup_logger
from core.data.mexc_client import MEXCWebSocketClient
from core.data.candle_builder import CandleBuilder
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.base import BaseStrategy
from core.execution.mexc_executor import MEXCExecutor
from core.risk.manager import RiskManager

logger = setup_logger("live_engine")

class LiveEngine:
    """
    Торговый движок реального времени.
    Связующее звено между WebSocket потоком, строителем свечей, стратегией и экзекутором ордеров.
    """

    from typing import Callable, Awaitable
    def __init__(self, 
                 symbol: str, 
                 timeframe_minutes: int, 
                 strategy: BaseStrategy,
                 paper_trading: bool = True,
                 tg_callback: 'Callable[[str], Awaitable[None]]' = None):
        
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.strategy = strategy
        self.paper_trading = paper_trading
        
        # Компоненты системы
        self.executor = MEXCExecutor()
        self.risk_manager = RiskManager()
        self.ws_client = MEXCWebSocketClient([symbol])
        self.candle_builder = CandleBuilder(symbol, timeframe_minutes)
        
        # Состояние (df)
        self.df: pd.DataFrame = pd.DataFrame()
        self.is_ready = False
        
        # Текущая открытая позиция
        self.current_position: Optional[Dict[str, Any]] = None
        self.tg_callback = tg_callback

    async def _notify(self, msg: str):
        if hasattr(self, 'tg_callback') and self.tg_callback:
            await self.tg_callback(msg)

    async def initialize(self):
        """Подготовка: скачивание истории для расчета индикаторов."""
        logger.info(f"Инициализация Live Engine для {self.symbol} ({self.timeframe_minutes}m). Paper Trading: {self.paper_trading}")
        
        # 1. Скачиваем последние N свечей истории
        # Нужно достаточно свечей для EMA200
        historical_df = await MEXCHistoricalDownloader.get_klines(self.symbol, self.timeframe_minutes, limit=500)
        if historical_df.empty:
            logger.error("Не удалось скачать исторические данные. Остановка.")
            return False
            
        self.df = historical_df
        
        # 2. Инициализируем свечной билдер и привязываем коллбэк закрытия свечи
        await self.candle_builder.init_state()
        self.candle_builder.callbacks.append(self._on_candle_closed)
        
        # 3. Привязываем WS клиент к строителю свечей
        self.ws_client.callbacks.append(self._on_ws_message)
        
        # 4. Проверяем баланс и стартуем сессию Риск-Менеджера
        initial_balance = await self.executor.get_balance("USDT")
        self.risk_manager.start_session(initial_balance)
        
        self.is_ready = True
        logger.info("Live Engine успешно инициализирован.")
        return True

    def _on_ws_message(self, msg: dict):
        """Коллбэк на поступление сообщения из WebSocket (тиков)."""
        channel = msg.get("channel", "")
        if channel == "push.deal":
            deal_list = msg.get("data", [])
            for deal_data in deal_list:
                price = None
                vol = None
                deal_time = None
                
                # Обрабатываем особенности формата MEXC (может быть float или str)
                try:
                    price = float(deal_data.get("p", 0))
                    vol = float(deal_data.get("v", 0))
                    deal_time = int(deal_data.get("t", 0))
                except (ValueError, TypeError):
                    continue
                    
                if price and vol and deal_time:
                    # Кормим тик Candle Builder-у (используем асинхронный запуск)
                    asyncio.create_task(self.candle_builder.process_tick(price, vol, deal_time))

    async def _on_candle_closed(self, candle_dict: dict):
        """Срабатывает при закрытии 1m / 15m свечи."""
        logger.info(f"Свеча закрыта: {candle_dict['timestamp']} Close: {candle_dict['close']}")
        
        # Добавляем свечу в наш DataFrame
        new_row = pd.DataFrame([candle_dict])
        self.df = pd.concat([self.df, new_row], ignore_index=True)
        
        # Просчитываем фичи (EMA, RSI, ATR) на новом DF
        self.df = self.strategy.prepare_data(self.df)
        
        # Скармливаем последний индекс стратегии
        current_idx = len(self.df) - 1
        
        # --- Симуляция проверки Stop Loss / Take Profit (т.к. лимитки мы ставим на бирже, мы просто ждем их срабатывания) ---
        # В реальной торговле мы бы периодически запрашивали статус ордеров (GET /api/v3/openOrders), 
        # но для MVP мы можем просто симулировать закрытие локально, если мы в Paper Trading,
        # либо доверять бирже (если ордера реально стоят на MEXC)
        if self.paper_trading and self.current_position:
            await self._check_paper_stops(candle_dict)

        # Вызываем логику стратегии для генерации сигнала
        if self.current_position is None:
            signal_data = self.strategy.on_ohlcv(self.df, current_idx)
            
            if signal_data["signal"] != "NONE":
                await self._notify(f"🎯 **СИГНАЛ** `{self.symbol}`\nНаправление: `{signal_data['signal']}`\nЦена: `{candle_dict['close']}`\nСтратегия: `{self.strategy.__class__.__name__}`")
                await self._execute_signal(signal_data, candle_dict["close"])

    async def _check_paper_stops(self, candle: dict):
        """Только для Paper Trading: закрытие сделки если цена коснулась SL/TP."""
        pos = self.current_position
        if not pos:
            return
            
        closed = False
        pnl = 0.0
        
        if pos["side"] == "BUY":
            if candle["low"] <= pos["stop_loss"]:
                pnl = (pos["stop_loss"] - pos["entry_price"]) * pos["qty"]
                logger.info(f"[-PAPER-] Сработал SL по BUY ({pos['stop_loss']}). PnL: {pnl:.2f}")
                closed = True
            elif pos.get("take_profit") and candle["high"] >= pos["take_profit"]:
                pnl = (pos["take_profit"] - pos["entry_price"]) * pos["qty"]
                logger.info(f"[+PAPER+] Сработал TP по BUY ({pos['take_profit']}). PnL: {pnl:.2f}")
                closed = True
                
        elif pos["side"] == "SELL":
            if candle["high"] >= pos["stop_loss"]:
                pnl = (pos["entry_price"] - pos["stop_loss"]) * pos["qty"] # Шорт: цена выросла = убыток
                logger.info(f"[-PAPER-] Сработал SL по SELL ({pos['stop_loss']}). PnL: {pnl:.2f}")
                closed = True
            elif pos.get("take_profit") and candle["low"] <= pos["take_profit"]:
                pnl = (pos["entry_price"] - pos["take_profit"]) * pos["qty"]
                logger.info(f"[+PAPER+] Сработал TP по SELL ({pos['take_profit']}). PnL: {pnl:.2f}")
                closed = True
                
        if closed:
            self.risk_manager.report_trade_result(pnl)
            await self._notify(f"🏁 **СДЕЛКА ЗАКРЫТА [PAPER]**\nПара: `{self.symbol}`\nPnL: `{pnl:.2f} USDT`")
            self.current_position = None

    async def _execute_signal(self, signal: dict, current_price: float):
        """Отправка сигнала на исполнение (в Paper или Live режиме)."""
        side = signal["signal"]
        stop_loss = signal["stop_loss"]
        take_profit = signal.get("take_profit", None)
        
        current_balance = await self.executor.get_balance("USDT")
        logger.info(f"Обработка сигнала {side}. Текущий баланс: {current_balance} USDT")
        
        # Рассчитываем размер через риск-менеджера
        size_info = self.risk_manager.calculate_position_size(current_balance, current_price, stop_loss)
        
        if size_info["quote_qty"] == 0:
            logger.warning(f"Ордер отменен риск-менеджером. Причина: {size_info['reason']}")
            await self._notify(f"🚫 **РИСК-МЕНЕДЖМЕНТ**\nОрдер отменен: {size_info['reason']}")
            return
            
        quote_qty = size_info["quote_qty"]
        base_qty = size_info["base_qty"]
        
        if self.paper_trading:
            logger.info(f"[PAPER TRADING] Открываем {side} на сумму {quote_qty} USDT. Entry: {current_price}, SL: {stop_loss}, TP: {take_profit}")
            await self._notify(f"🟢 **ПОЗИЦИЯ ОТКРЫТА [PAPER]**\nПара: `{self.symbol}`\nНаправление: `{side}`\nОбъем: `{quote_qty} USDT`\nВход: `{current_price}`\nSL: `{stop_loss}`\nTP: `{take_profit}`")
            self.current_position = {
                "side": side,
                "entry_price": current_price,
                "qty": base_qty,
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }
        else:
            # REAL TRADING (Отправка рыночного ордера)
            # 1. Кидаем Market Order на вход
            result = await self.executor.place_market_order(self.symbol, side, quote_qty)
            if "orderId" in result:
                # 2. Нужно выставить Limit ордера (TP и SL). 
                # На MEXC Spot нет прямого OCO ордера (One Cancels the Other) через простое API.
                # Поэтому в MVP мы просто можем кинуть Stop-Limit или просто надеяться, что
                # скрипт закроет позицию скриптово (как в Paper Trading).
                # В качестве простого решения - отправляем только Limit TP, а SL контролируем кодом.
                
                # Запишем инфу о позе для контроля SL скриптом (Semi-Auto)
                self.current_position = {
                    "side": side,
                    "entry_price": current_price,
                    "qty": base_qty,  # В идеале нужно парсить fact_qty из ответа Market Order-а
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "entry_order_id": result["orderId"]
                }
                
                if take_profit:
                    close_side = "SELL" if side == "BUY" else "BUY"
                    tp_res = await self.executor.place_limit_order(self.symbol, close_side, base_qty, take_profit)
                    if "orderId" in tp_res:
                         self.current_position["tp_order_id"] = tp_res["orderId"]
                         
                logger.info(f"[LIVE TRADING] Успешно открыта позиция {side}. Результат: {result}")
                await self._notify(f"🔴 **ПОЗИЦИЯ ОТКРЫТА [LIVE]**\nПара: `{self.symbol}`\nНаправление: `{side}`\nОбъем: `{quote_qty} USDT`\nВход: `{current_price}`")
            else:
                logger.error(f"[LIVE TRADING] Ошибка открытия ордера: {result}")

    async def run_forever(self):
        if not self.is_ready:
            logger.error("Live Engine не инициализирован!")
            return
            
        logger.info(f"Запуск WebSockets для {self.symbol}...")
        # Запускает бесконечный цикл слушания сокетов
        while True:
            try:
                await self.ws_client.connect()
                await self.ws_client.receive_messages()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Live Engine WebSocket Error: {e}")
                await asyncio.sleep(5)

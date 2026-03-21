import asyncio
import pandas as pd
from typing import Dict, Any, Optional

from core.logger import setup_logger
from core.config import settings
from core.data.mexc_client import MEXCWebSocketClient
from core.data.hyperliquid_client import HyperliquidWebSocketClient
from core.data.candle_builder import CandleBuilder
from core.data.historical import MEXCHistoricalDownloader, HyperliquidHistoricalDownloader
from core.strategies.base import BaseStrategy
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.strategies.liquidity_sweep import LiquiditySweepStrategy
from core.execution.mexc_executor import MEXCExecutor
from core.execution.hyperliquid_executor import HyperliquidExecutor
from core.risk.manager import RiskManager
from core.regime.classifier import RegimeClassifier
from core.ml.ensemble import EnsembleFilter
from core.data.trade_logger import TradeLogger

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
                 paper_trading: bool = True,
                 leverage: int = 1,
                 tg_callback: 'Callable[[str], Awaitable[Optional[int]]]' = None,
                 tg_delete_callback: 'Callable[[int], Awaitable[None]]' = None,
                 ws_client = None,
                 strategy_params: dict = None,
                 max_daily_loss_percent: float = 5.0,
                 enable_gemini: bool = True,
                 tg_position_callback: 'Callable[[str, str], Awaitable[Optional[int]]]' = None):
        
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.paper_trading = paper_trading
        self.leverage = leverage
        
        # Параметры стратегий (из Telegram настроек или дефолтные)
        sp = strategy_params or {}
        rsi_th = sp.get("rsi_threshold", 55)  # Был 50, увеличен для 1m/5m TF
        bb_th = sp.get("bb_width_threshold", 0.025)
        adx_th = sp.get("adx_threshold", 20)
        sl_atr = sp.get("sl_atr_mult", 2)
        rr = sp.get("rr_ratio", 2.5)
        
        # Стратегии для разных режимов
        # ВАЖНО: high_volatility -> LiquiditySweep (не Breakout!)
        # Breakout требует сжатие (low BBW), что противоречит high_volatility.
        # Range -> Breakout: при сжатии (range) ожидаем пробой.
        self.strategies = {
            "strong_trend": TrendPullbackStrategy(rsi_threshold=rsi_th, sl_atr_mult=sl_atr, rr_ratio=max(2.0, rr)),
            "weak_trend": TrendPullbackStrategy(rsi_threshold=rsi_th + 5, sl_atr_mult=sl_atr, rr_ratio=max(2.0, rr - 0.5)),
            "high_volatility": LiquiditySweepStrategy(rsi_ob_os=rsi_th, sl_atr_mult=sl_atr - 0.5, rr_ratio=max(2.0, rr)),
            "range": BreakoutStrategy(bb_width_threshold=bb_th, adx_threshold=adx_th, sl_atr_mult=sl_atr - 0.5, rr_ratio=max(2.0, rr - 0.5))
        }
        self.active_strategy = self.strategies["weak_trend"]
        self.current_regime = "unknown"
        
        # Компоненты системы: выбор биржи
        from bot.settings import bot_settings
        self.exchange_name = bot_settings.get("exchange", "mexc").lower()
        if self.exchange_name == "hyperliquid":
             self.executor = HyperliquidExecutor()
             self.quote_asset = "USDC"
        else:
             self.executor = MEXCExecutor()
             self.quote_asset = "USDT"
        self.risk_manager = RiskManager(
            risk_per_trade_percent=sp.get("risk_percent", 2.0),
            max_daily_loss_percent=max_daily_loss_percent
        )
        self.enable_gemini = enable_gemini
        # WS клиент — общий, передаётся из EngineManager
        self.ws_client = ws_client
        self.candle_builder = CandleBuilder(symbol, timeframe_minutes)
        
        # ML Ensemble Filter (опционально)
        self.ml_filter: Optional[EnsembleFilter] = None
        self._last_ml_score: Optional[float] = None  # Для отчёта в DynamicThreshold
        if settings.ENABLE_ML_FILTER:
            self.ml_filter = EnsembleFilter()
            ml_status = "обучены" if self.ml_filter.is_ready else "FALLBACK (модели не найдены)"
            logger.info(f"ML Ensemble Filter ВКЛЮЧЁН. Модели: {ml_status}")
        else:
            logger.info("ML Ensemble Filter ВЫКЛЮЧЕН (ENABLE_ML_FILTER=false)")
        
        # Состояние (df)
        self.df: pd.DataFrame = pd.DataFrame()
        self.is_ready = False
        
        # Текущая открытая позиция
        self.current_position: Optional[Dict[str, Any]] = None
        self.tg_callback = tg_callback
        self.tg_delete_callback = tg_delete_callback
        self.tg_position_callback = tg_position_callback

    async def _notify(self, msg: str) -> Optional[int]:
        if hasattr(self, 'tg_callback') and self.tg_callback:
            return await self.tg_callback(msg)
        return None
        
    async def _notify_position(self, msg: str, symbol: str) -> Optional[int]:
        if hasattr(self, 'tg_position_callback') and self.tg_position_callback:
            return await self.tg_position_callback(msg, symbol)
        elif hasattr(self, 'tg_callback') and self.tg_callback:
            return await self.tg_callback(msg)
        return None

    async def _delayed_delete(self, msg_ids: list, delay_seconds: int = 10):
        """Отложенное удаление списка сообщений."""
        await asyncio.sleep(delay_seconds)
        if hasattr(self, 'tg_delete_callback') and self.tg_delete_callback:
            for msg_id in msg_ids:
                if msg_id is not None:
                    try:
                        await self.tg_delete_callback(msg_id)
                    except Exception as e:
                        logger.error(f"[{self.symbol}] Ошибка при отложенном удалении сообщения {msg_id}: {e}")

    async def initialize(self):
        """Подготовка: скачивание истории для расчета индикаторов."""
        logger.info(f"Инициализация Live Engine для {self.symbol} ({self.timeframe_minutes}m). Paper Trading: {self.paper_trading}")
        
        # 1. Скачиваем последние N свечей истории
        if self.exchange_name == "hyperliquid":
            historical_df = await HyperliquidHistoricalDownloader.get_klines(self.symbol, self.timeframe_minutes, limit=500)
        else:
            historical_df = await MEXCHistoricalDownloader.get_klines(self.symbol, self.timeframe_minutes, limit=500)

        if historical_df.empty:
            logger.error("Не удалось скачать исторические данные. Остановка.")
            return False
            
        self.df = historical_df
        
        # 2. Инициализируем свечной билдер и привязываем коллбэк закрытия свечи
        await self.candle_builder.init_state()
        self.candle_builder.callbacks.append(self._on_candle_closed)
        
        # 3. Привязываем per-symbol callback в shared WS клиент
        if self.ws_client:
            self.ws_client.add_symbol_callback(self.symbol, self._on_ws_message)
            logger.info(f"[{self.symbol}] Зарегистрирован в общем WebSocket клиенте")
        else:
            logger.warning(f"[{self.symbol}] WS клиент не передан — тики не будут приходить")
        
        # 4. Проверяем баланс и стартуем сессию Риск-Менеджера
        if self.paper_trading:
            # В paper/signals режиме используем виртуальный баланс
            initial_balance = 10000.0
            logger.info(f"Paper Trading: виртуальный баланс {initial_balance} {self.quote_asset}")
        else:
            initial_balance = await self.executor.get_balance(self.quote_asset)
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
        if not hasattr(self, "_process_lock"):
            self._process_lock = asyncio.Lock()
            
        async with self._process_lock:
            try:
                await self._process_candle(candle_dict)
            except Exception as e:
                logger.error(f"[{self.symbol}] ОШИБКА при обработке свечи: {type(e).__name__}: {e}", exc_info=True)
                await self._notify(f"⚠️ Ошибка обработки свечи `{self.symbol}`: {e}")

    async def _process_candle(self, candle_dict: dict):
        """Внутренняя логика обработки закрытой свечи."""
        logger.info(f"Свеча закрыта: {candle_dict['timestamp']} Close: {candle_dict['close']}")
        
        # Добавляем свечу в наш DataFrame
        new_row = pd.DataFrame([candle_dict])
        self.df = pd.concat([self.df, new_row], ignore_index=True)
        
        # Просчитываем фичи (EMA, RSI, ATR) на новом DF используя любую стратегию (фичи общие)
        self.df = self.active_strategy.prepare_data(self.df)
        
        # Текущий индекс — ВСЕГДА последняя строка
        current_idx = len(self.df) - 1
        
        # --- Ограничиваем размер DataFrame (утечка памяти и замедление) ---
        MAX_ROWS = 1000
        if len(self.df) > MAX_ROWS:
            self.df = self.df.iloc[-MAX_ROWS:].reset_index(drop=True)
            current_idx = len(self.df) - 1
            logger.debug(f"DataFrame урезан до {MAX_ROWS} строк.")
        
        # Определяем режим рынка (Regime Classifier)
        regime_info = RegimeClassifier.classify(self.df, current_idx)
        new_regime = regime_info["regime"]
        
        logger.debug(f"[{self.symbol}] Оценка режима: {new_regime} (ADX: {regime_info.get('adx', 0):.2f}, BBW: {regime_info.get('bb_width_percent', 0):.2f}, EMA_Slope: {regime_info.get('ema_slope', 0):.2f})")
        
        if new_regime != self.current_regime and new_regime != "unknown":
            logger.info(f"Смена режима рынка: {self.current_regime} -> {new_regime}")
            self.current_regime = new_regime
            if new_regime in self.strategies:
                self.active_strategy = self.strategies[new_regime]
                # Пересчитываем prepare_data для новой стратегии
                # (например LiquiditySweep добавляет local_low/local_high)
                self.df = self.active_strategy.prepare_data(self.df)
                # await self._notify(f"🔄 *СМЕНА РЕЖИМА*\nНовый режим: `{new_regime}`\nВключена стратегия: `{self.active_strategy.__class__.__name__}`")
        
        # --- Проверка Stop Loss / Take Profit ---
        if self.current_position:
            await self._check_stops(candle_dict)

        # Вызываем логику стратегии для генерации сигнала
        logger.warning(f"[{self.symbol}] STATE DEBUG: current_position={self.current_position}")
        if self.current_position is None:
            logger.debug(f"[{self.symbol}] Проверка сигналов стратегией {self.active_strategy.__class__.__name__} на индексе {current_idx}")
            signal_data = self.active_strategy.on_ohlcv(self.df, current_idx)
            logger.debug(f"[{self.symbol}] Результат стратегии: {signal_data}")
            
            if signal_data["signal"] != "NONE":
                row = self.df.iloc[current_idx]
                close = candle_dict['close']
                rsi = row.get('rsi', 0)
                adx = row.get('adx', 0)
                ema_21 = row.get('ema_21', 0)
                ema_50 = row.get('ema_50', 0)
                ema_200 = row.get('ema_200', 0)
                atr = row.get('atr', 0)
                
                upper_bb = row.get('bb_upper', 0)
                lower_bb = row.get('bb_lower', 0)
                bb_width_pct = 0
                if pd.notna(upper_bb) and pd.notna(lower_bb) and close:
                    bb_width_pct = (upper_bb - lower_bb) / close * 100
                
                sl = signal_data.get('stop_loss', 0)
                tp = signal_data.get('take_profit', 0)
                direction = signal_data['signal']

                
                strategy_name = self.active_strategy.__class__.__name__
                
                # Позиция цены относительно EMA
                ema_pos = "выше" if close > ema_200 else "ниже"
                
                # ═══════════════════════════════════════════
                # 📊 ПОЛНЫЙ СЕТАП — отправляется ВСЕГДА
                # ═══════════════════════════════════════════
                setup_msg = (
                    f"{'🟢' if direction == 'BUY' else '🔴'} *СИГНАЛ `{self.symbol}`  {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}*\n"
                    f"\n"
                    f"📊 *Сетап:*\n"
                    f"  Стратегия: `{strategy_name}`\n"
                    f"  Режим: `{self.current_regime}`\n"
                    f"  Таймфрейм: `{self.timeframe_minutes}m`\n"
                    f"\n"
                    f"💰 *Цена:*\n"
                    f"  Close: `{close}`\n"
                    f"  SL: `{sl:.3f}` | TP: `{tp:.3f}`\n"
                    f"  R:R = `1:{((tp - close) / (close - sl)) if direction == 'BUY' and close != sl else ((close - tp) / (sl - close)) if direction == 'SELL' and sl != close else 0:.1f}`\n"
                    f"\n"
                    f"📈 *Индикаторы:*\n"
                    f"  RSI: `{rsi:.1f}` | ADX: `{adx:.1f}`\n"
                    f"  EMA21: `{ema_21:.2f}` | EMA50: `{ema_50:.2f}`\n"
                    f"  EMA200: `{ema_200:.2f}` ({ema_pos})\n"
                    f"  BB Width: `{bb_width_pct:.2f}%` | ATR: `{atr:.4f}`"
                )
                setup_msg_id = await self._notify(setup_msg)
                
                # ═══════════════════════════════════════════
                # 🧠 ML ENSEMBLE FILTER
                # ═══════════════════════════════════════════
                if self.ml_filter is not None:
                    logger.info(f"[{self.symbol}] ML фильтр: оценка сигнала {direction}...")
                    ml_passed, ml_score, ml_details = self.ml_filter.evaluate(
                        self.df, current_idx, direction,
                        strategy_name=strategy_name,
                        regime=self.current_regime,
                        symbol=self.symbol
                    )
                    self._last_ml_score = ml_score
                    
                    ml_emoji = "✅" if ml_passed else "❌"
                    ml_verdict = "ОДОБРИЛ" if ml_passed else "ОТКЛОНИЛ"
                    
                    if ml_details.get("fallback"):
                        ml_text = (
                            f"🧠 *ML АНСАМБЛЬ {ml_verdict}*  {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}    `{self.symbol}`\n\n"
                            f"⚠️ *Обучение не завершено*\n"
                            f"Сигнал одобрен автоматически (Fallback-режим)."
                        )
                    elif ml_details.get("blocked_by_history"):
                        ml_text = (
                            f"🧠 *ML АНСАМБЛЬ {ml_verdict}*  {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}    `{self.symbol}`\n\n"
                            f"📊 *Заблокировано историей сделок:*\n"
                            f"`{ml_details.get('block_reason', 'N/A')}`"
                        )
                    else:
                        ml_text = (
                            f"🧠 *ML АНСАМБЛЬ {ml_verdict}*  {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}    `{self.symbol}`\n\n"
                            f"  Momentum: `{ml_details.get('momentum_score', 0):.3f}`\n"
                            f"  Volatility: `{ml_details.get('volatility_score', 0):.3f}`\n"
                            f"  Structure: `{ml_details.get('structure_score', 0):.3f}`\n\n"
                            f"🎯 Score: `{ml_details.get('ensemble_score', 0):.3f}` "
                            f"{'≥' if ml_passed else '<'} "
                            f"Threshold: `{ml_details.get('threshold', 0):.3f}` "
                            f"(margin: `{ml_details.get('margin', 0):+.3f}`)"
                        )
                        
                    ml_msg_id = await self._notify(ml_text)
                    logger.info(
                        f"[{self.symbol}] {direction} {ml_verdict} ML. "
                        f"Score={ml_details.get('ensemble_score', 0):.3f} "
                        f"Threshold={ml_details.get('threshold', 0):.3f}"
                    )
                    
                    if not ml_passed:
                        # Запускаем отложенное удаление сообщений (сетап + ответ ML)
                        # Если заблокировано историей сделок — удаляем быстрее
                        delete_delay = 5 if ml_details.get("blocked_by_history") else 10
                        asyncio.create_task(self._delayed_delete([setup_msg_id, ml_msg_id], delay_seconds=delete_delay))
                        return
                
                # ═══════════════════════════════════════════
                # 🤖 AI VERIFICATION (Gemini)
                # ═══════════════════════════════════════════
                if not self.enable_gemini:
                    logger.info(f"[{self.symbol}] Gemini AI ВЫКЛЮЧЕН в настройках. Сигнал → исполнение.")
                    await self._notify(
                        f"⚡ *Gemini AI ВЫКЛЮЧЕН*\n"
                        f"Сигнал `{direction}` направлен напрямую в исполнение."
                    )
                    await self._execute_signal(signal_data, close)
                elif self.timeframe_minutes < settings.AI_VERIFY_MIN_TIMEFRAME:
                    logger.info(
                        f"[{self.symbol}] AI верификация ПРОПУЩЕНА "
                        f"(TF={self.timeframe_minutes}m < порог {settings.AI_VERIFY_MIN_TIMEFRAME}m)"
                    )
                    await self._notify(
                        f"⚡ *AI ПРОПУЩЕН* (скальпинг {self.timeframe_minutes}m)\n"
                        f"Сигнал `{direction}` направлен напрямую в исполнение."
                    )
                    await self._execute_signal(signal_data, close)
                else:
                    from core.ai.gemini_client import gemini_client
                    
                    indicators = {
                        'close': close, 'rsi': rsi, 'adx': adx,
                        'ema_200': ema_200, 'bb_width_percent': bb_width_pct
                    }
                    
                    # await self._notify("🤖 Запрашиваю 'Второе мнение' у ИИ Gemini...")
                    is_approved, reasoning = await gemini_client.verify_signal(
                        self.symbol, 
                        self.timeframe_minutes, 
                        direction, 
                        indicators
                    )
                    
                    if is_approved:
                        await self._notify(f"✅ *ИИ ОДОБРИЛ* `{self.symbol}`    {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}\n`{reasoning}`")
                        await self._execute_signal(signal_data, close)
                    else:
                        await self._notify(f"❌ *ИИ ОТКЛОНИЛ* `{self.symbol}`   {'🟢LONG🟢' if direction == 'BUY' else '🔴SHORT🔴'}\n`{reasoning}`")
                        logger.info(f"[{self.symbol}] ❌❌❌ Сделка отклонена ИИ. Причина: {reasoning}")

    async def _check_stops(self, candle: dict):
        """Проверка закрытия сделки по SL/TP (и для Paper, и для Live с ручным контролем)."""
        pos = self.current_position
        if not pos:
            return
            
        closed = False
        pnl = 0.0
        
        try:
            current_price = self.get_current_price()
        except Exception as e:
            logger.error(f"[{self.symbol}] Ошибка получения текущей цены: {e}")
            current_price = 0.0
        
        
        # Логика Trailing Stop (перевод в БУ при достижении 1R)
        if "initial_sl" not in pos:
            pos["initial_sl"] = pos["stop_loss"]
            
        r_dist = abs(pos["entry_price"] - pos["initial_sl"])
        one_tenth_tp = abs((pos["take_profit"] - pos["entry_price"])/10)
        
        if pos["side"] == "BUY":
            # Перевод в безубыток при достижении 1R
            if r_dist > 0 and candle["high"] >= pos["entry_price"] + r_dist:
                if pos["stop_loss"] < pos["entry_price"]:
                    pos["stop_loss"] = pos["entry_price"] + one_tenth_tp
                    logger.info(f"Trailing Stop (LONG): Стоп переведен в безубыток ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")
                if current_price:
                    if current_price >= pos["take_profit"] + one_tenth_tp:
                        pos["take_profit"] = pos["take_profit"] + r_dist
                        logger.info(f"Trailing Take Profit (LONG): Новый тейк ({pos['take_profit']})")
                        await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG)\nНовый тейк: `{pos['take_profit']:.4f}`")

                    if pos["stop_loss"] >= pos["entry_price"] + one_tenth_tp and current_price < pos["stop_loss"] + r_dist:
                        pos["stop_loss"] = current_price - r_dist
                        logger.info(f"Trailing Stop (LONG): Стоп переведен в безубыток ({pos['stop_loss']})")
                        await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (LONG) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")
                    

            if candle["low"] <= pos["stop_loss"]:
                close_price = pos["stop_loss"]
                close_reason = "SL"
                pnl = (close_price - pos["entry_price"]) * pos["qty"]
                closed = True
            elif pos.get("take_profit") and candle["high"] >= pos["take_profit"]:
                close_price = pos["take_profit"]
                close_reason = "TP"
                pnl = (close_price - pos["entry_price"]) * pos["qty"]
                closed = True
                
        elif pos["side"] == "SELL":
            # Перевод в безубыток при достижении 1R
            if r_dist > 0 and candle["low"] <= pos["entry_price"] - r_dist:
                if pos["stop_loss"] > pos["entry_price"]:
                    pos["stop_loss"] = pos["entry_price"] - one_tenth_tp
                    logger.info(f"Trailing Stop (SHORT): Стоп переведен в безубыток ({pos['stop_loss']})")
                    await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")
                if current_price:
                    if current_price < pos["take_profit"] - one_tenth_tp:
                        pos["take_profit"] = pos["take_profit"] - r_dist
                        logger.info(f"Trailing Take Profit (SHORT): Новый тейк ({pos['take_profit']})")
                        await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT)\nНовый тейк: `{pos['take_profit']:.4f}`")
                        
                    if pos["stop_loss"] <= pos["entry_price"] - one_tenth_tp and current_price > pos["stop_loss"] - r_dist:
                        pos["stop_loss"] = current_price + r_dist
                        logger.info(f"Trailing Stop (SHORT): Стоп переведен в безубыток ({pos['stop_loss']})")
                        await self._notify(f"🛡 *ATR TRAILING*\nСделка `{self.symbol}` (SHORT) переведена в БЕЗУБЫТОК!\nНовый стоп: `{pos['stop_loss']:.4f}`")

            if candle["high"] >= pos["stop_loss"]:
                close_price = pos["stop_loss"]
                close_reason = "SL"
                pnl = (pos["entry_price"] - close_price) * pos["qty"] # Шорт: цена выросла = убыток
                closed = True
            elif pos.get("take_profit") and candle["low"] <= pos["take_profit"]:
                close_price = pos["take_profit"]
                close_reason = "TP"
                pnl = (pos["entry_price"] - close_price) * pos["qty"]
                closed = True
                
        if closed:
            mode_tag = "PAPER" if self.paper_trading else "LIVE"
            logger.info(f"[{mode_tag}] Сработал {close_reason} по {pos['side']} ({close_price}). PnL: {pnl:.2f}")
            
            # Логируем сделку в CSV
            from core.data.trade_logger import TradeLogger
            TradeLogger.log_trade(
                symbol=self.symbol,
                mode=mode_tag,
                side=pos["side"],
                entry_price=pos["entry_price"],
                close_price=close_price,
                qty=pos["qty"],
                pnl=pnl,
                strategy_name=self.active_strategy.__class__.__name__,
                regime=self.current_regime,
                reason=close_reason
            )
            
            self.risk_manager.report_trade_result(pnl)
            
            # Отчёт в ML Ensemble (для адаптации Dynamic Threshold)
            result_emoji = "🟢" if pnl > 0 else "🔴"
            if self.ml_filter is not None and self._last_ml_score is not None:
                self.ml_filter.report_trade(
                    self._last_ml_score, pnl,
                    strategy=self.active_strategy.__class__.__name__,
                    regime=self.current_regime,
                    symbol=self.symbol,
                    side=pos["side"],
                    reason=close_reason
                )
                
                logger.info(
                    f"[{self.symbol}] ML Threshold обновлён: {result_emoji} PnL={pnl:.2f} score={self._last_ml_score:.3f} "
                    f"новый threshold={self.ml_filter.threshold.get_threshold():.3f}"
                )
                self._last_ml_score = None
                
            await self._notify(f"🏁 **СДЕЛКА ЗАКРЫТА [{mode_tag}]**\nПара: `{self.symbol}`   {'🟢LONG🟢' if pos['side'] == 'BUY' else '🔴SHORT🔴'}\nЦена входа: `{pos['entry_price']:.4f}`\nTP: `{pos['take_profit']:.4f}`\nSL: `{pos['stop_loss']:.4f}`\nПричина: `{close_reason}`\n{result_emoji}PnL: `{pnl:.2f} {self.quote_asset}`")
            
            if not self.paper_trading:
                # В Live режиме нужно закрыть позицию на бирже (рыночным ордером)
                # и отменить лимитный ордер TP, если он есть
                close_side = "SELL" if pos["side"] == "BUY" else "BUY"
                if close_reason == "SL":
                    # Если закрываем по нашему виртуальному SL, кидаем маркет-ордер на весь объем позиции
                    # Внимание: MEXC MARKET ордера могут требовать quoteOrderQty, но для закрытия позиции 
                    # проще использовать quantity (базовый объем) или если MEXC API позволяет закрыть все. 
                    # В нашем текущем MEXCExecutor place_market_order принимает quoteOrderQty (в USDT).
                    # Так как мы закрываем ровно qty монет:
                    close_quote_qty = pos["qty"] * close_price
                    logger.info(f"[{self.symbol}] LIVE CLOSE: Отправка MARKET {close_side} ордера для закрытия SL. Qty: {pos['qty']}, QuoteQty: {close_quote_qty}")
                    await self.executor.place_market_order(self.symbol, close_side, close_quote_qty)
                    
                # Отменяем LIMIT TP ордер
                if "tp_order_id" in pos:
                    logger.info(f"[{self.symbol}] LIVE CLOSE: Отмена TP ордера {pos['tp_order_id']}")
                    await self.executor.cancel_order(self.symbol, pos["tp_order_id"])
                    
            self.current_position = None

    async def _execute_signal(self, signal: dict, current_price: float):
        """Отправка сигнала на исполнение (в Paper или Live режиме)."""
        side = signal["signal"]
        stop_loss = signal["stop_loss"]
        take_profit = signal.get("take_profit", None)
        
        # В paper trading берём баланс из risk_manager (виртуальный),
        # в live — запрашиваем реальный с биржи
        if self.paper_trading:
            current_balance = self.risk_manager.session_start_balance
        else:
            current_balance = await self.executor.get_balance(self.quote_asset)
        logger.info(f"Обработка сигнала {side}. Текущий баланс: {current_balance} {self.quote_asset}. Плечо: {self.leverage}x")
        
        # Рассчитываем размер через риск-менеджера
        size_info = self.risk_manager.calculate_position_size(current_balance, current_price, stop_loss)
        
        if size_info["quote_qty"] == 0:
            logger.warning(f"Ордер отменен риск-менеджером. Причина: {size_info['reason']}")
            await self._notify(f"🚫 **РИСК-МЕНЕДЖМЕНТ**\nОрдер отменен: {size_info['reason']}")
            return
            
        # Применяем плечо
        quote_qty = size_info["quote_qty"] * self.leverage
        base_qty = size_info["base_qty"] * self.leverage
        
        # Расчеты для красивого отображения в Telegram
        margin = size_info["quote_qty"]
        free_balance = current_balance - margin
        sl_usdt = abs(current_price - stop_loss) * base_qty
        tp_usdt = abs(take_profit - current_price) * base_qty if take_profit else 0
        
        if self.paper_trading:
            logger.info(f"🟢 [PAPER TRADING] Открываем {side} на сумму {quote_qty} USDT. Entry: {current_price}, SL: {stop_loss}, TP: {take_profit}")
            await self._notify_position(
                f"🟢 *ПОЗИЦИЯ ОТКРЫТА [PAPER]*\n"
                f"Пара: `{self.symbol}`\n"
                f"Направление: `{'🟢LONG🟢' if side == 'BUY' else '🔴SHORT🔴'}`\n"
                f"Своб. баланс: `{free_balance:.2f} {self.quote_asset}`\n"
                f"Маржа: `{margin:.2f} {self.quote_asset}` (Плечо {self.leverage}x)\n"
                f"Объем: `{quote_qty:.2f} {self.quote_asset}`\n"
                f"Вход: `{current_price:.10f}`\n"
                f"SL: `{stop_loss:.10f}` (`-{sl_usdt:.2f} {self.quote_asset}`)\n"
                f"TP: `{take_profit:.10f}` (`+{tp_usdt:.2f} {self.quote_asset}`)",
                self.symbol
            )
            self.current_position = {
                "side": side,
                "entry_price": current_price,
                "qty": base_qty,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "leverage": self.leverage
            }
            
            # await self._notify(f"📄 *PAPER TRADING: ПОЗИЦИЯ ОТКРЫТА*\nПара: `{self.symbol}`\nНаправление: `{side}`\nОбъем: `{base_qty:.4f}` монет\nSL: `{stop_loss:.4f}` | TP: `{take_profit:.4f}`")
        else:
            # REAL TRADING (Отправка рыночного ордера)
            # 1. Кидаем Market Order на вход
            result = await self.executor.place_market_order(self.symbol, side, quote_qty)
            if result.get("orderId"):
                # 2. Нужно выставить Limit ордера (TP и SL). 
                # На MEXC Spot нет прямого OCO ордера (One Cancels the Other) через простое API.
                # Поэтому в MVP мы просто можем кинуть Stop-Limit или просто надеяться, что
                # скрипт закроет позицию скриптово (как в Paper Trading).
                # В качестве простого решения - отправляем только Limit TP, а SL контролируем кодом.
                
                # Запишем инфу о позе для контроля SL скриптом (Semi-Auto)
                self.current_position = {
                    "orderId": result["orderId"],
                    "side": side,
                    "entry_price": current_price,
                    "qty": base_qty,  # В идеале нужно парсить fact_qty из ответа Market Order-а
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "leverage": self.leverage
                }
                
                if take_profit:
                    close_side = "SELL" if side == "BUY" else "BUY"
                    tp_res = await self.executor.place_limit_order(self.symbol, close_side, base_qty, take_profit)
                    if "orderId" in tp_res:
                         self.current_position["tp_order_id"] = tp_res["orderId"]
                         
                logger.info(f"🔴 [LIVE TRADING] Успешно открыта позиция {side}. Результат: {result}")
                await self._notify_position(
                    f"📈 *ПОЗИЦИЯ ОТКРЫТА [LIVE]*\n"
                    f"Пара: `{self.symbol}`\n"
                    f"Направление: `{'🟢LONG🟢' if side == 'BUY' else '🔴SHORT🔴'}`\n"
                    f"Своб. баланс: `{free_balance:.2f} {self.quote_asset}`\n"
                    f"Маржа: `{margin:.2f} {self.quote_asset}` (Плечо {self.leverage}x)\n"
                    f"Объем: `{quote_qty:.2f} {self.quote_asset}`\n"
                    f"Вход: `{current_price:.10f}`\n"
                    f"SL: `{stop_loss:.10f}` (`-{sl_usdt:.2f} {self.quote_asset}`)\n"
                    f"TP: `{take_profit:.10f}` (`+{tp_usdt:.2f} {self.quote_asset}`)",
                    self.symbol
                )
            else:
                logger.error(f"[LIVE TRADING] Ошибка открытия ордера: {result}")

    def get_current_price(self) -> Optional[float]:
        """Безопасное получение последней известной цены."""
        if getattr(self, 'candle_builder', None) and self.candle_builder.current_candle:
            return self.candle_builder.current_candle["close"]
        if not self.df.empty:
            return self.df.iloc[-1]["close"]
        return None

    async def manual_close_position(self) -> bool:
        """Ручное закрытие позиции (по кнопке)."""
        pos = self.current_position
        if not pos:
            logger.warning(f"[{self.symbol}] Попытка закрыть несуществующую позицию.")
            return False
            
        current_price = self.get_current_price()
            
        if not current_price:
            logger.error(f"[{self.symbol}] Не удалось определить текущую цену для ручного закрытия")
            return False
            
        close_price = current_price
        
        if pos["side"] == "BUY":
            pnl = (close_price - pos["entry_price"]) * pos["qty"]
        else:
            pnl = (pos["entry_price"] - close_price) * pos["qty"]
            
        mode_tag = "PAPER" if self.paper_trading else "LIVE"
        logger.info(f"[{mode_tag}] Ручное закрытие позиции {pos['side']} ({close_price}). PnL: {pnl:.2f}")
        
        # Логируем сделку в CSV
        from core.data.trade_logger import TradeLogger
        TradeLogger.log_trade(
            symbol=self.symbol,
            mode=mode_tag,
            side=pos["side"],
            entry_price=pos["entry_price"],
            close_price=close_price,
            qty=pos["qty"],
            pnl=pnl,
            strategy_name=self.active_strategy.__class__.__name__,
            regime=self.current_regime,
            reason="MANUAL"
        )
        
        self.risk_manager.report_trade_result(pnl)
        
        # Интеграция с ML 
        result_emoji = "🟢" if pnl > 0 else "🔴"
        if self.ml_filter is not None and self._last_ml_score is not None:
            self.ml_filter.report_trade(
                self._last_ml_score, pnl,
                strategy=self.active_strategy.__class__.__name__,
                regime=self.current_regime,
                symbol=self.symbol,
                side=pos["side"],
                reason="MANUAL"
            )
            self._last_ml_score = None
            
        await self._notify(f"🏁 **СДЕЛКА ЗАКРЫТА ВРУЧНУЮ [{mode_tag}]**\nПара: `{self.symbol}`\n{result_emoji}PnL: `{pnl:.2f} {self.quote_asset}`")
        
        if not self.paper_trading:
            close_side = "SELL" if pos["side"] == "BUY" else "BUY"
            close_quote_qty = pos["qty"] * close_price
            logger.info(f"[{self.symbol}] LIVE CLOSE MANUAL: Отправка MARKET {close_side} ордера. Qty: {pos['qty']}, QuoteQty: {close_quote_qty}")
            await self.executor.place_market_order(self.symbol, close_side, close_quote_qty)
            
            if "tp_order_id" in pos:
                logger.info(f"[{self.symbol}] LIVE CLOSE MANUAL: Отмена TP ордера {pos['tp_order_id']}")
                await self.executor.cancel_order(self.symbol, pos["tp_order_id"])
                
        self.current_position = None
        return True
        
    async def update_sl(self, new_sl: float) -> bool:
        """Обновление значения Stop Loss вручную."""
        if not self.current_position:
            return False
            
        self.current_position["stop_loss"] = new_sl
        await self._notify(f"🔄 **SL ИЗМЕНЁН**\nПара: `{self.symbol}`\nНовый стоп: `{new_sl:.10f}`")
        logger.info(f"[{self.symbol}] Stop Loss изменён на {new_sl}")
        return True
        
    async def update_tp(self, new_tp: float) -> bool:
        """Обновление значения Take Profit вручную."""
        if not self.current_position:
            return False
            
        old_tp = self.current_position.get("take_profit")
        self.current_position["take_profit"] = new_tp
        
        if not self.paper_trading:
            # Отменяем старый лимитный ордер
            if "tp_order_id" in self.current_position:
                logger.info(f"[{self.symbol}] Обновление TP: отмена старого лимитника {self.current_position['tp_order_id']}")
                await self.executor.cancel_order(self.symbol, self.current_position["tp_order_id"])
            
            # Выставляем новый лимитный ордер
            close_side = "SELL" if self.current_position["side"] == "BUY" else "BUY"
            tp_res = await self.executor.place_limit_order(self.symbol, close_side, self.current_position["qty"], new_tp)
            if "orderId" in tp_res:
                 self.current_position["tp_order_id"] = tp_res["orderId"]
                 logger.info(f"[{self.symbol}] Обновление TP: новый лимитник выставлен {tp_res['orderId']}")
                 
        await self._notify(f"🔄 **TP ИЗМЕНЁН**\nПара: `{self.symbol}`\nНовый тейк: `{new_tp:.10f}`")
        logger.info(f"[{self.symbol}] Take Profit изменён с {old_tp} на {new_tp}")
        return True

    # WebSocket управляется EngineManager — run_forever() больше не нужен.
    # Движок получает тики через callback, зарегистрированный в shared WS клиенте.

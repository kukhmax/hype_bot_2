import pandas as pd
from typing import Dict, Any

from core.logger import setup_logger
from core.strategies.base import BaseStrategy
from core.features.indicators import FeatureEngineer

logger = setup_logger("trend_pullback")

class TrendPullbackStrategy(BaseStrategy):
    """
    Стратегия 1 (Core Intraday): Trend Pullback
    Логика: Поиск откатов к динамической поддержке на выраженном тренде.
    
    Правила Long:
    1. Тренд: EMA_50 > EMA_200 и Close > EMA_200 (Бычий режим)
    2. Моментум: EMA_21 > EMA_50 (Краткосрочный тренд вверх)
    3. Откат: RSI < 40 (Перепроданность на откате)
    4. Триггер: Цена (Low) касается или пробивает EMA_21 вниз.
    
    Выход:
    1. SL: 1.5 * ATR (от Low свечи входа)
    2. TP: Фиксированный 2 R (Risk/Reward 1:2)
    """
    def __init__(self, rsi_threshold: int = 40, sl_atr_mult: float = 1.5, rr_ratio: float = 2.0):
        super().__init__("Trend_Pullback_Strategy")
        self.rsi_threshold = rsi_threshold
        self.sl_atr_mult = sl_atr_mult
        self.rr_ratio = rr_ratio

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предрасчет всех индикаторов перед прогоном стратегии."""
        return FeatureEngineer.process_all_features(df)

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """Расчет сигнала на каждой закрытой свече."""
        
        # Нам нужно как минимум 2 свечи, чтобы не выйти за индекс
        if current_idx < 1:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        prev_candle = df.iloc[current_idx - 1]
        
        # Проверяем условия тренда (на закрытой свече)
        uptrend_confirmed = (
            candle["ema_50"] > candle["ema_200"] and 
            candle["close"] > candle["ema_200"] and 
            candle["ema_21"] > candle["ema_50"]
        )
        
        downtrend_confirmed = (
            candle["ema_50"] < candle["ema_200"] and 
            candle["close"] < candle["ema_200"] and 
            candle["ema_21"] < candle["ema_50"]
        )

        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        if uptrend_confirmed:
            # Откат: RSI упал ниже порога на прошлой или текущей свече
            rsi_condition = candle["rsi"] <= self.rsi_threshold or prev_candle["rsi"] <= self.rsi_threshold
            
            # Триггер: Цена коснулась EMA 21 (Low <= EMA21, но закрытие может быть выше или ниже, главное прокол)
            touch_ema21 = candle["low"] <= candle["ema_21"]
            
            # Для надежности требуем чтобы закрытие было выше EMA50
            close_above_ema50 = candle["close"] > candle["ema_50"]
            
            if rsi_condition and touch_ema21 and close_above_ema50:
                sl_distance = candle["atr"] * self.sl_atr_mult
                stop_loss = candle["close"] - sl_distance
                # Рассчитываем Take Profit 1 к 2 RR
                take_profit = candle["close"] + (sl_distance * self.rr_ratio)
                
                logger.debug(f"[{candle['timestamp']}] Сигнал BUY. Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
                return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        if downtrend_confirmed:
            # Для шорта ищем перекупленность на откате (например RSI > 60)
            short_rsi_threshold = 100 - self.rsi_threshold
            rsi_condition = candle["rsi"] >= short_rsi_threshold or prev_candle["rsi"] >= short_rsi_threshold
            
            # Триггер: Цена снизу ударилась об EMA21 (High >= EMA21)
            touch_ema21 = candle["high"] >= candle["ema_21"]
            
            # Закрытие ниже EMA50
            close_below_ema50 = candle["close"] < candle["ema_50"]
            
            if rsi_condition and touch_ema21 and close_below_ema50:
                sl_distance = candle["atr"] * self.sl_atr_mult
                stop_loss = candle["close"] + sl_distance
                take_profit = candle["close"] - (sl_distance * self.rr_ratio)
                
                logger.debug(f"[{candle['timestamp']}] Сигнал SELL. Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
                return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}

        return {"signal": "NONE"}

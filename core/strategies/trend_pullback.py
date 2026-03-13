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
    3. Откат: RSI < порог (Перепроданность на откате)
    4. Триггер: Цена (Low) касается или пробивает EMA_21 вниз.
    
    Выход:
    1. SL: 1.5 * ATR (от Low свечи входа)
    2. TP: Фиксированный 2 R (Risk/Reward 1:2)
    """
    def __init__(self, rsi_threshold: int = 40, sl_atr_mult: float = 1.5, rr_ratio: float = 2.5):
        super().__init__("Trend_Pullback_Strategy")
        self.rsi_threshold = rsi_threshold
        self.sl_atr_mult = sl_atr_mult
        self.rr_ratio = rr_ratio

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предрасчет всех индикаторов перед прогоном стратегии."""
        return FeatureEngineer.process_all_features(df)

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """Расчет сигнала на каждой закрытой свече."""
        
        if current_idx < 1:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        prev_candle = df.iloc[current_idx - 1]
        
        # Проверяем условия тренда (на закрытой свече)
        ema50_above_200 = candle["ema_50"] > candle["ema_200"]
        close_above_200 = candle["close"] > candle["ema_200"]
        ema21_above_50 = candle["ema_21"] > candle["ema_50"]
        
        uptrend_confirmed = ema50_above_200 and close_above_200 and ema21_above_50
        
        ema50_below_200 = candle["ema_50"] < candle["ema_200"]
        close_below_200 = candle["close"] < candle["ema_200"]
        ema21_below_50 = candle["ema_21"] < candle["ema_50"]
        
        downtrend_confirmed = ema50_below_200 and close_below_200 and ema21_below_50

        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        if uptrend_confirmed:
            rsi_condition = candle["rsi"] <= self.rsi_threshold or prev_candle["rsi"] <= self.rsi_threshold
            touch_ema21 = candle["low"] <= candle["ema_21"]
            close_above_ema50 = candle["close"] > candle["ema_50"]
            
            if rsi_condition and touch_ema21 and close_above_ema50:
                sl_distance = candle["atr"] * self.sl_atr_mult
                stop_loss = candle["close"] - sl_distance
                take_profit = candle["close"] + (sl_distance * self.rr_ratio)
                
                logger.info(f"SIGNAL BUY. Close={candle['close']:.2f}, RSI={candle['rsi']:.1f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
                return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}
            else:
                # Диагностика: почему не вошли
                reasons = []
                if not rsi_condition:
                    reasons.append(f"RSI={candle['rsi']:.1f}>{self.rsi_threshold}")
                if not touch_ema21:
                    reasons.append(f"Low={candle['low']:.2f}>EMA21={candle['ema_21']:.2f}")
                if not close_above_ema50:
                    reasons.append(f"Close={candle['close']:.2f}<EMA50={candle['ema_50']:.2f}")
                logger.debug(f"Uptrend OK, но нет входа LONG: {', '.join(reasons)}")

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        if downtrend_confirmed:
            short_rsi_threshold = 100 - self.rsi_threshold
            rsi_condition = candle["rsi"] >= short_rsi_threshold or prev_candle["rsi"] >= short_rsi_threshold
            touch_ema21 = candle["high"] >= candle["ema_21"]
            close_below_ema50 = candle["close"] < candle["ema_50"]
            
            if rsi_condition and touch_ema21 and close_below_ema50:
                sl_distance = candle["atr"] * self.sl_atr_mult
                stop_loss = candle["close"] + sl_distance
                take_profit = candle["close"] - (sl_distance * self.rr_ratio)
                
                logger.info(f"SIGNAL SELL. Close={candle['close']:.2f}, RSI={candle['rsi']:.1f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
                return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}
            else:
                reasons = []
                if not rsi_condition:
                    reasons.append(f"RSI={candle['rsi']:.1f}<{short_rsi_threshold}")
                if not touch_ema21:
                    reasons.append(f"High={candle['high']:.2f}<EMA21={candle['ema_21']:.2f}")
                if not close_below_ema50:
                    reasons.append(f"Close={candle['close']:.2f}>EMA50={candle['ema_50']:.2f}")
                logger.debug(f"Downtrend OK, но нет входа SHORT: {', '.join(reasons)}")
                
        # Диагностика: нет тренда
        if not uptrend_confirmed and not downtrend_confirmed:
            reasons = []
            if not ema50_above_200 and not ema50_below_200:
                reasons.append("EMA50≈EMA200(нет направления)")
            if ema50_above_200 and not close_above_200:
                reasons.append(f"Close={candle['close']:.2f}<EMA200={candle['ema_200']:.2f}")
            if ema50_above_200 and not ema21_above_50:
                reasons.append("EMA21<EMA50(нет моментума)")
            logger.debug(f"Нет тренда: {', '.join(reasons) if reasons else 'mixed signals'}")

        return {"signal": "NONE"}

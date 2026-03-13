import pandas as pd
from typing import Dict, Any

from core.logger import setup_logger
from core.strategies.base import BaseStrategy
from core.features.indicators import FeatureEngineer

logger = setup_logger("volatility_breakout")

class BreakoutStrategy(BaseStrategy):
    """
    Стратегия 2 (Scalp Engine): Volatility Breakout
    Логика: Поиск сужения волатильности (консолидации) и вход на пробитии в сторону тренда
    при растущем моментуме.
    
    Правила Long:
    1. Сужение: bb_width < порога ИЛИ bb_width резко выросла (breakout из сжатия).
    2. Тренд: Учитываем направленность по ADX (di_plus > di_minus) и силу (adx > 20).
    3. Триггер: Close > bb_upper (пробитие верхней полосы).
    
    Правила Short:
    1. Сужение: bb_width < порога ИЛИ bb_width резко выросла.
    2. Тренд: Направленность (di_minus > di_plus) и сила (adx > 20).
    3. Триггер: Close < bb_lower (пробитие нижней полосы).
    
    Выход:
    1. SL: 1 * ATR.
    2. TP: 1.5 R (быстрый скальп-профит).
    """
    def __init__(self, bb_width_threshold: float = 0.05, adx_threshold: float = 20.0, sl_atr_mult: float = 1.5, rr_ratio: float = 2.5):
        super().__init__("Volatility_Breakout")
        self.bb_width_threshold = bb_width_threshold
        self.adx_threshold = adx_threshold
        self.sl_atr_mult = sl_atr_mult
        self.rr_ratio = rr_ratio

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предрасчет индикаторов."""
        return FeatureEngineer.process_all_features(df)

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """Расчет сигнала на каждой закрытой свече."""
        
        if current_idx < 2:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        prev_candle = df.iloc[current_idx - 1]
        prev_prev = df.iloc[current_idx - 2]
        
        # 1. Проверяем условие сжатия или выхода из сжатия
        # Вариант A: Предыдущая свеча была в сжатии (классический)
        squeeze_condition = prev_candle["bb_width"] < self.bb_width_threshold
        
        # Вариант B: Выход из сжатия — BB Width резко расширилась
        # (предпредыдущая была в сжатии, а текущая расширилась = пробой)
        expansion_from_squeeze = (
            prev_prev["bb_width"] < self.bb_width_threshold and 
            candle["bb_width"] > self.bb_width_threshold
        )
        
        entry_condition = squeeze_condition or expansion_from_squeeze
        
        if not entry_condition:
            logger.debug(
                f"Нет сжатия/расширения: BBW={prev_candle['bb_width']:.4f} "
                f"(порог {self.bb_width_threshold:.4f}), "
                f"prev_prev BBW={prev_prev['bb_width']:.4f}"
            )
            return {"signal": "NONE"}
            
        # 2. Проверяем наличие сильного тренда по ADX на момент пробоя
        trend_strong = candle["adx"] > self.adx_threshold

        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        bullish_bias = candle["di_plus"] > candle["di_minus"] and candle["close"] > candle["ema_50"]
        breakout_up = candle["close"] > candle["bb_upper"]
        
        if bullish_bias and trend_strong and breakout_up:
            sl_distance = candle["atr"] * self.sl_atr_mult
            stop_loss = candle["close"] - sl_distance
            take_profit = candle["close"] + (sl_distance * self.rr_ratio)
            
            trigger = "squeeze" if squeeze_condition else "expansion"
            logger.info(f"SIGNAL BUY (Breakout/{trigger}). Close={candle['close']:.2f}, BBW={candle['bb_width']:.4f}, ADX={candle['adx']:.1f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        bearish_bias = candle["di_minus"] > candle["di_plus"] and candle["close"] < candle["ema_50"]
        breakout_down = candle["close"] < candle["bb_lower"]
        
        if bearish_bias and trend_strong and breakout_down:
            sl_distance = candle["atr"] * self.sl_atr_mult
            stop_loss = candle["close"] + sl_distance
            take_profit = candle["close"] - (sl_distance * self.rr_ratio)
            
            trigger = "squeeze" if squeeze_condition else "expansion"
            logger.info(f"SIGNAL SELL (Breakout/{trigger}). Close={candle['close']:.2f}, BBW={candle['bb_width']:.4f}, ADX={candle['adx']:.1f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}

        # Диагностика
        reasons = []
        if not trend_strong:
            reasons.append(f"ADX={candle['adx']:.1f}<{self.adx_threshold}")
        if not breakout_up and not breakout_down:
            reasons.append(f"Нет пробоя: BB[{candle['bb_lower']:.2f}-{candle['bb_upper']:.2f}], Close={candle['close']:.2f}")
        if breakout_up and not bullish_bias:
            reasons.append(f"Пробой вверх, но нет бычьего смещения (DI+={candle['di_plus']:.1f}<DI-={candle['di_minus']:.1f})")
        if breakout_down and not bearish_bias:
            reasons.append(f"Пробой вниз, но нет медвежьего смещения")
        logger.debug(f"Сжатие есть, но нет входа: {', '.join(reasons)}")

        return {"signal": "NONE"}

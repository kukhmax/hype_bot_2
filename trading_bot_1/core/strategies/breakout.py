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
    1. Сужение: bb_width (ширина полос Боллинджера) < порога (например, 0.05).
    2. Тренд: Учитываем направленность по ADX (di_plus > di_minus) и силу (adx > 20).
    3. Триггер: Close > bb_upper (пробитие верхней полосы).
    
    Правила Short:
    1. Сужение: bb_width < порога.
    2. Тренд: Направленность (di_minus > di_plus) и сила (adx > 20).
    3. Триггер: Close < bb_lower (пробитие нижней полосы).
    
    Выход:
    1. SL: За противоположную полосу (Mid или Lower для Long) или фиксированный 1*ATR. Мы используем 1ATR.
    2. TP: 1.5 R (быстрый скальп-профит).
    """
    def __init__(self, bb_width_threshold: float = 0.05, adx_threshold: float = 20.0, sl_atr_mult: float = 1.0, rr_ratio: float = 1.5):
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
        
        if current_idx < 1:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        prev_candle = df.iloc[current_idx - 1]
        
        # 1. Проверяем условие сжатия (на предыдущей свече была узкая горловина)
        # Сужение мы измеряли как (Upper - Lower) / Mid. Чем меньше, тем сильнее сжатие.
        squeeze_condition = prev_candle["bb_width"] < self.bb_width_threshold
        
        # Если сжатия не было, нет смысла проверять пробой
        if not squeeze_condition:
            return {"signal": "NONE"}
            
        # 2. Проверяем наличие сильного тренда по ADX на момент пробоя
        trend_strong = candle["adx"] > self.adx_threshold

        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        bullish_bias = candle["di_plus"] > candle["di_minus"] and candle["close"] > candle["ema_50"]
        # Пробой верхней ленты
        breakout_up = candle["close"] > candle["bb_upper"]
        
        if bullish_bias and trend_strong and breakout_up:
            sl_distance = candle["atr"] * self.sl_atr_mult
            # SL можно ставить за середину пробойной свечи, либо фиксировано по ATR
            stop_loss = candle["close"] - sl_distance
            take_profit = candle["close"] + (sl_distance * self.rr_ratio)
            
            logger.debug(f"[{candle['timestamp']}] Сигнал BUY (Breakout). Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        bearish_bias = candle["di_minus"] > candle["di_plus"] and candle["close"] < candle["ema_50"]
        # Пробой нижней ленты
        breakout_down = candle["close"] < candle["bb_lower"]
        
        if bearish_bias and trend_strong and breakout_down:
            sl_distance = candle["atr"] * self.sl_atr_mult
            stop_loss = candle["close"] + sl_distance
            take_profit = candle["close"] - (sl_distance * self.rr_ratio)
            
            logger.debug(f"[{candle['timestamp']}] Сигнал SELL (Breakout). Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}

        return {"signal": "NONE"}

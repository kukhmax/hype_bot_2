import numpy as np
from typing import Dict, Optional, Tuple
from ..utils.indicators import calculate_ema, calculate_adx

class EMAStrategy:
    def __init__(self, ema_period: int = 20, adx_threshold: float = 20):
        self.ema_period = ema_period
        self.adx_threshold = adx_threshold
        self.candles_needed = ema_period + 14 + 10  # EMA + ADX + запас
    
    def check_long_setup(self, 
                         highs: np.array, 
                         lows: np.array, 
                         closes: np.array,
                         timestamps: np.array) -> Tuple[bool, Optional[Dict]]:
        """
        Проверка LONG сетапа:
        1. Цена закрытия > EMA20(High)
        2. ADX > 20
        3. Цена > предыдущего максимума, который выше EMA20(High)
        """
        if len(closes) < self.candles_needed:
            return False, None
        
        # Рассчет индикаторов
        ema_high = calculate_ema(highs, self.ema_period)
        adx, plus_di, minus_di = calculate_adx(highs, lows, closes)
        
        # Текущие значения
        current_close = closes[-1]
        current_ema_high = ema_high[-1]
        prev_high = max(highs[-5:-1])  # предыдущий максимум (исключая текущую свечу)
        
        # Проверка условий
        condition1 = current_close > current_ema_high
        condition2 = adx > self.adx_threshold and plus_di > minus_di
        condition3 = prev_high > current_ema_high and current_close > prev_high
        
        if condition1 and condition2 and condition3:
            return True, {
                'side': 'LONG',
                'entry': current_close,
                'ema_high': current_ema_high,
                'prev_high': prev_high,
                'adx': adx,
                'plus_di': plus_di,
                'minus_di': minus_di
            }
        
        return False, None
    
    def check_short_setup(self,
                          highs: np.array,
                          lows: np.array,
                          closes: np.array,
                          timestamps: np.array) -> Tuple[bool, Optional[Dict]]:
        """
        Проверка SHORT сетапа:
        1. Цена закрытия < EMA20(Low)
        2. ADX > 20
        3. Цена < предыдущего минимума, который ниже EMA20(Low)
        """
        if len(closes) < self.candles_needed:
            return False, None
        
        # Рассчет индикаторов
        ema_low = calculate_ema(lows, self.ema_period)
        adx, plus_di, minus_di = calculate_adx(highs, lows, closes)
        
        # Текущие значения
        current_close = closes[-1]
        current_ema_low = ema_low[-1]
        prev_low = min(lows[-5:-1])  # предыдущий минимум
        
        # Проверка условий
        condition1 = current_close < current_ema_low
        condition2 = adx > self.adx_threshold and minus_di > plus_di
        condition3 = prev_low < current_ema_low and current_close < prev_low
        
        if condition1 and condition2 and condition3:
            return True, {
                'side': 'SHORT',
                'entry': current_close,
                'ema_low': current_ema_low,
                'prev_low': prev_low,
                'adx': adx,
                'plus_di': plus_di,
                'minus_di': minus_di
            }
        
        return False, None
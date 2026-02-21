import logging
import numpy as np
from typing import Dict, Optional, Tuple
from ..utils.indicators import calculate_ema, calculate_adx

logger = logging.getLogger(__name__)

class EMAStrategy:
    def __init__(self, ema_period: int = 20, adx_threshold: float = 20):
        self.ema_period = ema_period
        self.adx_threshold = adx_threshold
        self.candles_needed = ema_period + 14 + 10  # EMA + ADX + запас
    
    def check_long_setup(self, 
                         highs: np.array, 
                         lows: np.array, 
                         closes: np.array,
                         timestamps: np.array,
                         context: Optional[str] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Проверка LONG сетапа:
        1. Цена закрытия > EMA20(High)
        2. ADX > 20
        3. Цена > предыдущего максимума, который выше EMA20(High)
        """
        label = context or "LONG"
        if len(closes) < self.candles_needed:
            logger.info(
                "EMA+ADX LONG skip [%s]: candles=%d need=%d",
                label,
                len(closes),
                self.candles_needed,
            )
            return False, None
        
        ema_high = calculate_ema(highs, self.ema_period)
        adx, plus_di, minus_di = calculate_adx(highs, lows, closes)
        
        current_close = closes[-1]
        current_ema_high = ema_high[-1]
        prev_high = max(highs[-5:-1])  # предыдущий максимум (исключая текущую свечу)
        
        condition1 = current_close >= current_ema_high
        condition2 = adx > self.adx_threshold and plus_di > minus_di
        breakout_tolerance = prev_high * 0.001
        condition3 = prev_high > current_ema_high and current_close > prev_high - breakout_tolerance
        last_ts = timestamps[-1] if len(timestamps) else None
        logger.info(
            "EMA+ADX LONG check [%s]: candles=%d ts=%s close=%.4f ema_high=%.4f prev_high=%.4f "
            "adx=%.2f plus_di=%.2f minus_di=%.2f cond1=%s cond2=%s cond3=%s",
            label,
            len(closes),
            last_ts,
            float(current_close),
            float(current_ema_high),
            float(prev_high),
            float(adx),
            float(plus_di),
            float(minus_di),
            condition1,
            condition2,
            condition3,
        )
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
        
        reasons = []
        if not condition1:
            reasons.append(
                f"close<=ema_high ({float(current_close):.4f}<={float(current_ema_high):.4f})"
            )
        if not condition2:
            reasons.append(
                f"ADX/DI fail (adx={float(adx):.2f}, +DI={float(plus_di):.2f}, -DI={float(minus_di):.2f}, thr={float(self.adx_threshold):.2f})"
            )
        if not condition3:
            reasons.append(
                f"breakout fail (prev_high={float(prev_high):.4f}, close={float(current_close):.4f})"
            )
        if reasons:
            logger.info("EMA+ADX LONG not triggered [%s]: %s", label, "; ".join(reasons))
        return False, None
    
    def check_short_setup(self,
                          highs: np.array,
                          lows: np.array,
                          closes: np.array,
                          timestamps: np.array,
                          context: Optional[str] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Проверка SHORT сетапа:
        1. Цена закрытия < EMA20(Low)
        2. ADX > 20
        3. Цена < предыдущего минимума, который ниже EMA20(Low)
        """
        label = context or "SHORT"
        if len(closes) < self.candles_needed:
            logger.info(
                "EMA+ADX SHORT skip [%s]: candles=%d need=%d",
                label,
                len(closes),
                self.candles_needed,
            )
            return False, None
        
        ema_low = calculate_ema(lows, self.ema_period)
        adx, plus_di, minus_di = calculate_adx(highs, lows, closes)
        
        current_close = closes[-1]
        current_ema_low = ema_low[-1]
        prev_low = min(lows[-5:-1])  # предыдущий минимум
        
        condition1 = current_close <= current_ema_low
        condition2 = adx > self.adx_threshold and minus_di > plus_di
        breakdown_tolerance = prev_low * 0.001
        condition3 = prev_low < current_ema_low and current_close < prev_low + breakdown_tolerance
        last_ts = timestamps[-1] if len(timestamps) else None
        logger.info(
            "EMA+ADX SHORT check [%s]: candles=%d ts=%s close=%.4f ema_low=%.4f prev_low=%.4f "
            "adx=%.2f plus_di=%.2f minus_di=%.2f cond1=%s cond2=%s cond3=%s",
            label,
            len(closes),
            last_ts,
            float(current_close),
            float(current_ema_low),
            float(prev_low),
            float(adx),
            float(plus_di),
            float(minus_di),
            condition1,
            condition2,
            condition3,
        )
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
        
        reasons = []
        if not condition1:
            reasons.append(
                f"close>=ema_low ({float(current_close):.4f}>={float(current_ema_low):.4f})"
            )
        if not condition2:
            reasons.append(
                f"ADX/DI fail (adx={float(adx):.2f}, +DI={float(plus_di):.2f}, -DI={float(minus_di):.2f}, thr={float(self.adx_threshold):.2f})"
            )
        if not condition3:
            reasons.append(
                f"breakdown fail (prev_low={float(prev_low):.4f}, close={float(current_close):.4f})"
            )
        if reasons:
            logger.info("EMA+ADX SHORT not triggered [%s]: %s", label, "; ".join(reasons))
        return False, None

import pandas as pd
from typing import Dict, Any

from core.logger import setup_logger
from core.strategies.base import BaseStrategy
from core.features.indicators import FeatureEngineer

logger = setup_logger("liquidity_sweep")

class LiquiditySweepStrategy(BaseStrategy):
    """
    Стратегия 3 (Reversal): Liquidity Sweep
    Логика: Поиск ложных пробоев локальных экстремумов (снятие ликвидности)
    с подтверждением дивергенции или фильтром перепроданности/перекупленности по RSI.
    
    Правила Long:
    1. Ищем локальный минимум за последние N свечей.
    2. Цена (Low) пробивает этот минимум (Sweep).
    3. Но цена закрытия (Close) возвращается выше пробитого уровня.
    4. Фильтр: RSI находится в зоне перепроданности (<= порог).
    
    Правила Short:
    1. Ищем локальный максимум за последние N свечей.
    2. Цена (High) перебивает этот максимум.
    3. Цена закрытия (Close) возвращается ниже пробитого уровня.
    4. Фильтр: RSI в зоне перекупленности (>= 100-порог).
    
    Выход:
    1. SL: чуть за хвостом свечи (Low - 1 ATR для лонга).
    2. TP: 2 R.
    """
    def __init__(self, lookback_period: int = 15, rsi_ob_os: int = 40, sl_atr_mult: float = 1.5, rr_ratio: float = 2.5):
        super().__init__("Liquidity_Sweep")
        self.lookback = lookback_period
        self.rsi_ob_os = rsi_ob_os
        self.sl_atr_mult = sl_atr_mult
        self.rr_ratio = rr_ratio

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предрасчет всех индикаторов и дополнительных фичей (локальные минимумы/максимумы)."""
        df = FeatureEngineer.process_all_features(df)
        
        # Считаем локальные экстремумы сдвигом (без учета текущей свечи)
        df["local_low"] = df["low"].rolling(window=self.lookback).min().shift(1)
        df["local_high"] = df["high"].rolling(window=self.lookback).max().shift(1)
        
        return df

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """Расчет сигнала на каждой закрытой свече."""
        
        if current_idx <= self.lookback:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        
        local_low = candle["local_low"]
        local_high = candle["local_high"]

        if pd.isna(local_low) or pd.isna(local_high):
            return {"signal": "NONE"}
            
        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        sweep_low = candle["low"] < local_low
        close_above_low = candle["close"] > local_low
        rsi_bullish = candle["rsi"] <= self.rsi_ob_os

        if sweep_low and close_above_low and rsi_bullish:
            sl_distance = candle["atr"] * self.sl_atr_mult
            stop_loss = candle["low"] - sl_distance
            take_profit = candle["close"] + ((candle["close"] - stop_loss) * self.rr_ratio)
            
            logger.info(
                f"SIGNAL BUY (Liq Sweep). Sweep Low={local_low:.2f}. "
                f"Close={candle['close']:.2f}, RSI={candle['rsi']:.1f}, "
                f"SL={stop_loss:.2f}, TP={take_profit:.2f}"
            )
            return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}
        
        # Диагностика LONG при частичном совпадении
        if sweep_low:
            reasons = []
            if not close_above_low:
                reasons.append(f"Close={candle['close']:.2f}<=LocalLow={local_low:.2f}")
            if not rsi_bullish:
                reasons.append(f"RSI={candle['rsi']:.1f}>{self.rsi_ob_os}")
            logger.debug(f"Sweep Low есть, но нет LONG: {', '.join(reasons)}")

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        sweep_high = candle["high"] > local_high
        close_below_high = candle["close"] < local_high
        rsi_bearish = candle["rsi"] >= (100 - self.rsi_ob_os)

        if sweep_high and close_below_high and rsi_bearish:
            sl_distance = candle["atr"] * self.sl_atr_mult
            stop_loss = candle["high"] + sl_distance
            take_profit = candle["close"] - ((stop_loss - candle["close"]) * self.rr_ratio)
            
            logger.info(
                f"SIGNAL SELL (Liq Sweep). Sweep High={local_high:.2f}. "
                f"Close={candle['close']:.2f}, RSI={candle['rsi']:.1f}, "
                f"SL={stop_loss:.2f}, TP={take_profit:.2f}"
            )
            return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}
        
        # Диагностика SHORT при частичном совпадении
        if sweep_high:
            reasons = []
            if not close_below_high:
                reasons.append(f"Close={candle['close']:.2f}>=LocalHigh={local_high:.2f}")
            if not rsi_bearish:
                reasons.append(f"RSI={candle['rsi']:.1f}<{100 - self.rsi_ob_os}")
            logger.debug(f"Sweep High есть, но нет SHORT: {', '.join(reasons)}")

        return {"signal": "NONE"}

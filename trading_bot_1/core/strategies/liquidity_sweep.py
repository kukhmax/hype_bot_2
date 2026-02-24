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
    1. Ищем локальный минимум за последние N свечей (например, 10).
    2. Цена (Low) пробивает этот минимум (Sweep).
    3. Но цена закрытия (Close) возвращается выше пробитого уровня.
    4. Фильтр: RSI находится в зоне перепроданности (<= 40) или растет после дампа.
    
    Правила Short:
    1. Ищем локальный максимум за последние N свечей.
    2. Цена (High) перебивает этот максимум.
    3. Цена закрытия (Close) возвращается ниже пробитого уровня.
    4. Фильтр: RSI в зоне перекупленности (>= 60).
    
    Выход:
    1. SL: чуть за хвостом свечи (Low - 1 ATR для лонга).
    2. TP: 2 R или возврат к скользящей (EMA50).
    """
    def __init__(self, lookback_period: int = 15, rsi_ob_os: int = 40, sl_atr_mult: float = 1.0, rr_ratio: float = 2.0):
        super().__init__("Liquidity_Sweep")
        self.lookback = lookback_period
        self.rsi_ob_os = rsi_ob_os
        self.sl_atr_mult = sl_atr_mult
        self.rr_ratio = rr_ratio

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предрасчет всех индикаторов и дополнительных фичей (локальные минимумы/максимумы)."""
        df = FeatureEngineer.process_all_features(df)
        
        # Считаем локальные экстремумы сдвигом (без учета текущей свечи)
        # rolling().min() включает текущую свечу, поэтому мы сдвигаем на 1 назад
        df["local_low"] = df["low"].rolling(window=self.lookback).min().shift(1)
        df["local_high"] = df["high"].rolling(window=self.lookback).max().shift(1)
        
        return df

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """Расчет сигнала на каждой закрытой свече."""
        
        # Нам нужно достаточно истории для локальных экстремумов
        if current_idx <= self.lookback:
            return {"signal": "NONE"}
            
        candle = df.iloc[current_idx]
        
        local_low = candle["local_low"]
        local_high = candle["local_high"]

        # Если NaN из-за окон расчета, пропускаем
        if pd.isna(local_low) or pd.isna(local_high):
            return {"signal": "NONE"}
            
        # -----------------------------------------------
        # Логика входа в LONG
        # -----------------------------------------------
        # Сняли ликвидность снизу: хвост свечи ниже локального дна
        sweep_low = candle["low"] < local_low
        # Но закрылись выше этого дна (откупили)
        close_above_low = candle["close"] > local_low
        # Подтверждение перепроданности
        rsi_bullish = candle["rsi"] <= self.rsi_ob_os

        if sweep_low and close_above_low and rsi_bullish:
            sl_distance = candle["atr"] * self.sl_atr_mult
            # Безопасный стоп за самым низом сквиза
            stop_loss = candle["low"] - sl_distance
            # 1 к 2 RR
            take_profit = candle["close"] + ((candle["close"] - stop_loss) * self.rr_ratio)
            
            logger.debug(f"[{candle['timestamp']}] Сигнал BUY (Liq Sweep). Sweep Low={local_low:.2f}. Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "BUY", "stop_loss": stop_loss, "take_profit": take_profit}

        # -----------------------------------------------
        # Логика входа в SHORT
        # -----------------------------------------------
        # Сняли ликвидность сверху: хвост свечи выше локального хая
        sweep_high = candle["high"] > local_high
        # Но закрылись ниже этого хая (продали)
        close_below_high = candle["close"] < local_high
        # Подтверждение перекупленности
        rsi_bearish = candle["rsi"] >= (100 - self.rsi_ob_os)

        if sweep_high and close_below_high and rsi_bearish:
            sl_distance = candle["atr"] * self.sl_atr_mult
            # Стоп выше самого максимума сквиза
            stop_loss = candle["high"] + sl_distance
            # 1 к 2 RR
            take_profit = candle["close"] - ((stop_loss - candle["close"]) * self.rr_ratio)
            
            logger.debug(f"[{candle['timestamp']}] Сигнал SELL (Liq Sweep). Sweep High={local_high:.2f}. Close={candle['close']:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return {"signal": "SELL", "stop_loss": stop_loss, "take_profit": take_profit}

        return {"signal": "NONE"}

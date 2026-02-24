import pandas as pd
from typing import Dict, Any

from core.strategies.base import BaseStrategy

class SimpleMAStrategy(BaseStrategy):
    """
    Простейшая тестовая стратегия на пересечении двух EMA.
    Предназначена исключительно для тестирования бэктест-движка.
    """
    def __init__(self, fast_ma: int = 10, slow_ma: int = 20):
        super().__init__("Simple_MA_Cross")
        self.fast_ma = fast_ma
        self.slow_ma = slow_ma

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Предварительный расчет индикаторов на всем датасете для скорости."""
        df = df.copy()
        df[f'ema_fast'] = df['close'].ewm(span=self.fast_ma, adjust=False).mean()
        df[f'ema_slow'] = df['close'].ewm(span=self.slow_ma, adjust=False).mean()
        return df

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """
        Проверка сигнала пересечения на закрытой свече.
        Мы смотрим на `current_idx` (наиболее свежая закрытая свеча)
        и `current_idx - 1` (предыдущая).
        """
        if current_idx < 1:
            return {"signal": "NONE"}
            
        prev_fast = df.at[current_idx - 1, 'ema_fast']
        prev_slow = df.at[current_idx - 1, 'ema_slow']
        
        curr_fast = df.at[current_idx, 'ema_fast']
        curr_slow = df.at[current_idx, 'ema_slow']
        
        # Cross UP
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            # Для простоты стоп за лоу текущей свечи
            stop_loss = df.at[current_idx, 'low'] * 0.99
            return {"signal": "BUY", "stop_loss": stop_loss}
            
        # Cross DOWN
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            stop_loss = df.at[current_idx, 'high'] * 1.01
            return {"signal": "SELL", "stop_loss": stop_loss}
            
        return {"signal": "NONE"}

import pandas as pd
from typing import Dict, Any, List

class BaseStrategy:
    """
    Базовый класс для всех торговых стратегий.
    """
    def __init__(self, name: str):
        self.name = name

    def on_ohlcv(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """
        Метод вызывается на каждой новой свече в бектесте (или в live).
        df - DataFrame со всеми свечами ДО текущей включительно.
        current_idx - индекс текущей закрывшейся свечи в df.
        
        Должен вернуть словарь с сигналом.
        Например: {"signal": "BUY", "stop_loss": 90.0, "take_profit": 110.0}
        или {"signal": "NONE"}
        """
        return {"signal": "NONE"}

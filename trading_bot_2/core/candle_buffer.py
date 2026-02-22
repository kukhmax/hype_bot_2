"""
Буфер свечей с фиксированным размером.
Хранит OHLCV как numpy arrays для быстрых вычислений.
"""
from collections import deque
from dataclasses import dataclass
import numpy as np
from typing import Optional


@dataclass
class Candle:
    timestamp: int   # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def candle_range(self) -> float:
        return self.high - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open


class CandleBuffer:
    """
    Кольцевой буфер свечей.
    buffer[0] — текущая (последняя) свеча
    buffer[-1] — предыдущая
    buffer[-N] — N свечей назад
    """

    def __init__(self, maxlen: int = 300):
        self._data: deque[Candle] = deque(maxlen=maxlen)
        self.maxlen = maxlen

    def push(self, candle: Candle) -> bool:
        """
        Добавить свечу. Если последняя свеча с тем же timestamp — обновить.
        Возвращает True если свеча закрыта (новая).
        """
        if self._data and self._data[-1].timestamp == candle.timestamp:
            self._data[-1] = candle  # обновляем незакрытую
            return candle.is_closed
        else:
            self._data.append(candle)
            return candle.is_closed

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, idx: int) -> Candle:
        """buffer[0] = последняя, buffer[-1] = предыдущая"""
        n = len(self._data)
        if idx == 0:
            return self._data[-1]
        elif idx < 0:
            real_idx = n + idx - 1  # buffer[-1] → self._data[-2]
            if real_idx < 0:
                raise IndexError(f"Index {idx} out of range (buffer size={n})")
            return self._data[real_idx]
        else:
            raise IndexError("Используй buffer[0] для текущей, buffer[-N] для N свечей назад")

    def ready(self, min_candles: int = 50) -> bool:
        return len(self._data) >= min_candles

    # ─── Numpy arrays для индикаторов ───────────────────────────────────────

    def _arr(self, field: str, n: Optional[int] = None) -> np.ndarray:
        data = list(self._data)
        if n:
            data = data[-n:]
        return np.array([getattr(c, field) for c in data], dtype=float)

    def opens(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("open", n)

    def highs(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("high", n)

    def lows(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("low", n)

    def closes(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("close", n)

    def volumes(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("volume", n)

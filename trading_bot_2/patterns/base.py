"""
Базовый класс для детекторов паттернов.
Каждый паттерн возвращает PatternResult с направлением и силой сигнала.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

from core.candle_buffer import CandleBuffer, Candle
from indicators.calculator import IndicatorSet


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class PatternResult:
    name: str
    detected: bool
    direction: Direction = Direction.NONE
    strength: float = 0.0       # 0.0 - 1.0
    description: str = ""

    @property
    def is_long(self) -> bool:
        return self.detected and self.direction == Direction.LONG

    @property
    def is_short(self) -> bool:
        return self.detected and self.direction == Direction.SHORT


class PatternDetector(ABC):
    name: str = "base"
    min_candles: int = 20

    def detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        if len(buf) < self.min_candles:
            return PatternResult(self.name, False)
        try:
            return self._detect(buf, ind)
        except Exception as e:
            return PatternResult(self.name, False, description=str(e))

    @abstractmethod
    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        ...

    # ─── Хелперы ─────────────────────────────────────────────────────────────

    @staticmethod
    def ema_series(closes: np.ndarray, period: int) -> np.ndarray:
        from indicators.calculator import ema_np
        return ema_np(closes, period)

    @staticmethod
    def atr_series(highs, lows, closes, period=14) -> np.ndarray:
        from indicators.calculator import atr_np
        return atr_np(highs, lows, closes, period)

    def c(self, buf: CandleBuffer, i: int = 0) -> Candle:
        """buf[0] = текущая, buf[-1] = предыдущая"""
        return buf[i]

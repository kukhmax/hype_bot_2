"""
strategy.py — Высокоуровневая оркестрация стратегии.

Этот модуль является "мозгом" стратегии: он принимает список свечей,
запускает расчёт индикаторов и возвращает результат проверки сетапа.
Отделён от worker.py для удобства тестирования и расширения.
"""
import logging
import numpy as np
from dataclasses import dataclass

from config import config
from core.indicators import (
    IndicatorResult,
    SetupResult,
    calculate_indicators,
    check_setup,
)

logger = logging.getLogger(__name__)


@dataclass
class StrategyContext:
    """Полный контекст стратегии для одной пары."""
    token: str
    tf: str
    candles: list[dict]
    indicators: IndicatorResult
    setup: SetupResult

    @property
    def has_signal(self) -> bool:
        return self.setup.direction != "NONE"

    @property
    def summary(self) -> str:
        """Краткое текстовое описание текущего состояния."""
        s = self.setup
        lines = [
            f"Пара:      {self.token} / {self.tf}",
            f"Свечей:    {len(self.candles)}",
            f"Цена:      {s.close_last:.4f}",
            f"EMA_High:  {s.ema_high_last:.4f}",
            f"EMA_Low:   {s.ema_low_last:.4f}",
            f"ADX:       {s.adx_value:.2f}  (+DI {s.plus_di:.2f} / -DI {s.minus_di:.2f})",
            f"PH / PL:   {s.prev_high:.4f} / {s.prev_low:.4f}",
            f"Объём x:   {s.volume_ratio:.2f}",
            f"Сигнал:    {s.direction}",
        ]
        return "\n".join(lines)


class Strategy:
    """
    EMA Channel Breakout Strategy.

    Параметры берутся из config.py, но могут быть переопределены
    при создании экземпляра для тестирования.

    Условия LONG:
      1. close > EMA20(High)
      2. ADX > threshold  AND  +DI > -DI
      3. prev_high > EMA20(High)[-2]   ← PH выше канала
      4. close > prev_high              ← пробой PH

    Условия SHORT:
      1. close < EMA20(Low)
      2. ADX > threshold  AND  -DI > +DI
      3. prev_low < EMA20(Low)[-2]     ← PL ниже канала
      4. close < prev_low               ← пробой PL
    """

    def __init__(
        self,
        ema_period: int | None = None,
        adx_period: int | None = None,
        adx_threshold: float | None = None,
        min_candles: int | None = None,
    ):
        self.ema_period = ema_period or config.EMA_PERIOD
        self.adx_period = adx_period or config.ADX_PERIOD
        self.adx_threshold = adx_threshold or config.ADX_THRESHOLD
        self.min_candles = min_candles or config.MIN_CANDLES

    def evaluate(self, token: str, tf: str, candles: list[dict]) -> StrategyContext | None:
        """
        Оценить список свечей и вернуть StrategyContext или None если данных мало.

        :param token:   Тикер, например 'ETH'
        :param tf:      Таймфрейм, например '5m'
        :param candles: Список dict с ключами o, h, l, c, v, t
        :return:        StrategyContext или None
        """
        if len(candles) < self.min_candles:
            logger.debug(
                f"{token}/{tf}: недостаточно свечей "
                f"({len(candles)}/{self.min_candles})"
            )
            return None

        try:
            highs   = np.array([c["h"] for c in candles], dtype=np.float64)
            lows    = np.array([c["l"] for c in candles], dtype=np.float64)
            closes  = np.array([c["c"] for c in candles], dtype=np.float64)
            volumes = np.array([c["v"] for c in candles], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"{token}/{tf}: ошибка парсинга свечей — {e}")
            return None

        try:
            ind = calculate_indicators(
                highs, lows, closes, volumes,
                ema_period=self.ema_period,
                adx_period=self.adx_period,
            )
            setup = check_setup(ind, adx_threshold=self.adx_threshold)
        except Exception as e:
            logger.error(f"{token}/{tf}: ошибка расчёта индикаторов — {e}")
            return None

        ctx = StrategyContext(
            token=token,
            tf=tf,
            candles=candles,
            indicators=ind,
            setup=setup,
        )

        if ctx.has_signal:
            logger.info(
                f"[СЕТАП] {token}/{tf} → {setup.direction} | "
                f"ADX={setup.adx_value:.1f} | "
                f"close={setup.close_last:.4f} | "
                f"vol={setup.volume_ratio:.2f}x"
            )

        return ctx


# Глобальный экземпляр стратегии с параметрами из конфига
strategy = Strategy()
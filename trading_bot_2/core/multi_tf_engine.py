"""
MultiTFEngine — оркестратор двух таймфреймов.

Логика:
  • 1h буфер → определяет тренд (EMA, ADX)
  • 15m буфер → ищет паттерны и точку входа

Сигнал выдаётся только если направление 15m совпадает с трендом 1h.
Это исключает контртрендовые сделки и сильно снижает ложные сигналы.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from core.candle_buffer import CandleBuffer
from core.data_feed import DataFeed
from core.signal_engine import SignalEngine, TradeSignal
from indicators.calculator import calculate_indicators, IndicatorSet
from patterns.base import Direction
from config import config
from utils.logger import logger


@dataclass
class MTFContext:
    """Контекст мультитаймфреймового анализа для Gemini и форматтера."""
    signal: TradeSignal
    h1_trend: str          # "UP" | "DOWN" | "FLAT"
    h1_adx: float
    h1_rsi: float
    h1_ema_alignment: bool  # EMA10>EMA20>EMA50 или наоборот
    tf_agreement: bool      # 15m направление == 1h тренд
    confluence_score: int   # итоговый балл 0-100


class MultiTFEngine:
    """
    Запускает два DataFeed (15m и 1h) для одного символа на MEXC Futures.
    На каждой закрытой 15m свече:
      1. Проверяет тренд по 1h буферу
      2. Запускает SignalEngine на 15m
      3. Если направления совпадают — формирует MTFContext и вызывает on_signal
    """

    def __init__(
        self,
        symbol: str,
        entry_tf: str,
        trend_tf: str,
        on_signal: Callable[[MTFContext], Awaitable[None]],
    ):
        self.symbol = symbol
        self.entry_tf = entry_tf
        self.trend_tf = trend_tf
        self.on_signal = on_signal

        # Буферы
        self.buf_entry = CandleBuffer(maxlen=300)
        self.buf_trend = CandleBuffer(maxlen=300)

        # SignalEngine работает только на базовом ТФ
        self.signal_engine = SignalEngine(
            symbol=symbol,
            timeframe=entry_tf,
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
        )

        # DataFeed × 2
        self._feed_entry = DataFeed(
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
            symbol=symbol,
            tf=entry_tf,
            buffer=self.buf_entry,
            on_closed_candle=self._on_entry_closed,
        )
        self._feed_trend = DataFeed(
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
            symbol=symbol,
            tf=trend_tf,
            buffer=self.buf_trend,
            on_closed_candle=self._on_trend_closed,   # просто накапливаем
        )

        self._task_entry: Optional[asyncio.Task] = None
        self._task_trend: Optional[asyncio.Task] = None
        self._running = False

    # ─── Public ──────────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        logger.info(f"[MTF] Запуск {self.symbol}: {self.entry_tf} + {self.trend_tf}")
        self._task_trend = asyncio.create_task(self._feed_trend.start())
        # Небольшая задержка чтобы трендовая история успела загрузиться
        await asyncio.sleep(3)
        self._task_entry = asyncio.create_task(self._feed_entry.start())
        # Ждём оба таска
        await asyncio.gather(self._task_entry, self._task_trend, return_exceptions=True)

    async def stop(self):
        self._running = False
        await self._feed_entry.stop()
        await self._feed_trend.stop()
        for t in (self._task_entry, self._task_trend):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        logger.info("[MTF] Остановлен")

    # ─── Callbacks ───────────────────────────────────────────────────────────

    async def _on_trend_closed(self, buf: CandleBuffer):
        """Просто логируем — трендовые свечи накапливаются в буфере."""
        if buf.ready(20):
            ind = calculate_indicators(buf)
            trend = "UP" if ind.trend_up else "DOWN" if ind.trend_down else "FLAT"
            logger.debug(f"[MTF {self.trend_tf}] Тренд={trend} ADX={ind.adx:.1f} RSI={ind.rsi:.1f}")

    async def _on_entry_closed(self, buf_entry: CandleBuffer):
        """Основная логика: паттерны входа + фильтр тренда."""
        if not self._running:
            return

        # Трендовый буфер должен быть готов
        if not self.buf_trend.ready(30):
            logger.debug(f"[MTF] {self.trend_tf} буфер ещё не готов, пропуск")
            return

        # Получаем индикаторы тренда
        ind_trend = calculate_indicators(self.buf_trend)
        h1_trend = "UP" if ind_trend.trend_up else "DOWN" if ind_trend.trend_down else "FLAT"

        # Запускаем анализ на ТФ входа
        signal = await self.signal_engine.analyze(buf_entry)
        if not signal:
            return

        # Проверяем согласование таймфреймов
        tf_agreement = (
            (signal.direction == Direction.LONG and h1_trend == "UP") or
            (signal.direction == Direction.SHORT and h1_trend == "DOWN")
        )

        if not tf_agreement:
            logger.info(
                f"[MTF] ❌ Несогласованность TF: сигнал={signal.direction.value}, "
                f"{self.trend_tf} тренд={h1_trend} — пропуск"
            )
            return

        # Вычисляем итоговый confluence score
        confluence = self._calc_confluence(signal, ind_trend, tf_agreement)

        ctx = MTFContext(
            signal=signal,
            h1_trend=h1_trend,
            h1_adx=ind_trend.adx,
            h1_rsi=ind_trend.rsi,
            h1_ema_alignment=(ind_trend.trend_up or ind_trend.trend_down),
            tf_agreement=tf_agreement,
            confluence_score=confluence,
        )

        logger.info(
            f"[MTF] ✅ Confluence сигнал: {signal.direction.value} {self.symbol} "
            f"confluence={confluence}% {self.trend_tf}={h1_trend}"
        )
        await self.on_signal(ctx)

    def _calc_confluence(
        self, signal: TradeSignal, ind_1h: IndicatorSet, tf_agreement: bool
    ) -> int:
        """
        Итоговый балл согласованности (0-100):
        - AI confidence (из Gemini): вес 40%
        - Паттерны: вес 25%
        - 1h индикаторы: вес 20%
        - Согласование TF: вес 15%
        """
        score_ai = signal.confidence * 0.40

        # Паттерны: кол-во × средняя сила
        avg_strength = (
            sum(p.strength for p in signal.patterns) / len(signal.patterns)
            if signal.patterns else 0
        )
        pattern_count_bonus = min(len(signal.patterns) / 6, 1.0)
        score_patterns = (avg_strength * 0.6 + pattern_count_bonus * 0.4) * 100 * 0.25

        # 1h индикаторы
        ind_score = 0
        is_long = signal.direction == Direction.LONG
        if ind_1h.adx_strong:
            ind_score += 40
        elif ind_1h.adx_trending:
            ind_score += 20
        if is_long and ind_1h.plus_di > ind_1h.minus_di:
            ind_score += 30
        elif not is_long and ind_1h.minus_di > ind_1h.plus_di:
            ind_score += 30
        if is_long and 40 <= ind_1h.rsi <= 65:
            ind_score += 30
        elif not is_long and 35 <= ind_1h.rsi <= 60:
            ind_score += 30
        score_1h = min(ind_score, 100) * 0.20

        # TF согласование
        score_tf = 100 * 0.15 if tf_agreement else 0

        total = int(score_ai + score_patterns + score_1h + score_tf)
        return min(total, 100)

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
        on_signal: Callable[[MTFContext], Awaitable[None]],
    ):
        self.symbol = symbol
        self.on_signal = on_signal

        # Буферы
        self.buf_15m = CandleBuffer(maxlen=300)
        self.buf_1h = CandleBuffer(maxlen=300)

        # SignalEngine работает только на 15m
        self.engine_15m = SignalEngine(
            symbol=symbol,
            timeframe=config.ENTRY_TIMEFRAME,
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
        )

        # DataFeed × 2
        self._feed_15m = DataFeed(
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
            symbol=symbol,
            tf=config.ENTRY_TIMEFRAME,
            buffer=self.buf_15m,
            on_closed_candle=self._on_15m_closed,
        )
        self._feed_1h = DataFeed(
            exchange=config.DEFAULT_EXCHANGE,
            market=config.DEFAULT_MARKET_TYPE,
            symbol=symbol,
            tf=config.TREND_TIMEFRAME,
            buffer=self.buf_1h,
            on_closed_candle=self._on_1h_closed,   # просто накапливаем
        )

        self._task_15m: Optional[asyncio.Task] = None
        self._task_1h: Optional[asyncio.Task] = None
        self._running = False

    # ─── Public ──────────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        logger.info(f"[MTF] Запуск {self.symbol}: {config.ENTRY_TIMEFRAME} + {config.TREND_TIMEFRAME}")
        self._task_1h = asyncio.create_task(self._feed_1h.start())
        # Небольшая задержка чтобы 1h история успела загрузиться
        await asyncio.sleep(3)
        self._task_15m = asyncio.create_task(self._feed_15m.start())
        # Ждём оба таска
        await asyncio.gather(self._task_15m, self._task_1h, return_exceptions=True)

    async def stop(self):
        self._running = False
        await self._feed_15m.stop()
        await self._feed_1h.stop()
        for t in (self._task_15m, self._task_1h):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        logger.info("[MTF] Остановлен")

    # ─── Callbacks ───────────────────────────────────────────────────────────

    async def _on_1h_closed(self, buf: CandleBuffer):
        """Просто логируем — 1h свечи накапливаются в буфере."""
        if buf.ready(20):
            ind = calculate_indicators(buf)
            trend = "UP" if ind.trend_up else "DOWN" if ind.trend_down else "FLAT"
            logger.debug(f"[MTF 1h] Тренд={trend} ADX={ind.adx:.1f} RSI={ind.rsi:.1f}")

    async def _on_15m_closed(self, buf_15m: CandleBuffer):
        """Основная логика: 15m паттерны + фильтр 1h тренда."""
        if not self._running:
            return

        # 1h буфер должен быть готов
        if not self.buf_1h.ready(30):
            logger.debug("[MTF] 1h буфер ещё не готов, пропуск")
            return

        # Получаем индикаторы 1h
        ind_1h = calculate_indicators(self.buf_1h)
        h1_trend = "UP" if ind_1h.trend_up else "DOWN" if ind_1h.trend_down else "FLAT"

        # Запускаем анализ на 15m
        signal = await self.engine_15m.analyze(buf_15m)
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
                f"1h тренд={h1_trend} — пропуск"
            )
            return

        # Вычисляем итоговый confluence score
        confluence = self._calc_confluence(signal, ind_1h, tf_agreement)

        ctx = MTFContext(
            signal=signal,
            h1_trend=h1_trend,
            h1_adx=ind_1h.adx,
            h1_rsi=ind_1h.rsi,
            h1_ema_alignment=(ind_1h.trend_up or ind_1h.trend_down),
            tf_agreement=tf_agreement,
            confluence_score=confluence,
        )

        logger.info(
            f"[MTF] ✅ Confluence сигнал: {signal.direction.value} {self.symbol} "
            f"confluence={confluence}% 1h={h1_trend}"
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

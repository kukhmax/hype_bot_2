"""
SignalEngine: оркестратор всей логики анализа.
На каждой закрытой свече:
1. Считает индикаторы
2. Прогоняет все паттерны
3. Если ≥ MIN_PATTERNS + подтверждение индикаторов → запрашивает Gemini
4. Возвращает TradeSignal
"""
from dataclasses import dataclass, field
from typing import Optional
import asyncio

from core.candle_buffer import CandleBuffer
from indicators.calculator import calculate_indicators, IndicatorSet
from patterns.base import PatternResult, Direction
from patterns.registry import ALL_PATTERNS
from config import config
from utils.logger import logger


@dataclass
class TradeSignal:
    symbol: str
    timeframe: str
    exchange: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: int           # 0-100%
    patterns: list[PatternResult] = field(default_factory=list)
    indicators: Optional[IndicatorSet] = None
    gemini_analysis: str = ""
    atr: float = 0.0

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.take_profit_1 - self.entry)
        return round(reward / risk, 2) if risk else 0

    @property
    def pattern_names(self) -> list[str]:
        return [p.name for p in self.patterns if p.detected]

    @property
    def direction_emoji(self) -> str:
        return "🟢" if self.direction == Direction.LONG else "🔴"


def _indicator_confirms(ind: IndicatorSet, direction: Direction) -> tuple[bool, list[str]]:
    """
    Проверка подтверждения индикаторами.
    Возвращает (подтверждает, список причин).
    """
    confirms = []
    rejects = []

    is_long = direction == Direction.LONG

    # ADX — сила тренда
    if ind.adx_trending:
        if is_long and ind.plus_di > ind.minus_di:
            confirms.append(f"ADX {ind.adx:.1f} (+DI>{ind.plus_di:.1f} > -DI{ind.minus_di:.1f})")
        elif not is_long and ind.minus_di > ind.plus_di:
            confirms.append(f"ADX {ind.adx:.1f} (-DI>{ind.minus_di:.1f} > +DI{ind.plus_di:.1f})")
        else:
            rejects.append(f"ADX DI не согласован")
    # При слабом ADX — нейтрально (не подтверждаем и не отклоняем)

    # RSI
    if is_long:
        if 40 <= ind.rsi <= 65:
            confirms.append(f"RSI {ind.rsi:.1f} (бычья зона)")
        elif ind.rsi > 70:
            rejects.append(f"RSI {ind.rsi:.1f} (перекуплен)")
    else:
        if 35 <= ind.rsi <= 60:
            confirms.append(f"RSI {ind.rsi:.1f} (медвежья зона)")
        elif ind.rsi < 30:
            rejects.append(f"RSI {ind.rsi:.1f} (перепродан)")

    # CCI
    if is_long:
        if ind.cci > -50:
            confirms.append(f"CCI {ind.cci:.0f} (бычий)")
        else:
            rejects.append(f"CCI {ind.cci:.0f} (слабый)")
    else:
        if ind.cci < 50:
            confirms.append(f"CCI {ind.cci:.0f} (медвежий)")
        else:
            rejects.append(f"CCI {ind.cci:.0f} (слабый)")

    # EMA тренд
    if is_long and ind.trend_up:
        confirms.append("EMA тренд ↑")
    elif not is_long and ind.trend_down:
        confirms.append("EMA тренд ↓")

    confirmed = len(confirms) >= 2 and len(rejects) <= 1
    return confirmed, confirms + [f"❌ {r}" for r in rejects]


def _calc_tp_sl(ind: IndicatorSet, direction: Direction, entry: float) -> tuple[float, float, float]:
    """Рассчитывает SL, TP1, TP2 на основе ATR"""
    atr = ind.atr
    if direction == Direction.LONG:
        sl = entry - atr
        tp1 = entry + atr * 1.5
        tp2 = entry + atr * 2.25
    else:
        sl = entry + atr
        tp1 = entry - atr * 1.5
        tp2 = entry - atr * 2.25
    return round(sl, 6), round(tp1, 6), round(tp2, 6)


class SignalEngine:
    def __init__(self, symbol: str, timeframe: str, exchange: str, market: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange = exchange
        self.market = market
        self._gemini = None
        self._last_signal_ts: int = 0
        self._cooldown_sec = 60 * 5  # не слать сигналы чаще раз в 5 минут

    def _get_gemini(self):
        if self._gemini is None:
            from ai.gemini_client import GeminiClient
            self._gemini = GeminiClient()
        return self._gemini

    async def analyze(self, buf: CandleBuffer) -> Optional[TradeSignal]:
        try:
            return await self._analyze(buf)
        except Exception as e:
            logger.error(f"[SignalEngine] Ошибка анализа: {e}")
            return None

    async def _analyze(self, buf: CandleBuffer) -> Optional[TradeSignal]:
        # Кулдаун
        cur_ts = buf[0].timestamp
        if cur_ts - self._last_signal_ts < self._cooldown_sec * 1000:
            return None

        # 1. Индикаторы
        ind = calculate_indicators(buf)
        logger.debug(f"[Engine] ADX={ind.adx:.1f} RSI={ind.rsi:.1f} CCI={ind.cci:.0f} "
                     f"ATR={ind.atr:.4f} EMA10={ind.ema10:.4f}")

        # 2. Паттерны
        results = []
        logger.debug(f"[Engine] ({self.symbol} {self.timeframe}) Поиск паттернов...")
        for detector in ALL_PATTERNS:
            # logger.debug(f"[Engine] ({self.symbol}) Проверка паттерна: {detector.__class__.__name__}")
            r = detector.detect(buf, ind)
            if r.detected:
                results.append(r)
                logger.debug(f"[Pattern] ✅ Найден {r.name} ({r.direction.value}) str={r.strength:.2f}")

        if not results:
            logger.debug(f"[Engine] ({self.symbol} {self.timeframe}) Паттерны не найдены")
            return None

        # 3. Голосование по направлению
        long_results = [r for r in results if r.direction == Direction.LONG]
        short_results = [r for r in results if r.direction == Direction.SHORT]

        direction = Direction.LONG if len(long_results) >= len(short_results) else Direction.SHORT
        active = long_results if direction == Direction.LONG else short_results

        if len(active) < config.MIN_PATTERNS_TO_SIGNAL:
            logger.debug(f"[Engine] Паттернов {len(active)} < {config.MIN_PATTERNS_TO_SIGNAL}, пропуск")
            return None

        # 4. Подтверждение индикаторами
        confirmed, ind_reasons = _indicator_confirms(ind, direction)
        if not confirmed:
            logger.debug(f"[Engine] Индикаторы не подтвердили: {ind_reasons}")
            return None

        logger.info(f"[Engine] 🎯 Сетап: {direction.value}, паттернов={len(active)}, {ind_reasons}")

        # 5. Рассчитываем вход/выход
        entry = ind.close
        sl, tp1, tp2 = _calc_tp_sl(ind, direction, entry)

        # 6. Запрос к Gemini
        avg_strength = sum(r.strength for r in active) / len(active)
        pattern_score = min(100, int(avg_strength * 60 + len(active) * 8))

        gemini_text = ""
        gemini_confidence = pattern_score

        try:
            gemini = self._get_gemini()
            gemini_result = await gemini.analyze_signal(
                symbol=self.symbol,
                timeframe=self.timeframe,
                direction=direction.value,
                patterns=active,
                indicators=ind,
                entry=entry,
                stop_loss=sl,
                take_profit=tp1,
            )
            gemini_text = gemini_result.get("analysis", "")
            gemini_confidence = gemini_result.get("confidence", pattern_score)
        except Exception as e:
            logger.warning(f"[Engine] Gemini недоступен: {e}")
            gemini_text = "AI-анализ недоступен"

        if gemini_confidence < config.MIN_CONFIDENCE:
            logger.info(f"[Engine] Уверенность Gemini {gemini_confidence}% < {config.MIN_CONFIDENCE}%, пропуск")
            return None

        self._last_signal_ts = cur_ts

        signal = TradeSignal(
            symbol=self.symbol,
            timeframe=self.timeframe,
            exchange=self.exchange,
            direction=direction,
            entry=round(entry, 6),
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=gemini_confidence,
            patterns=active,
            indicators=ind,
            gemini_analysis=gemini_text,
            atr=ind.atr,
        )

        logger.info(f"[Engine] ✅ Сигнал: {direction.value} {self.symbol} "
                    f"entry={entry:.4f} sl={sl:.4f} tp={tp1:.4f} conf={gemini_confidence}%")
        return signal

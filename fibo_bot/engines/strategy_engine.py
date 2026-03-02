"""
Fibo Bot — Strategy Engine.

Детерминистическая стратегия на базе Elliott Wave:
1. RegimeClassifier — определение режима (TREND_UP / TREND_DOWN / RANGE)
2. Wave1Detector — обнаружение импульса ≥ 2.5 ATR
3. Wave2FibZone — проверка отката в зону 0.5–0.618 Fibonacci
4. StructureBreakDetector — пробой структуры (entry trigger)
5. SignalGenerator — формирование сделки (Entry / Stop / TP1-TP3)

Все компоненты работают на CandleBuffer + FeatureResult.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

import numpy as np

from config import config
from engines.market_engine import CandleBuffer, Candle
from engines.feature_engine import FeatureEngine, FeatureResult, compute_atr, compute_ema
from utils.logger import get_logger

logger = get_logger("strategy_engine")


# ─── Enums ───────────────────────────────────────────────────────────────────

class Regime(Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SetupStage(Enum):
    """Этапы поиска сетапа."""
    IDLE = "IDLE"                   # ожидание
    WAVE1_DETECTED = "WAVE1"        # импульс найден
    WAVE2_IN_ZONE = "WAVE2_ZONE"    # откат в зону Fibonacci
    STRUCTURE_BREAK = "BREAK"       # пробой структуры → вход


# ─── Signal dataclass ────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    """Торговый сигнал."""
    symbol: str = ""
    timeframe: str = ""
    direction: str = ""          # LONG / SHORT
    regime: str = ""             # TREND_UP / TREND_DOWN / RANGE

    # Setup info
    setup: str = ""              # "Wave 2 → Wave 3"
    wave1_start: float = 0.0
    wave1_end: float = 0.0
    wave1_length_atr: float = 0.0
    retracement_depth: float = 0.0

    # Levels
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    risk_reward: float = 0.0

    # Features
    probability: float = 0.0     # ML (будет в шаге 8)
    confidence: str = "MEDIUM"   # HIGH / MEDIUM / LOW
    vwap_alignment: bool = False
    order_flow_info: str = ""
    htf_bias: str = ""
    explanation: str = ""

    # Meta
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "regime": self.regime,
            "setup": self.setup,
            "wave1_start": self.wave1_start,
            "wave1_end": self.wave1_end,
            "wave1_length_atr": self.wave1_length_atr,
            "retracement_depth": self.retracement_depth,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "risk_reward": self.risk_reward,
            "probability": self.probability,
            "confidence": self.confidence,
            "vwap_alignment": self.vwap_alignment,
            "order_flow_info": self.order_flow_info,
            "htf_bias": self.htf_bias,
            "explanation": self.explanation,
            "timestamp": self.timestamp,
        }


# ─── Regime Classifier ──────────────────────────────────────────────────────

class RegimeClassifier:
    """
    Определение рыночного режима.

    TREND_UP:   EMA20 > EMA50, ADX > 20, closes выше EMA50
    TREND_DOWN: EMA20 < EMA50, ADX > 20, closes ниже EMA50
    RANGE:      ADX < 20 или EMA20 ≈ EMA50
    """

    def classify(self, features: FeatureResult) -> Regime:
        adx = features.adx
        alignment = features.trend_ema_alignment

        if adx < config.trading.min_adx:
            regime = Regime.RANGE
        elif alignment == "BULLISH":
            regime = Regime.TREND_UP
        elif alignment == "BEARISH":
            regime = Regime.TREND_DOWN
        else:
            regime = Regime.RANGE

        logger.debug(
            f"[Regime] ADX={adx:.1f} EMA alignment={alignment} → {regime.value}"
        )
        return regime


# ─── Wave 1 Detector ─────────────────────────────────────────────────────────

@dataclass
class Wave1:
    """Обнаруженная волна 1 (импульс)."""
    direction: Direction
    start_idx: int          # индекс в буфере (от конца)
    end_idx: int
    start_price: float
    end_price: float
    length_atr: float       # длина в ATR
    duration: int           # количество свечей


class Wave1Detector:
    """
    Обнаружение Wave 1 = сильный импульс ≥ min_impulse_atr * ATR.

    Ищет последний значимый swing: последовательность свечей
    в одном направлении с суммарным движением >= порога.
    """

    def __init__(self, min_impulse_atr: float = 2.5, lookback: int = 50):
        self.min_impulse_atr = min_impulse_atr
        self.lookback = lookback
        logger.info(
            f"[Wave1Detector] Инициализирован: min_impulse={min_impulse_atr} ATR, "
            f"lookback={lookback}"
        )

    def detect(self, buffer: CandleBuffer, atr: float) -> Optional[Wave1]:
        """
        Поиск импульсной волны в последних N свечах.
        """
        if atr <= 0:
            return None

        n = len(buffer)
        lookback = min(self.lookback, n - 1)
        candles = buffer.to_list()

        if len(candles) < 10:
            return None

        threshold = self.min_impulse_atr * atr

        # Ищем swing highs и lows
        best_wave: Optional[Wave1] = None
        best_length = 0

        # Скользящее окно: ищем максимальный импульс
        for start in range(max(0, len(candles) - lookback), len(candles) - 5):
            for end in range(start + 3, min(start + 30, len(candles))):
                move_up = candles[end].high - candles[start].low
                move_down = candles[start].high - candles[end].low

                # Бычий импульс
                if move_up >= threshold and move_up > best_length:
                    best_length = move_up
                    length_atr = move_up / atr
                    best_wave = Wave1(
                        direction=Direction.LONG,
                        start_idx=-(len(candles) - start),
                        end_idx=-(len(candles) - end),
                        start_price=candles[start].low,
                        end_price=candles[end].high,
                        length_atr=length_atr,
                        duration=end - start,
                    )

                # Медвежий импульс
                if move_down >= threshold and move_down > best_length:
                    best_length = move_down
                    length_atr = move_down / atr
                    best_wave = Wave1(
                        direction=Direction.SHORT,
                        start_idx=-(len(candles) - start),
                        end_idx=-(len(candles) - end),
                        start_price=candles[start].high,
                        end_price=candles[end].low,
                        length_atr=length_atr,
                        duration=end - start,
                    )

        if best_wave:
            logger.info(
                f"[Wave1] ✅ Импульс обнаружен: {best_wave.direction.value} | "
                f"{best_wave.start_price:.2f} → {best_wave.end_price:.2f} | "
                f"Длина: {best_wave.length_atr:.2f} ATR | "
                f"Свечей: {best_wave.duration}"
            )
        else:
            logger.debug(
                f"[Wave1] Импульс не найден (порог: {threshold:.2f}, "
                f"ATR: {atr:.2f}, lookback: {lookback})"
            )

        return best_wave


# ─── Wave 2 Fibonacci Zone ───────────────────────────────────────────────────

@dataclass
class Wave2:
    """Зона отката Wave 2."""
    fib_low: float          # нижняя граница (0.618)
    fib_high: float         # верхняя граница (0.5)
    retracement_depth: float  # текущая глубина отката
    in_zone: bool           # цена в зоне?


class Wave2FibZone:
    """
    Проверка: откатилась ли цена в зону Fibonacci 0.5–0.618 от Wave 1.
    """

    def __init__(self, fib_low: float = 0.5, fib_high: float = 0.618):
        self.fib_low = fib_low
        self.fib_high = fib_high

    def check(self, wave1: Wave1, current_price: float) -> Wave2:
        """Проверить, находится ли цена в fib zone."""
        move = abs(wave1.end_price - wave1.start_price)

        if wave1.direction == Direction.LONG:
            # Бычий: откат вниз
            fib_high_level = wave1.end_price - move * self.fib_low
            fib_low_level = wave1.end_price - move * self.fib_high
            retracement = (wave1.end_price - current_price) / move if move > 0 else 0
            in_zone = fib_low_level <= current_price <= fib_high_level
        else:
            # Медвежий: откат вверх
            fib_high_level = wave1.end_price + move * self.fib_high
            fib_low_level = wave1.end_price + move * self.fib_low
            retracement = (current_price - wave1.end_price) / move if move > 0 else 0
            in_zone = fib_low_level <= current_price <= fib_high_level

        result = Wave2(
            fib_low=fib_low_level,
            fib_high=fib_high_level,
            retracement_depth=retracement,
            in_zone=in_zone,
        )

        if in_zone:
            logger.info(
                f"[Wave2] ✅ Цена в Fib зоне! Откат: {retracement:.3f} | "
                f"Зона: {fib_low_level:.2f} – {fib_high_level:.2f} | "
                f"Цена: {current_price:.2f}"
            )
        else:
            logger.debug(
                f"[Wave2] Цена вне зоны. Откат: {retracement:.3f} | "
                f"Зона: {fib_low_level:.2f} – {fib_high_level:.2f} | "
                f"Цена: {current_price:.2f}"
            )

        return result


# ─── Structure Break Detector ────────────────────────────────────────────────

class StructureBreakDetector:
    """
    Обнаружение пробоя структуры = entry trigger.

    Для LONG: цена пробивает последний swing high после отката.
    Для SHORT: цена пробивает последний swing low после отката.
    """

    def __init__(self, confirmation_candles: int = 2):
        self.confirmation_candles = confirmation_candles

    def check(self, buffer: CandleBuffer, wave1: Wave1,
              wave2: Wave2) -> bool:
        """
        Проверить пробой структуры.
        """
        if not wave2.in_zone:
            return False

        n = len(buffer)
        if n < 5:
            return False

        # Последние свечи
        last = buffer[0]
        prev = buffer[-1]

        if wave1.direction == Direction.LONG:
            # Ищем пробой swing high (маленький high в зоне отката)
            # Простая логика: последняя свеча закрылась выше предыдущей high
            # + свеча бычья (close > open)
            breakout = (
                last.close > prev.high
                and last.is_bull
                and last.body > last.candle_range * 0.3  # значимое тело
            )
        else:
            # SHORT: пробой swing low
            breakout = (
                last.close < prev.low
                and last.is_bear
                and last.body > last.candle_range * 0.3
            )

        if breakout:
            logger.info(
                f"[StructureBreak] ✅ ПРОБОЙ! {wave1.direction.value} | "
                f"Цена закрытия: {last.close:.2f} | "
                f"Prev {'high' if wave1.direction == Direction.LONG else 'low'}: "
                f"{prev.high if wave1.direction == Direction.LONG else prev.low:.2f}"
            )
        else:
            logger.debug(
                f"[StructureBreak] Нет пробоя. Close={last.close:.2f} "
                f"PrevH={prev.high:.2f} PrevL={prev.low:.2f}"
            )

        return breakout


# ─── Signal Generator ────────────────────────────────────────────────────────

class SignalGenerator:
    """
    Формирование полного торгового сигнала:
    - Entry zone
    - Stop Loss (ниже/выше начала Wave 1)
    - TP1 (1.0 extension), TP2 (1.272), TP3 (1.618)
    - Risk:Reward ratio
    """

    def generate(
        self,
        symbol: str,
        timeframe: str,
        wave1: Wave1,
        wave2: Wave2,
        features: FeatureResult,
        regime: Regime,
        current_price: float,
    ) -> TradeSignal:
        """Сформировать сигнал."""
        move = abs(wave1.end_price - wave1.start_price)

        signal = TradeSignal(
            symbol=symbol,
            timeframe=timeframe,
            direction=wave1.direction.value,
            regime=regime.value,
            setup="Wave 2 → Wave 3",
            wave1_start=wave1.start_price,
            wave1_end=wave1.end_price,
            wave1_length_atr=wave1.length_atr,
            retracement_depth=wave2.retracement_depth,
            timestamp=time.time(),
        )

        if wave1.direction == Direction.LONG:
            # LONG
            signal.entry_low = wave2.fib_low
            signal.entry_high = wave2.fib_high
            signal.stop_loss = wave1.start_price - features.atr * 0.5
            signal.tp1 = wave1.end_price + move * 0.0     # начало Wave 1 end
            signal.tp1 = wave1.end_price                   # return to wave1 high
            signal.tp2 = wave1.end_price + move * 0.272    # 1.272 extension
            signal.tp3 = wave1.end_price + move * 0.618    # 1.618 extension

        else:
            # SHORT
            signal.entry_low = wave2.fib_high   # для SHORT low = верхняя граница
            signal.entry_high = wave2.fib_low
            signal.stop_loss = wave1.start_price + features.atr * 0.5
            signal.tp1 = wave1.end_price
            signal.tp2 = wave1.end_price - move * 0.272
            signal.tp3 = wave1.end_price - move * 0.618

        # Risk:Reward
        if wave1.direction == Direction.LONG:
            risk = current_price - signal.stop_loss
            reward = signal.tp3 - current_price
        else:
            risk = signal.stop_loss - current_price
            reward = current_price - signal.tp3

        signal.risk_reward = round(reward / risk, 2) if risk > 0 else 0

        # VWAP alignment
        signal.vwap_alignment = (
            (wave1.direction == Direction.LONG and features.distance_to_vwap > 0)
            or (wave1.direction == Direction.SHORT and features.distance_to_vwap < 0)
        )

        # HTF bias
        signal.htf_bias = features.trend_ema_alignment

        # Confidence
        if signal.risk_reward >= 2.5 and signal.vwap_alignment:
            signal.confidence = "HIGH"
        elif signal.risk_reward >= 1.5:
            signal.confidence = "MEDIUM"
        else:
            signal.confidence = "LOW"

        # Explanation
        explanations = []
        explanations.append(f"Импульс {wave1.length_atr:.1f} ATR.")
        explanations.append(f"Откат {wave2.retracement_depth:.2f} Fibonacci.")
        if signal.vwap_alignment:
            explanations.append(
                f"Цена {'выше' if wave1.direction == Direction.LONG else 'ниже'} "
                f"VWAP ({features.distance_to_vwap:.2f}%)."
            )
        explanations.append(f"RSI: {features.rsi:.1f}.")
        explanations.append(f"ADX: {features.adx:.1f} ({regime.value}).")
        if features.volatility_expansion:
            explanations.append("Волатильность расширяется.")
        explanations.append(f"Сессия: {features.session}.")
        signal.explanation = "\n".join(explanations)

        logger.info(
            f"[Signal] 📈 СИГНАЛ СФОРМИРОВАН: {signal.direction} {signal.symbol} | "
            f"TF: {signal.timeframe} | "
            f"Entry: {signal.entry_low:.2f} – {signal.entry_high:.2f} | "
            f"Stop: {signal.stop_loss:.2f} | "
            f"TP1: {signal.tp1:.2f} TP2: {signal.tp2:.2f} TP3: {signal.tp3:.2f} | "
            f"R:R = {signal.risk_reward} | "
            f"Confidence: {signal.confidence}"
        )
        logger.info(f"[Signal] Объяснение:\n{signal.explanation}")

        return signal


# ─── Strategy Engine (главный класс) ─────────────────────────────────────────

class StrategyEngine:
    """
    Объединяет все компоненты стратегии.

    Pipeline на каждую закрытую свечу:
    1. Вычислить фичи (FeatureEngine)
    2. Определить режим (RegimeClassifier)
    3. Найти Wave 1 (Wave1Detector)
    4. Проверить Wave 2 зону (Wave2FibZone)
    5. Проверить пробой структуры (StructureBreakDetector)
    6. Сформировать сигнал (SignalGenerator)
    """

    def __init__(self):
        self.feature_engine = FeatureEngine(
            atr_period=int(config.trading.min_adx),
        )
        self.regime_classifier = RegimeClassifier()
        self.wave1_detector = Wave1Detector(
            min_impulse_atr=config.trading.min_impulse_atr,
        )
        self.wave2_fib = Wave2FibZone(
            fib_low=config.trading.fib_zone_low,
            fib_high=config.trading.fib_zone_high,
        )
        self.structure_break = StructureBreakDetector()
        self.signal_generator = SignalGenerator()

        self._signals_generated = 0
        self._analyses_count = 0

        logger.info(
            "[StrategyEngine] ✅ Инициализирован | "
            f"min_impulse={config.trading.min_impulse_atr} ATR | "
            f"Fib zone: {config.trading.fib_zone_low}–{config.trading.fib_zone_high} | "
            f"Extension target: {config.trading.fib_extension_target}"
        )

    async def analyze(
        self,
        symbol: str,
        timeframe: str,
        buffer: CandleBuffer,
    ) -> Optional[TradeSignal]:
        """
        Анализ текущего состояния рынка.
        Возвращает TradeSignal или None.
        """
        start_time = time.time()
        self._analyses_count += 1

        logger.info(
            f"[Strategy] ─── Анализ #{self._analyses_count}: "
            f"{symbol} {timeframe} ({len(buffer)} свечей) ───"
        )

        # 1. Фичи
        features = self.feature_engine.compute(buffer)
        if features is None:
            logger.warning("[Strategy] Фичи не вычислены — пропуск")
            return None

        # 2. Режим
        regime = self.regime_classifier.classify(features)
        logger.info(f"[Strategy] Режим: {regime.value}")

        if regime == Regime.RANGE:
            logger.info("[Strategy] Режим RANGE — сигнал не генерируется")
            return None

        # 3. Wave 1
        wave1 = self.wave1_detector.detect(buffer, features.atr)
        if wave1 is None:
            logger.info("[Strategy] Wave 1 не найден — пропуск")
            return None

        # Проверка: направление Wave1 совпадает с режимом
        if regime == Regime.TREND_UP and wave1.direction != Direction.LONG:
            logger.info("[Strategy] Wave 1 SHORT в TREND_UP — пропуск")
            return None
        if regime == Regime.TREND_DOWN and wave1.direction != Direction.SHORT:
            logger.info("[Strategy] Wave 1 LONG в TREND_DOWN — пропуск")
            return None

        # 4. Wave 2 зона
        current_price = float(buffer[0].close)
        wave2 = self.wave2_fib.check(wave1, current_price)
        if not wave2.in_zone:
            logger.info("[Strategy] Цена вне Fib зоны — пропуск")
            return None

        # 5. Structure Break
        breakout = self.structure_break.check(buffer, wave1, wave2)
        if not breakout:
            logger.info("[Strategy] Нет пробоя структуры — пропуск")
            return None

        # 6. Генерация сигнала
        signal = self.signal_generator.generate(
            symbol=symbol,
            timeframe=timeframe,
            wave1=wave1,
            wave2=wave2,
            features=features,
            regime=regime,
            current_price=current_price,
        )

        # Фильтр: R:R должен быть >= 1.5
        if signal.risk_reward < 1.5:
            logger.warning(
                f"[Strategy] ⚠ Сигнал отклонён: R:R = {signal.risk_reward} < 1.5"
            )
            return None

        self._signals_generated += 1
        elapsed_ms = (time.time() - start_time) * 1000

        logger.info(
            f"[Strategy] 🎯 СИГНАЛ #{self._signals_generated} ПРИНЯТ! "
            f"({elapsed_ms:.1f}мс) | {signal.direction} {signal.symbol} {signal.timeframe}"
        )

        return signal

"""
EMA-паттерны:
1. Дриблинг на скользящей (Dribling EMA)
2. Подхват скользящей
3. Накопление на 50 MA
4. Движение под 45 градусов
"""
import numpy as np
from patterns.base import PatternDetector, PatternResult, Direction
from core.candle_buffer import CandleBuffer
from indicators.calculator import IndicatorSet


class DriblingEMA(PatternDetector):
    """
    Дриблинг на EMA: фитиль касается 10/20 EMA, но свеча закрылась выше.
    Сигнал в ЛОНГ только при восходящем тренде.
    """
    name = "Дриблинг на EMA"
    min_candles = 30

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        c1 = buf[-1]  # предыдущая закрытая
        ind_ema10 = ind.ema10
        ind_ema20 = ind.ema20

        # Тренд вверх
        if not ind.trend_up:
            return PatternResult(self.name, False)

        # Фитиль касается EMA
        touch_10 = c1.low <= ind_ema10 * 1.003
        touch_20 = c1.low <= ind_ema20 * 1.003

        if not (touch_10 or touch_20):
            return PatternResult(self.name, False)

        # Закрытие выше EMA (ключевое)
        if c1.close <= ind_ema10:
            return PatternResult(self.name, False)

        # Сила: насколько сильно закрылся выше EMA
        strength = min((c1.close - ind_ema10) / (ind.atr * 0.5), 1.0)

        return PatternResult(
            name=self.name,
            detected=True,
            direction=Direction.LONG,
            strength=round(strength, 2),
            description=f"Фитиль {'к 10 EMA' if touch_10 else 'к 20 EMA'}, закрытие выше"
        )


class CatchingMA(PatternDetector):
    """
    Подхват скользящей: откат к 10/20 EMA после импульса,
    малые свечи у EMA, затем бычье поглощение.
    """
    name = "Подхват скользящей"
    min_candles = 30

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        if not ind.trend_up:
            return PatternResult(self.name, False)

        # Большие свечи до отката (импульс)
        prev_bodies = [buf[-i].body for i in range(5, 2, -1)]
        avg_prev_body = np.mean(prev_bodies) if prev_bodies else 0
        if avg_prev_body < ind.atr * 0.4:
            return PatternResult(self.name, False)

        # Малые свечи у EMA (откат)
        small1 = buf[-2]
        small2 = buf[-1] if len(buf) >= 3 else small1
        avg_small_body = (small1.body + small2.body) / 2
        if avg_small_body > avg_prev_body * 0.5:
            return PatternResult(self.name, False)

        # Откат дошёл до EMA
        near_ema = (small1.low <= ind.ema10 * 1.008 or
                    small1.low <= ind.ema20 * 1.008)
        if not near_ema:
            return PatternResult(self.name, False)

        # Текущая свеча - бычье поглощение
        cur = buf[0]
        if not cur.is_bull:
            return PatternResult(self.name, False)
        if cur.close <= ind.ema10:
            return PatternResult(self.name, False)

        strength = min(cur.body / (ind.atr * 0.8), 1.0)
        return PatternResult(
            name=self.name,
            detected=True,
            direction=Direction.LONG,
            strength=round(strength, 2),
            description=f"Откат к EMA {ind.ema10:.4f}, поглощение вверх"
        )


class AccumulationMA50(PatternDetector):
    """
    Накопление на 50 MA: консолидация у MA50 + пробой с объёмом.
    """
    name = "Накопление на 50 MA"
    min_candles = 60

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        ma50 = ind.ema50
        atr = ind.atr

        # Цена у 50 MA
        cur = buf[0]
        if abs(cur.close - ma50) > atr * 2:
            return PatternResult(self.name, False)

        # Консолидация: 5 свечей с малым диапазоном
        ranges = [buf[-i].candle_range for i in range(1, 6)]
        avg_range = np.mean(ranges)
        if avg_range > atr * 1.2:
            return PatternResult(self.name, False)

        # Стандартное отклонение закрытий
        closes_5 = [buf[-i].close for i in range(1, 6)]
        if np.std(closes_5) > atr * 0.6:
            return PatternResult(self.name, False)

        # Пробой вверх текущей свечой
        consolidation_max = max(buf[-i].high for i in range(1, 6))
        if cur.close <= consolidation_max:
            return PatternResult(self.name, False)

        # Объём пробоя
        vols = [buf[-i].volume for i in range(1, 6)]
        avg_vol = np.mean(vols)
        vol_confirm = cur.volume > avg_vol * 1.3

        # Направление по тренду
        direction = Direction.LONG if cur.close > ma50 else Direction.SHORT
        if direction == Direction.SHORT and not ind.trend_down:
            return PatternResult(self.name, False)
        if direction == Direction.LONG and not ind.trend_up:
            return PatternResult(self.name, False)

        strength = 0.7 + (0.3 if vol_confirm else 0.0)
        return PatternResult(
            name=self.name,
            detected=True,
            direction=direction,
            strength=round(strength, 2),
            description=f"Консолидация у 50MA {ma50:.4f}, пробой {'с объёмом' if vol_confirm else ''}"
        )


class Angle45(PatternDetector):
    """
    Движение под 45 градусов: устойчивый тренд вдоль 20 EMA,
    откат к EMA → вход.
    """
    name = "Движение 45°"
    min_candles = 30

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        closes = buf.closes()
        ema20 = self.ema_series(closes, 20)
        atr = ind.atr

        # Угол подъёма EMA20 за последние 10 свечей
        if len(ema20) < 10:
            return PatternResult(self.name, False)

        slope = (ema20[-1] - ema20[-10]) / (ema20[-10] * 10)
        if not (0.003 <= abs(slope) <= 0.025):
            return PatternResult(self.name, False)

        direction = Direction.LONG if slope > 0 else Direction.SHORT

        # Последние свечи у EMA
        cur = buf[0]
        prev = buf[-1]

        if direction == Direction.LONG:
            # Лоу близко к EMA20
            near = prev.low <= ind.ema20 * 1.005
            closing_above = prev.close > ind.ema20
            bullish = cur.is_bull
            if not (near and closing_above and bullish):
                return PatternResult(self.name, False)
        else:
            near = prev.high >= ind.ema20 * 0.995
            closing_below = prev.close < ind.ema20
            bearish = cur.is_bear
            if not (near and closing_below and bearish):
                return PatternResult(self.name, False)

        strength = min(abs(slope) / 0.01, 1.0)
        return PatternResult(
            name=self.name,
            detected=True,
            direction=direction,
            strength=round(strength, 2),
            description=f"Угол EMA20: {slope*100:.3f}%/свеча, откат к EMA"
        )

"""
Волатильные и разворотные паттерны:
9.  Маленькие свечи (сжатие волатильности)
10. Рыбий крюк
11. Пинг-Понг эффект
12. Эффект уменьшения тела свечей
"""
import numpy as np
from patterns.base import PatternDetector, PatternResult, Direction
from core.candle_buffer import CandleBuffer
from indicators.calculator import IndicatorSet


class SmallCandles(PatternDetector):
    """
    Маленькие свечи после больших = сжатие волатильности.
    Направление — по тренду.
    """
    name = "Маленькие свечи (сжатие)"
    min_candles = 25

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]

        # Средний размер тела 5-10 свечей назад (прошлая волатильность)
        old_bodies = [buf[-i].body for i in range(5, 11) if i < len(buf)]
        if not old_bodies:
            return PatternResult(self.name, False)
        avg_old_body = np.mean(old_bodies)

        # Маленькие свечи последних 3-4
        small_count = sum(
            1 for i in range(1, 5)
            if i < len(buf) and buf[-i].body < avg_old_body * 0.35
        )
        if small_count < 3:
            return PatternResult(self.name, False)

        # Пробой выходом из сжатия
        consol_max = max(buf[-i].high for i in range(1, 5) if i < len(buf))
        consol_min = min(buf[-i].low for i in range(1, 5) if i < len(buf))

        if cur.close > consol_max and cur.body > avg_old_body * 0.6:
            direction = Direction.LONG if ind.trend_up else Direction.LONG  # по тренду
            strength = min(1.0, cur.body / (atr * 0.8))
            return PatternResult(
                name=self.name,
                detected=True,
                direction=Direction.LONG,
                strength=round(strength, 2),
                description=f"Взрыв вверх после {small_count} малых свечей"
            )
        if cur.close < consol_min and cur.body > avg_old_body * 0.6:
            strength = min(1.0, cur.body / (atr * 0.8))
            return PatternResult(
                name=self.name,
                detected=True,
                direction=Direction.SHORT,
                strength=round(strength, 2),
                description=f"Взрыв вниз после {small_count} малых свечей"
            )

        return PatternResult(self.name, False)


class FishHook(PatternDetector):
    """
    Рыбий крюк: сильное падение → слабый отскок (покупатели исчерпаны) → шорт.
    """
    name = "Рыбий крюк"
    min_candles = 20

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]

        # Ищем большую медвежью свечу
        for drop_i in range(4, 8):
            if drop_i >= len(buf):
                break
            drop = buf[-drop_i]
            if not (drop.is_bear and drop.body > atr * 1.3):
                continue

            # После падения: 2-4 бычьи свечи — отскок
            bounce_candles = [buf[-i] for i in range(1, drop_i) if buf[-i].is_bull]
            if len(bounce_candles) < 1:
                continue

            # Отскок слабый: максимум не достигает уровня начала падения
            bounce_max = max(c.high for c in bounce_candles)
            if bounce_max >= drop.open:
                continue

            # Тела отскока < 40% тела падения
            avg_bounce_body = np.mean([c.body for c in bounce_candles])
            if avg_bounce_body >= drop.body * 0.4:
                continue

            # Затухание (последний отскок меньше предыдущего)
            if len(bounce_candles) >= 2:
                if bounce_candles[-1].body >= bounce_candles[0].body:
                    continue

            # Текущая свеча — медвежий разворот
            if cur.is_bear and cur.close < (bounce_candles[-1].low if bounce_candles else drop.low):
                strength = min(1.0, drop.body / (atr * 2.0))
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.SHORT,
                    strength=round(strength, 2),
                    description=f"Отскок {round(avg_bounce_body / drop.body * 100)}% от падения, пробой вниз"
                )

        return PatternResult(self.name, False)


class PingPong(PatternDetector):
    """
    Пинг-Понг: нарушение восходящего паттерна резким падением,
    моментальный возврат к сопротивлению — ловушка покупателей → шорт.
    """
    name = "Пинг-Понг"
    min_candles = 25

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]
        from patterns.level_patterns import find_levels
        levels = find_levels(buf, n=40)

        # Нужна большая медвежья свеча 3-8 свечей назад
        for break_i in range(3, 9):
            if break_i >= len(buf):
                break
            breaker = buf[-break_i]
            if not (breaker.is_bear and breaker.body > atr * 1.5):
                continue

            # После нее — возврат к уровню сопротивления
            for lvl in levels:
                # Уровень выше места пробоя
                if lvl <= breaker.close:
                    continue
                # Текущая цена подошла к сопротивлению снизу
                near_resistance = (cur.high >= lvl * 0.997 and cur.close < lvl)
                if near_resistance and cur.is_bear:
                    strength = min(1.0, breaker.body / (atr * 1.5))
                    return PatternResult(
                        name=self.name,
                        detected=True,
                        direction=Direction.SHORT,
                        strength=round(strength, 2),
                        description=f"Возврат к сопротивлению {lvl:.4f} — ловушка"
                    )

        return PatternResult(self.name, False)


class BodyShrinkage(PatternDetector):
    """
    Эффект уменьшения тела свечей у уровня — затухание импульса → разворот.
    """
    name = "Уменьшение тел"
    min_candles = 20

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]

        b1 = buf[-3].body if len(buf) >= 4 else None
        b2 = buf[-2].body if len(buf) >= 3 else None
        b3 = buf[-1].body

        if b1 is None or b2 is None:
            return PatternResult(self.name, False)

        # Последовательное уменьшение тел
        if not (b1 > b2 > b3):
            return PatternResult(self.name, False)

        # Последнее тело очень маленькое
        if b3 >= atr * 0.25:
            return PatternResult(self.name, False)

        # Уменьшение значительное
        if b3 >= b1 * 0.4:
            return PatternResult(self.name, False)

        from patterns.level_patterns import find_levels
        levels = find_levels(buf, n=40)

        # Свечи у уровня
        for lvl in levels:
            prices_near = all(
                abs(buf[-i].close - lvl) < atr * 0.8
                for i in range(1, 4) if i < len(buf)
            )
            if not prices_near:
                continue

            # Разворот от уровня поддержки → ЛОНГ
            if buf[-1].close >= lvl and cur.is_bull and cur.body > b3:
                strength = min(1.0, (b1 - b3) / (atr * 0.5))
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.LONG,
                    strength=round(strength, 2),
                    description=f"Затухание у поддержки {lvl:.4f}, разворот вверх"
                )

            # Разворот от уровня сопротивления → ШОРТ
            if buf[-1].close <= lvl and cur.is_bear and cur.body > b3:
                strength = min(1.0, (b1 - b3) / (atr * 0.5))
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.SHORT,
                    strength=round(strength, 2),
                    description=f"Затухание у сопротивления {lvl:.4f}, разворот вниз"
                )

        return PatternResult(self.name, False)

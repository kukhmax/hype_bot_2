"""
Уровневые паттерны:
5. Возврат за уровень
6. Поджатие к уровню = пробой
7. Остров на гэпе
8. Откуп на негативных новостях
"""
import numpy as np
from patterns.base import PatternDetector, PatternResult, Direction
from core.candle_buffer import CandleBuffer
from indicators.calculator import IndicatorSet


def find_levels(buf: CandleBuffer, n: int = 50, tolerance: float = 0.002) -> list[float]:
    """
    Простой поиск горизонтальных уровней:
    ищем хаи/лои, которые касались цены >= 2 раз.
    """
    highs = [buf[-i].high for i in range(1, min(n, len(buf)))]
    lows = [buf[-i].low for i in range(1, min(n, len(buf)))]
    all_prices = highs + lows
    levels = []
    used = set()

    for i, p in enumerate(all_prices):
        if i in used:
            continue
        touches = [j for j, q in enumerate(all_prices) if j != i and abs(q - p) / p < tolerance]
        if len(touches) >= 2:
            cluster = [all_prices[j] for j in [i] + touches]
            lvl = np.mean(cluster)
            levels.append(lvl)
            used.update([i] + touches)

    return sorted(set(round(l, 6) for l in levels))


class ReturnToLevel(PatternDetector):
    """
    Возврат за уровень: пробой → ретест → продолжение.
    """
    name = "Возврат за уровень"
    min_candles = 40

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]
        prev = buf[-1]

        levels = find_levels(buf, n=60)
        if not levels:
            return PatternResult(self.name, False)

        for lvl in levels:
            # Пробой вверх: prev закрылась выше уровня, cur делает ретест
            if prev.close > lvl and cur.low <= lvl * 1.005 and cur.close > lvl:
                # Уровень держится как поддержка
                strength = min(1.0, (cur.close - lvl) / (atr * 0.5))
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.LONG,
                    strength=round(max(0, strength), 2),
                    description=f"Ретест уровня {lvl:.4f} сверху, держится"
                )
            # Пробой вниз: prev закрылась ниже уровня, cur делает ретест снизу
            if prev.close < lvl and cur.high >= lvl * 0.995 and cur.close < lvl:
                strength = min(1.0, (lvl - cur.close) / (atr * 0.5))
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.SHORT,
                    strength=round(max(0, strength), 2),
                    description=f"Ретест уровня {lvl:.4f} снизу, держится"
                )

        return PatternResult(self.name, False)


class CompressionBreakout(PatternDetector):
    """
    Поджатие к уровню = пробой: 3+ касания с уменьшением амплитуды.
    """
    name = "Поджатие → пробой"
    min_candles = 50

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]
        levels = find_levels(buf, n=80, tolerance=0.003)

        for lvl in levels:
            # Считаем касания снизу (для пробоя вверх)
            upper_touches = sum(
                1 for i in range(1, min(15, len(buf)))
                if abs(buf[-i].high - lvl) / lvl < 0.004
            )
            if upper_touches >= 3:
                # Уменьшение амплитуды
                recent_range = np.mean([buf[-i].candle_range for i in range(1, 4)])
                earlier_range = np.mean([buf[-i].candle_range for i in range(6, 10)])
                compression = earlier_range > 0 and recent_range < earlier_range * 0.75

                # Пробой
                if cur.close > lvl and cur.body > atr * 0.4:
                    strength = 0.6 + (0.3 if compression else 0.0) + (0.1 if upper_touches >= 5 else 0.0)
                    return PatternResult(
                        name=self.name,
                        detected=True,
                        direction=Direction.LONG,
                        strength=round(min(strength, 1.0), 2),
                        description=f"Пробой {lvl:.4f} после {upper_touches} касаний"
                    )

            # Касания сверху (для пробоя вниз)
            lower_touches = sum(
                1 for i in range(1, min(15, len(buf)))
                if abs(buf[-i].low - lvl) / lvl < 0.004
            )
            if lower_touches >= 3:
                recent_range = np.mean([buf[-i].candle_range for i in range(1, 4)])
                earlier_range = np.mean([buf[-i].candle_range for i in range(6, 10)])
                compression = earlier_range > 0 and recent_range < earlier_range * 0.75

                if cur.close < lvl and cur.body > atr * 0.4:
                    strength = 0.6 + (0.3 if compression else 0.0) + (0.1 if lower_touches >= 5 else 0.0)
                    return PatternResult(
                        name=self.name,
                        detected=True,
                        direction=Direction.SHORT,
                        strength=round(min(strength, 1.0), 2),
                        description=f"Пробой {lvl:.4f} вниз после {lower_touches} касаний"
                    )

        return PatternResult(self.name, False)


class IslandGap(PatternDetector):
    """
    Остров на гэпе: гэп → остров → заполнение.
    """
    name = "Остров на гэпе"
    min_candles = 30

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]

        # Ищем гэп в последних 20 свечах
        for n in range(3, min(20, len(buf) - 2)):
            gap_candle = buf[-n]
            before_gap = buf[-(n + 1)]

            # Гэп вниз
            if gap_candle.open < before_gap.low * 0.998:
                gap_level = before_gap.low
                # Все свечи острова ниже уровня гэпа
                island = all(buf[-i].high < gap_level for i in range(1, n))
                if island and cur.close >= gap_level * 0.998 and cur.is_bull:
                    strength = min(1.0, (cur.close - gap_level + atr * 0.2) / (atr * 0.5))
                    return PatternResult(
                        name=self.name,
                        detected=True,
                        direction=Direction.LONG,
                        strength=round(max(0, strength), 2),
                        description=f"Заполнение гэпа вниз, уровень {gap_level:.4f}"
                    )

            # Гэп вверх
            if gap_candle.open > before_gap.high * 1.002:
                gap_level = before_gap.high
                island = all(buf[-i].low > gap_level for i in range(1, n))
                if island and cur.close <= gap_level * 1.002 and cur.is_bear:
                    strength = min(1.0, (gap_level - cur.close + atr * 0.2) / (atr * 0.5))
                    return PatternResult(
                        name=self.name,
                        detected=True,
                        direction=Direction.SHORT,
                        strength=round(max(0, strength), 2),
                        description=f"Заполнение гэпа вверх, уровень {gap_level:.4f}"
                    )

        return PatternResult(self.name, False)


class NewsBuyback(PatternDetector):
    """
    Откуп на негативных новостях: гэп вниз / большая медвежья свеча,
    но закрытие возвращается выше поддержки.
    """
    name = "Откуп на новостях"
    min_candles = 30

    def _detect(self, buf: CandleBuffer, ind: IndicatorSet) -> PatternResult:
        atr = ind.atr
        cur = buf[0]
        prev = buf[-1]

        # Большое медвежье движение
        if not (prev.is_bear and prev.body > atr * 1.2):
            # Или гэп вниз
            if not (cur.open < prev.low * 0.997):
                return PatternResult(self.name, False)

        levels = find_levels(buf, n=50)
        if not levels:
            return PatternResult(self.name, False)

        for lvl in levels:
            # Пробили уровень, но восстановились
            if prev.low < lvl and cur.close > lvl and cur.is_bull:
                vol_spike = cur.volume > np.mean([buf[-i].volume for i in range(2, 7)]) * 1.5
                strength = 0.65 + (0.25 if vol_spike else 0.0)
                return PatternResult(
                    name=self.name,
                    detected=True,
                    direction=Direction.LONG,
                    strength=round(strength, 2),
                    description=f"Откуп выше уровня {lvl:.4f}{', объём ×1.5' if vol_spike else ''}"
                )

        return PatternResult(self.name, False)

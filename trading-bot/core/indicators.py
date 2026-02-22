"""
Расчёт технических индикаторов для стратегии.
Используем чистый numpy — без pandas overhead на горячем пути.
"""
import numpy as np
from dataclasses import dataclass


@dataclass
class IndicatorResult:
    # EMA channel
    ema_high: np.ndarray      # EMA(High, 20)
    ema_low: np.ndarray       # EMA(Low, 20)

    # ADX components
    adx: np.ndarray
    plus_di: np.ndarray       # +DI
    minus_di: np.ndarray      # -DI

    # Price arrays
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray


@dataclass
class SetupResult:
    direction: str            # "LONG" | "SHORT" | "NONE"
    adx_value: float
    plus_di: float
    minus_di: float
    ema_high_last: float
    ema_low_last: float
    prev_high: float          # PH — предыдущий структурный максимум
    prev_low: float           # PL — предыдущий структурный минимум
    close_last: float
    volume_ratio: float       # объём текущей свечи / средний объём


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """Экспоненциальная скользящая средняя."""
    k = 2.0 / (period + 1)
    result = np.empty_like(series, dtype=np.float64)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def _true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Вычисляет True Range (истинный диапазон) для серии свечей."""
    tr = np.zeros(len(highs))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return tr


def calculate_indicators(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    ema_period: int = 20,
    adx_period: int = 14,
) -> IndicatorResult:
    """
    Рассчитывает все необходимые индикаторы (EMA, ADX, DI) для списка 
    свечей одной серии. Используется оптимизированный расчет через numpy.
    """
    n = len(closes)
    assert n >= adx_period + ema_period, "Недостаточно свечей"

    # EMA канал
    ema_h = _ema(highs, ema_period)
    ema_l = _ema(lows, ema_period)

    # Directional Movement
    tr = _true_range(highs, lows, closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    # Smoothed values (Wilder)
    def wilder_smooth(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.zeros(n)
        out[p] = np.sum(arr[1: p + 1])
        for i in range(p + 1, n):
            out[i] = out[i - 1] - out[i - 1] / p + arr[i]
        return out

    smooth_tr = wilder_smooth(tr, adx_period)
    smooth_plus = wilder_smooth(plus_dm, adx_period)
    smooth_minus = wilder_smooth(minus_dm, adx_period)

    plus_di = np.where(smooth_tr > 0, 100 * smooth_plus / smooth_tr, 0.0)
    minus_di = np.where(smooth_tr > 0, 100 * smooth_minus / smooth_tr, 0.0)

    dx = np.where(
        (plus_di + minus_di) > 0,
        100 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
        0.0,
    )

    adx_arr = np.zeros(n)
    start = adx_period * 2
    if start < n:
        adx_arr[start] = np.mean(dx[adx_period: start + 1])
        for i in range(start + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (adx_period - 1) + dx[i]) / adx_period

    return IndicatorResult(
        ema_high=ema_h,
        ema_low=ema_l,
        adx=adx_arr,
        plus_di=plus_di,
        minus_di=minus_di,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )


def find_swing_high(highs: np.ndarray, lookback: int = 20) -> float:
    """Предыдущий структурный максимум (исключая последнюю свечу)."""
    return float(np.max(highs[-lookback - 1: -1]))


def find_swing_low(lows: np.ndarray, lookback: int = 20) -> float:
    """Предыдущий структурный минимум (исключая последнюю свечу)."""
    return float(np.min(lows[-lookback - 1: -1]))


def check_setup(ind: IndicatorResult, adx_threshold: float = 15.0) -> SetupResult:
    """
    Проверяет сигнал на последней завершённой свече.

    LONG:
      close > ema_high  AND  adx > threshold  AND  +DI > -DI
      AND  prev_high > ema_high[-2]  AND  close > prev_high

    SHORT:
      close < ema_low  AND  adx > threshold  AND  -DI > +DI
      AND  prev_low < ema_low[-2]  AND  close < prev_low
    """
    close = float(ind.closes[-1])
    ema_h = float(ind.ema_high[-1])
    ema_l = float(ind.ema_low[-1])
    adx_val = float(ind.adx[-1])
    pdi = float(ind.plus_di[-1])
    mdi = float(ind.minus_di[-1])

    # Структурные уровни (по предыдущим 20 свечам)
    prev_high = find_swing_high(ind.highs)
    prev_low = find_swing_low(ind.lows)

    # Объём — отношение последней свечи к среднему за 20
    avg_vol = float(np.mean(ind.volumes[-21:-1])) if len(ind.volumes) > 20 else 1.0
    vol_ratio = float(ind.volumes[-1]) / avg_vol if avg_vol > 0 else 1.0

    base = dict(
        adx_value=adx_val,
        plus_di=pdi,
        minus_di=mdi,
        ema_high_last=ema_h,
        ema_low_last=ema_l,
        prev_high=prev_high,
        prev_low=prev_low,
        close_last=close,
        volume_ratio=vol_ratio,
    )

    # LONG условия
    if (
        close > ema_h
        and adx_val > adx_threshold
        and pdi > mdi
        and close > prev_high  # цена пробила PH
    ):
        return SetupResult(direction="LONG", **base)

    # SHORT условия
    if (
        close < ema_l
        and adx_val > adx_threshold
        and mdi > pdi
        and close < prev_low  # цена пробила PL
    ):
        return SetupResult(direction="SHORT", **base)

    return SetupResult(direction="NONE", **base)
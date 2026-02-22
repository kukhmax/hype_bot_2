"""
Расчёт индикаторов через numpy (без зависимостей от pandas-ta для скорости).
Все функции принимают numpy arrays [oldest → newest].
"""
import numpy as np
from dataclasses import dataclass
from core.candle_buffer import CandleBuffer
from config import config


@dataclass
class IndicatorSet:
    # EMA
    ema10: float
    ema20: float
    ema50: float
    ema200: float

    # ADX
    adx: float        # сила тренда (>25 = тренд есть)
    plus_di: float    # +DI (бычья сила)
    minus_di: float   # -DI (медвежья сила)

    # RSI
    rsi: float        # 0-100

    # CCI
    cci: float        # типично -200..+200

    # ATR
    atr: float
    atr_pct: float    # ATR / Close * 100

    # Текущая цена
    close: float

    @property
    def trend_up(self) -> bool:
        """Восходящий тренд по EMA"""
        return self.ema10 > self.ema20 > self.ema50

    @property
    def trend_down(self) -> bool:
        """Нисходящий тренд по EMA"""
        return self.ema10 < self.ema20 < self.ema50

    @property
    def adx_trending(self) -> bool:
        return self.adx > 20

    @property
    def adx_strong(self) -> bool:
        return self.adx > 25

    @property
    def rsi_overbought(self) -> bool:
        return self.rsi > 70

    @property
    def rsi_oversold(self) -> bool:
        return self.rsi < 30

    @property
    def rsi_bullish(self) -> bool:
        return 40 < self.rsi < 70

    @property
    def rsi_bearish(self) -> bool:
        return 30 < self.rsi < 60

    @property
    def cci_bullish(self) -> bool:
        return self.cci > 0

    @property
    def cci_oversold(self) -> bool:
        return self.cci < -100

    @property
    def cci_overbought(self) -> bool:
        return self.cci > 100


# ─── Low-level numpy functions ───────────────────────────────────────────────

def ema_np(series: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.empty_like(series)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        )
    )
    # Wilder smoothing
    atr = np.empty(len(tr))
    atr[0] = tr[:period].mean()
    for i in range(1, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    # Паддинг первой свечи
    return np.concatenate([[atr[0]], atr])


def rsi_np(close: np.ndarray, period: int = 14) -> float:
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def cci_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 20) -> float:
    tp = (high + low + close) / 3
    tp_slice = tp[-period:]
    mean_tp = tp_slice.mean()
    mean_dev = np.abs(tp_slice - mean_tp).mean()
    if mean_dev == 0:
        return 0.0
    return (tp[-1] - mean_tp) / (0.015 * mean_dev)


def adx_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
    """Возвращает (ADX, +DI, -DI) — последнее значение"""
    n = len(close)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        h_diff = high[i] - high[i - 1]
        l_diff = low[i - 1] - low[i]
        plus_dm[i] = h_diff if h_diff > l_diff and h_diff > 0 else 0
        minus_dm[i] = l_diff if l_diff > h_diff and l_diff > 0 else 0
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))

    # Wilder smoothing
    def wilder(arr, p):
        result = np.zeros(len(arr))
        result[p] = arr[1:p + 1].sum()
        for i in range(p + 1, len(arr)):
            result[i] = result[i - 1] - result[i - 1] / p + arr[i]
        return result

    tr_s = wilder(tr, period)
    plus_dm_s = wilder(plus_dm, period)
    minus_dm_s = wilder(minus_dm, period)

    with np.errstate(divide='ignore', invalid='ignore'):
        plus_di = 100 * np.where(tr_s != 0, plus_dm_s / tr_s, 0)
        minus_di = 100 * np.where(tr_s != 0, minus_dm_s / tr_s, 0)
        dx = 100 * np.where(
            (plus_di + minus_di) != 0,
            np.abs(plus_di - minus_di) / (plus_di + minus_di),
            0,
        )

    adx = np.zeros(n)
    adx[2 * period] = dx[period:2 * period + 1].mean()
    for i in range(2 * period + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx[-1], plus_di[-1], minus_di[-1]


# ─── Главная функция ─────────────────────────────────────────────────────────

def calculate_indicators(buf: CandleBuffer) -> IndicatorSet:
    n = len(buf)
    closes = buf.closes()
    highs = buf.highs()
    lows = buf.lows()

    # EMA
    ema10_arr = ema_np(closes, 10)
    ema20_arr = ema_np(closes, 20)
    ema50_arr = ema_np(closes, 50)
    ema200_arr = ema_np(closes, min(200, n))

    # ATR
    atr_arr = atr_np(highs, lows, closes, config.ATR_PERIOD)
    atr_val = atr_arr[-1]

    # ADX
    adx_val, plus_di, minus_di = adx_np(highs, lows, closes, config.ADX_PERIOD)

    # RSI
    rsi_val = rsi_np(closes, config.RSI_PERIOD)

    # CCI
    cci_val = cci_np(highs, lows, closes, config.CCI_PERIOD)

    close_last = closes[-1]

    return IndicatorSet(
        ema10=ema10_arr[-1],
        ema20=ema20_arr[-1],
        ema50=ema50_arr[-1],
        ema200=ema200_arr[-1],
        adx=adx_val,
        plus_di=plus_di,
        minus_di=minus_di,
        rsi=rsi_val,
        cci=cci_val,
        atr=atr_val,
        atr_pct=atr_val / close_last * 100 if close_last else 0,
        close=close_last,
    )

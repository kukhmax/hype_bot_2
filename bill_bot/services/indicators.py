from __future__ import annotations

import statistics

from bill_bot.services.candle_store import Candle


def ema(values: list[float], length: int) -> list[float]:
    if length <= 0:
        raise ValueError("EMA length must be positive")
    if not values:
        return []
    k = 2.0 / (length + 1.0)
    out: list[float] = []
    prev = float(values[0])
    out.append(prev)
    for x in values[1:]:
        v = float(x)
        prev = (v * k) + (prev * (1.0 - k))
        out.append(prev)
    return out


def alligator_ema(closes: list[float]) -> dict[str, list[float]]:
    return {
        "jaw": ema(closes, 13),
        "teeth": ema(closes, 8),
        "lips": ema(closes, 5),
    }


def atr(candles: list[Candle], period: int = 14) -> list[float]:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    if not candles:
        return []

    true_ranges: list[float] = []
    prev_close: float | None = None
    for c in candles:
        high = float(c.h)
        low = float(c.l)
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(float(tr))
        prev_close = float(c.c)

    out: list[float] = []
    for i in range(len(true_ranges)):
        start = max(0, i - period + 1)
        chunk = true_ranges[start : i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def spread_lines(jaw: list[float], teeth: list[float], lips: list[float]) -> list[float]:
    n = min(len(jaw), len(teeth), len(lips))
    out: list[float] = []
    for i in range(n):
        mx = max(jaw[i], teeth[i], lips[i])
        mn = min(jaw[i], teeth[i], lips[i])
        out.append(mx - mn)
    return out


def is_sleep(spreads: list[float], last_close: float, window: int, k: float) -> tuple[bool, float]:
    if window <= 0:
        raise ValueError("Sleep window must be positive")
    if not spreads:
        return False, 0.0
    chunk = spreads[-window:] if len(spreads) >= window else spreads
    med = float(statistics.median(chunk))
    threshold = float(k) * float(last_close) if last_close else 0.0
    return med < threshold, med


def is_alligator_tangled(jaw: list[float], teeth: list[float], lips: list[float], window: int) -> bool:
    if window <= 1:
        return False
    n = min(len(jaw), len(teeth), len(lips))
    if n < 3:
        return False
    start = max(0, n - window)
    eps = 1e-12

    def crossed(a: list[float], b: list[float]) -> bool:
        prev = float(a[start]) - float(b[start])
        for i in range(start + 1, n):
            cur = float(a[i]) - float(b[i])
            if abs(cur) <= eps:
                return True
            if (prev < -eps and cur > eps) or (prev > eps and cur < -eps):
                return True
            prev = cur
        return False

    return crossed(jaw, teeth) and crossed(teeth, lips) and crossed(jaw, lips)

from __future__ import annotations

import statistics


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

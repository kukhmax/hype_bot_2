from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bill_bot.services.candle_store import Candle


@dataclass(frozen=True)
class FractalPoint:
    kind: str
    t: int
    price: float
    idx: int | None = None


@dataclass(frozen=True)
class PriceLevel:
    price: float
    label: str
    color: str
    linestyle: str = "-"
    linewidth: float = 1.0


def _fmt_ts_ms(ts_ms: int) -> str:
    try:
        ts_ms = int(ts_ms)
    except Exception:
        return str(ts_ms)
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    except Exception:
        return str(ts_ms)
    return dt.strftime("%H:%M %d/%m/%y")


def build_chart_png(
    pair: str,
    tf: str,
    candles: list[Candle],
    jaw: list[float],
    teeth: list[float],
    lips: list[float],
    fractals: Iterable[FractalPoint],
    levels: Iterable[PriceLevel] = (),
) -> bytes:
    if not candles:
        raise ValueError("no candles")
    if not (len(jaw) == len(teeth) == len(lips) == len(candles)):
        raise ValueError("indicator length mismatch")

    x = list(range(len(candles)))

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)

    up = "#16a34a"
    down = "#dc2626"
    wick = "#0f172a"

    w = 0.6
    for i, c in enumerate(candles):
        color = up if c.c >= c.o else down
        ax.vlines(i, c.l, c.h, color=wick, linewidth=1.0, alpha=0.9)
        y0 = min(c.o, c.c)
        h = max(abs(c.c - c.o), 1e-12)
        ax.add_patch(plt.Rectangle((i - w / 2, y0), w, h, facecolor=color, edgecolor=color, alpha=0.9))

    ax.plot(x, jaw, color="#2563eb", linewidth=1.2, label="Jaw(13)")
    ax.plot(x, teeth, color="#ef4444", linewidth=1.2, label="Teeth(8)")
    ax.plot(x, lips, color="#22c55e", linewidth=1.2, label="Lips(5)")

    t_min = candles[0].t
    t_max = candles[-1].t
    t_to_idx = {c.t: i for i, c in enumerate(candles)}
    step = (candles[1].t - candles[0].t) if len(candles) >= 2 else None
    for f in fractals:
        idx = getattr(f, "idx", None)
        if idx is None:
            if f.t < t_min or f.t > t_max:
                continue
            idx = t_to_idx.get(f.t)
            if idx is None and step:
                closest = min(range(len(candles)), key=lambda i: abs(candles[i].t - f.t))
                if abs(candles[closest].t - f.t) <= abs(step) / 2:
                    idx = closest
        if idx is None or idx < 0 or idx >= len(candles):
            continue
        if f.kind.upper() == "HIGH":
            ax.scatter([idx], [f.price], marker="^", s=80, color="#7c3aed", edgecolors="#0f172a", linewidths=0.6, zorder=6)
        else:
            ax.scatter([idx], [f.price], marker="v", s=80, color="#f59e0b", edgecolors="#0f172a", linewidths=0.6, zorder=6)

    for lvl in levels:
        try:
            price = float(lvl.price)
        except Exception:
            continue
        ax.axhline(y=price, color=lvl.color, linestyle=lvl.linestyle, linewidth=lvl.linewidth, alpha=0.9, label=lvl.label)

    ax.set_title(f"{pair} {tf} — Alligator + Fractals")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")

    step = max(1, len(candles) // 10)
    ax.set_xticks(list(range(0, len(candles), step)))
    ax.set_xticklabels([_fmt_ts_ms(candles[i].t) for i in range(0, len(candles), step)], rotation=30, fontsize=8)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

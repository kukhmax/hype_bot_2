from __future__ import annotations

from dataclasses import dataclass
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


def build_chart_png(
    pair: str,
    tf: str,
    candles: list[Candle],
    jaw: list[float],
    teeth: list[float],
    lips: list[float],
    fractals: Iterable[FractalPoint],
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
    for f in fractals:
        if f.t < t_min or f.t > t_max:
            continue
        idx = None
        for i, c in enumerate(candles):
            if c.t == f.t:
                idx = i
                break
        if idx is None:
            continue
        if f.kind.upper() == "HIGH":
            ax.scatter([idx], [f.price], marker="^", s=40, color="#7c3aed", zorder=5)
        else:
            ax.scatter([idx], [f.price], marker="v", s=40, color="#7c3aed", zorder=5)

    ax.set_title(f"{pair} {tf} — Alligator + Fractals")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")

    step = max(1, len(candles) // 10)
    ax.set_xticks(list(range(0, len(candles), step)))
    ax.set_xticklabels([str(candles[i].t) for i in range(0, len(candles), step)], rotation=30, fontsize=8)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

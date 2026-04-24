from __future__ import annotations

import json
from dataclasses import dataclass

import redis.asyncio as redis

from bill_bot.services.candle_store import Candle


@dataclass(frozen=True)
class Fractal:
    kind: str  # HIGH | LOW
    t: int
    price: float
    close: float
    teeth: float

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "t": self.t,
            "price": self.price,
            "close": self.close,
            "teeth": self.teeth,
        }

    @staticmethod
    def from_dict(d: dict) -> "Fractal":
        return Fractal(
            kind=str(d["kind"]),
            t=int(d["t"]),
            price=float(d["price"]),
            close=float(d["close"]),
            teeth=float(d["teeth"]),
        )


def detect_confirmed_fractal(window: list[Candle], teeth_series: list[float], center_idx: int) -> list[Fractal]:
    if center_idx < 2:
        return []
    if center_idx + 2 >= len(window):
        return []
    if center_idx >= len(teeth_series):
        return []

    c = window[center_idx]
    left2 = window[center_idx - 2]
    left1 = window[center_idx - 1]
    right1 = window[center_idx + 1]
    right2 = window[center_idx + 2]

    out: list[Fractal] = []

    high = c.h
    if high > left2.h and high > left1.h and high > right1.h and high > right2.h:
        out.append(
            Fractal(
                kind="HIGH",
                t=c.t,
                price=high,
                close=c.c,
                teeth=float(teeth_series[center_idx]),
            )
        )

    low = c.l
    if low < left2.l and low < left1.l and low < right1.l and low < right2.l:
        out.append(
            Fractal(
                kind="LOW",
                t=c.t,
                price=low,
                close=c.c,
                teeth=float(teeth_series[center_idx]),
            )
        )

    return out


class RedisFractalStore:
    def __init__(self, r: redis.Redis):
        self.r = r

    @staticmethod
    def key(pair: str, tf: str) -> str:
        return f"fractals:{pair}:{tf}"

    async def get_all(self, pair: str, tf: str) -> list[Fractal]:
        raw = await self.r.get(self.key(pair, tf))
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out: list[Fractal] = []
        for item in data:
            if isinstance(item, dict):
                out.append(Fractal.from_dict(item))
        out.sort(key=lambda f: f.t)
        return out

    async def append_new(self, pair: str, tf: str, fractals: list[Fractal], max_len: int) -> int:
        if not fractals:
            return 0
        existing = await self.get_all(pair, tf)
        seen = {f"{f.kind}:{f.t}" for f in existing}
        added: list[Fractal] = []
        for f in fractals:
            k = f"{f.kind}:{f.t}"
            if k in seen:
                continue
            added.append(f)
            seen.add(k)
        if not added:
            return 0
        merged = existing + added
        merged.sort(key=lambda f: f.t)
        if len(merged) > max_len:
            merged = merged[-max_len:]
        await self.r.set(
            self.key(pair, tf),
            json.dumps([f.to_dict() for f in merged], separators=(",", ":")),
        )
        return len(added)

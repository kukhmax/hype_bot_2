from __future__ import annotations

import json
from dataclasses import dataclass

import redis.asyncio as redis


@dataclass(frozen=True)
class Candle:
    t: int
    T: int
    s: str
    i: str
    o: float
    c: float
    h: float
    l: float
    v: float
    n: int | None = None

    @staticmethod
    def from_hl(raw: dict) -> "Candle":
        return Candle(
            t=int(raw["t"]),
            T=int(raw["T"]),
            s=str(raw.get("s") or raw.get("coin") or ""),
            i=str(raw.get("i") or raw.get("interval") or ""),
            o=float(raw["o"]),
            c=float(raw["c"]),
            h=float(raw["h"]),
            l=float(raw["l"]),
            v=float(raw["v"]),
            n=int(raw["n"]) if "n" in raw and raw["n"] is not None else None,
        )

    def to_dict(self) -> dict:
        d = {
            "t": self.t,
            "T": self.T,
            "s": self.s,
            "i": self.i,
            "o": self.o,
            "c": self.c,
            "h": self.h,
            "l": self.l,
            "v": self.v,
        }
        if self.n is not None:
            d["n"] = self.n
        return d


class RedisCandleStore:
    def __init__(self, r: redis.Redis):
        self.r = r

    @staticmethod
    def candles_key(pair: str, tf: str) -> str:
        return f"candles:{pair}:{tf}"

    @staticmethod
    def meta_key(pair: str, tf: str) -> str:
        return f"candles_meta:{pair}_{tf}"

    async def set_window(self, pair: str, tf: str, candles: list[Candle]):
        key = self.candles_key(pair, tf)
        payload = [c.to_dict() for c in candles]
        raw = json.dumps(payload, separators=(",", ":"))
        await self.r.set(key, raw)
        await self._update_meta(pair, tf, candles)

    async def get_window(self, pair: str, tf: str) -> list[Candle]:
        raw = await self.r.get(self.candles_key(pair, tf))
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out: list[Candle] = []
        for item in data:
            if isinstance(item, dict):
                out.append(Candle.from_hl(item))
        out.sort(key=lambda c: c.t)
        return out

    async def append_if_new(self, pair: str, tf: str, candle: Candle, max_len: int):
        window = await self.get_window(pair, tf)
        last_t = window[-1].t if window else None
        if last_t is not None and candle.t <= last_t:
            return False
        window.append(candle)
        if len(window) > max_len:
            window = window[-max_len:]
        await self.set_window(pair, tf, window)
        return True

    async def _update_meta(self, pair: str, tf: str, candles: list[Candle]):
        if not candles:
            return
        ts_from = candles[0].t
        ts_to = candles[-1].t
        await self.r.hset(
            self.meta_key(pair, tf),
            mapping={
                "count": len(candles),
                "ts_from": ts_from,
                "ts_to": ts_to,
            },
        )

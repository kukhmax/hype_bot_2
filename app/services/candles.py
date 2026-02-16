import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List

import pandas as pd

from app.core.redis import redis_client


def _floor_ts_to_bucket(ts_ms: int, tf_minutes: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    minutes = (dt.minute // tf_minutes) * tf_minutes
    bucket = dt.replace(minute=minutes, second=0, microsecond=0)
    return int(bucket.timestamp() * 1000)


class CandleBuilder:
    def __init__(self, pair: str, timeframe_min: int):
        self.pair = pair
        self.tf = timeframe_min
        self.bucket_ms: Optional[int] = None
        self.o = self.h = self.l = self.c = None
        self._lock = asyncio.Lock()

    async def update(self, ts_ms: int, price: float) -> Optional[dict]:
        async with self._lock:
            bucket_ms = _floor_ts_to_bucket(ts_ms, self.tf)
            if self.bucket_ms is None:
                self.bucket_ms = bucket_ms
                self.o = self.h = self.l = self.c = price
                return None

            if bucket_ms > self.bucket_ms:
                candle = {
                    "t": self.bucket_ms,
                    "o": float(self.o),
                    "h": float(self.h),
                    "l": float(self.l),
                    "c": float(self.c),
                }
                self.bucket_ms = bucket_ms
                self.o = self.h = self.l = self.c = price
                return candle

            self.c = price
            if price > self.h:
                self.h = price
            if price < self.l:
                self.l = price
            return None


class RedisCandleStore:
    @staticmethod
    def key(pair: str, tf: int) -> str:
        return f"ohlcv:{pair}:{tf}m"

    @staticmethod
    async def append(pair: str, tf: int, candle: dict, keep: int = 200):
        k = RedisCandleStore.key(pair, tf)
        await redis_client.rpush(k, json.dumps(candle))
        await redis_client.ltrim(k, -keep, -1)

    @staticmethod
    async def get_last(pair: str, tf: int, n: int = 200) -> List[dict]:
        k = RedisCandleStore.key(pair, tf)
        items = await redis_client.lrange(k, -n, -1)
        result = []
        for raw in items:
            if isinstance(raw, bytes):
                raw = raw.decode()
            result.append(json.loads(raw))
        return result

    @staticmethod
    async def to_df(pair: str, tf: int, n: int = 200) -> Optional[pd.DataFrame]:
        arr = await RedisCandleStore.get_last(pair, tf, n)
        if not arr:
            return None
        df = pd.DataFrame(arr)
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
        df = df[["timestamp", "o", "h", "l", "c"]]
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
        return df


class CandlePipeline:
    def __init__(self):
        self.builders: Dict[tuple, CandleBuilder] = {}

    async def on_tick(self, pair: str, ts_ms: int, price: float, timeframes: List[int]):
        for tf in timeframes:
            key = (pair, tf)
            if key not in self.builders:
                self.builders[key] = CandleBuilder(pair, tf)
            finished = await self.builders[key].update(ts_ms, price)
            if finished:
                await RedisCandleStore.append(pair, tf, finished)

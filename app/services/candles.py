"""Cервис построения свечей из тиков и их хранение в Redis.

Назначение:
- CandleBuilder: инкрементально собирает OHLC свечу из последовательности тиков.
- RedisCandleStore: сохраняет и извлекает последние N (по умолчанию 200) свечей.
- CandlePipeline: маршрутизирует тики по таймфреймам и завершённые свечи складывает в Redis.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List

import pandas as pd

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

def _floor_ts_to_bucket(ts_ms: int, tf_minutes: int) -> int:
    """Округляет метку времени вниз до начала интервала таймфрейма (в миллисекундах)."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    minutes = (dt.minute // tf_minutes) * tf_minutes
    bucket = dt.replace(minute=minutes, second=0, microsecond=0)
    return int(bucket.timestamp() * 1000)


class CandleBuilder:
    """Сборщик свечей OHLC для заданного инструмента и таймфрейма.

    Использование:
    - вызывать update(ts_ms, price) на каждом тике;
    - когда интервал таймфрейма завершается, метод возвращает готовую свечу и начинает следующую.
    """

    def __init__(self, pair: str, timeframe_min: int):
        self.pair = pair
        self.tf = timeframe_min
        self.bucket_ms: Optional[int] = None
        self.o = self.h = self.l = self.c = None
        self._lock = asyncio.Lock()

    async def update(self, ts_ms: int, price: float) -> Optional[dict]:
        """Обрабатывает тик.

        Возвращает готовую свечу (dict) при переходе на следующий интервал,
        либо None, если свеча ещё строится.
        """
        async with self._lock:
            bucket_ms = _floor_ts_to_bucket(ts_ms, self.tf)
            if self.bucket_ms is None:
                self.bucket_ms = bucket_ms
                self.o = self.h = self.l = self.c = price
                logger.debug("Старт новой свечи %s %sm @ %s, o=%.6f", self.pair, self.tf, bucket_ms, price)
                return None

            if bucket_ms > self.bucket_ms:
                candle = {
                    "t": self.bucket_ms,
                    "o": float(self.o),
                    "h": float(self.h),
                    "l": float(self.l),
                    "c": float(self.c),
                }
                logger.debug(
                    "Свеча закрыта %s %sm @ %s: o=%.6f h=%.6f l=%.6f c=%.6f",
                    self.pair, self.tf, self.bucket_ms, self.o, self.h, self.l, self.c
                )
                self.bucket_ms = bucket_ms
                self.o = self.h = self.l = self.c = price
                logger.debug("Старт следующей свечи %s %sm @ %s, o=%.6f", self.pair, self.tf, bucket_ms, price)
                return candle

            self.c = price
            if price > self.h:
                self.h = price
            if price < self.l:
                self.l = price
            logger.debug("Апдейт текущей свечи %s %sm: c=%.6f h=%.6f l=%.6f", self.pair, self.tf, self.c, self.h, self.l)
            return None


class RedisCandleStore:
    """Хранилище свечей в Redis (список JSON-объектов)."""

    @staticmethod
    def key(pair: str, tf: int) -> str:
        return f"ohlcv:{pair}:{tf}m"

    @staticmethod
    async def append(pair: str, tf: int, candle: dict, keep: int = 200):
        """Добавляет свечу в конец списка и обрезает хвост до последних keep элементов."""
        k = RedisCandleStore.key(pair, tf)
        await redis_client.rpush(k, json.dumps(candle))
        await redis_client.ltrim(k, -keep, -1)
        logger.info("Сохранена свеча %s %sm, всего храним %s", pair, tf, keep)

    @staticmethod
    async def get_last(pair: str, tf: int, n: int = 200) -> List[dict]:
        """Возвращает список из последних n свечей (dict)."""
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
        """Возвращает pandas.DataFrame со столбцами open/high/low/close и индексом времени."""
        arr = await RedisCandleStore.get_last(pair, tf, n)
        if not arr:
            logger.debug("Нет свечей в Redis для %s %sm", pair, tf)
            return None
        df = pd.DataFrame(arr)
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
        df = df[["timestamp", "o", "h", "l", "c"]]
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
        return df


class CandlePipeline:
    """Пайплайн, распределяющий тики по таймфреймам и сохраняющий завершённые свечи."""

    def __init__(self):
        self.builders: Dict[tuple, CandleBuilder] = {}

    async def on_tick(self, pair: str, ts_ms: int, price: float, timeframes: List[int]):
        """Обрабатывает тик и обновляет свечи для всех интересующих таймфреймов."""
        logger.debug("Tick %s ts=%s price=%.6f tf_list=%s", pair, ts_ms, price, timeframes)
        for tf in timeframes:
            key = (pair, tf)
            if key not in self.builders:
                self.builders[key] = CandleBuilder(pair, tf)
            finished = await self.builders[key].update(ts_ms, price)
            if finished:
                await RedisCandleStore.append(pair, tf, finished)

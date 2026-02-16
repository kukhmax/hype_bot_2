"""Redis-замок для дедупликации сигналов на период таймфрейма."""

import logging
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

class SignalLock:
    """Простой NX+EX замок в Redis для блокировки повторной отправки сигнала."""

    @staticmethod
    async def acquire(user_id: int, pair: str, timeframe: int, ttl_seconds: int) -> bool:
        key = f"siglock:{user_id}:{pair}:{timeframe}m"
        ok = await redis_client.set(key, "1", ex=ttl_seconds, nx=True) is True
        logger.debug("Acquire lock %s -> %s, ttl=%ss", key, ok, ttl_seconds)
        return ok

    @staticmethod
    async def clear(user_id: int, pair: str, timeframe: int):
        key = f"siglock:{user_id}:{pair}:{timeframe}m"
        await redis_client.delete(key)
        logger.debug("Clear lock %s", key)

"""Простой rate limiter по пользователю на основе Redis."""

import logging
import time
from app.core.redis import redis_client
from app.config import RATE_LIMIT_SECONDS

logger = logging.getLogger(__name__)

class RateLimiter:
    """Ограничивает частоту действий пользователя с шагом RATE_LIMIT_SECONDS."""

    @staticmethod
    async def check(user_id: int):
        """Возвращает True, если действие разрешено, иначе False."""
        key = f"rate:{user_id}"
        last = await redis_client.get(key)

        now = time.time()

        if last and now - float(last) < RATE_LIMIT_SECONDS:
            logger.debug("Rate limit: user=%s blocked", user_id)
            return False

        await redis_client.set(key, now)
        logger.debug("Rate limit: user=%s allowed", user_id)
        return True

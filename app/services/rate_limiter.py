import time
from app.core.redis import redis_client
from app.config import RATE_LIMIT_SECONDS

class RateLimiter:

    @staticmethod
    async def check(user_id: int):
        key = f"rate:{user_id}"
        last = await redis_client.get(key)

        now = time.time()

        if last and now - float(last) < RATE_LIMIT_SECONDS:
            return False

        await redis_client.set(key, now)
        return True

from app.core.redis import redis_client


class SignalLock:
    @staticmethod
    async def acquire(user_id: int, pair: str, timeframe: int, ttl_seconds: int) -> bool:
        key = f"siglock:{user_id}:{pair}:{timeframe}m"
        return await redis_client.set(key, "1", ex=ttl_seconds, nx=True) is True

    @staticmethod
    async def clear(user_id: int, pair: str, timeframe: int):
        key = f"siglock:{user_id}:{pair}:{timeframe}m"
        await redis_client.delete(key)

import json
from app.core.redis import redis_client


class IndicatorCache:
    @staticmethod
    def key(pair: str, tf: int) -> str:
        return f"ind:last:{pair}:{tf}m"

    @staticmethod
    async def set_last(pair: str, tf: int, payload: dict):
        k = IndicatorCache.key(pair, tf)
        await redis_client.set(k, json.dumps(payload), ex=3600)

    @staticmethod
    async def get_last(pair: str, tf: int) -> dict | None:
        k = IndicatorCache.key(pair, tf)
        raw = await redis_client.get(k)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)

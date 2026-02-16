import json
from typing import Optional

from app.core.redis import redis_client


class PositionManager:
    @staticmethod
    def _key(user_id: int, pair: str) -> str:
        return f"position:{user_id}:{pair}"

    @staticmethod
    async def get(user_id: int, pair: str) -> Optional[dict]:
        k = PositionManager._key(user_id, pair)
        raw = await redis_client.get(k)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)

    @staticmethod
    async def open(user_id: int, pair: str, side: str, qty: float, entry: float):
        k = PositionManager._key(user_id, pair)
        data = {"side": side, "qty": qty, "entry": entry, "status": "OPEN"}
        await redis_client.set(k, json.dumps(data))
        return data

    @staticmethod
    async def close(user_id: int, pair: str):
        k = PositionManager._key(user_id, pair)
        await redis_client.delete(k)


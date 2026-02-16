import json
"""Менеджер позиций пользователя в Redis."""

import json
import logging
from typing import Optional

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

class PositionManager:
    """Хранит состояние позиции по паре: сторона, размер, цена входа, статус."""

    @staticmethod
    def _key(user_id: int, pair: str) -> str:
        return f"position:{user_id}:{pair}"

    @staticmethod
    async def get(user_id: int, pair: str) -> Optional[dict]:
        """Читает текущую позицию пользователя по паре, либо None."""
        k = PositionManager._key(user_id, pair)
        raw = await redis_client.get(k)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        pos = json.loads(raw)
        logger.debug("Получена позиция %s: %s", k, pos)
        return pos

    @staticmethod
    async def open(user_id: int, pair: str, side: str, qty: float, entry: float):
        """Открывает позицию и сохраняет в Redis."""
        k = PositionManager._key(user_id, pair)
        data = {"side": side, "qty": qty, "entry": entry, "status": "OPEN"}
        await redis_client.set(k, json.dumps(data))
        logger.info("Открыта позиция %s: %s", k, data)
        return data

    @staticmethod
    async def close(user_id: int, pair: str):
        """Закрывает позицию (удаляет ключ)."""
        k = PositionManager._key(user_id, pair)
        await redis_client.delete(k)
        logger.info("Закрыта позиция %s", k)

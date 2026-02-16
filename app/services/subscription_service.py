"""Сервис управления подписками пользователей на сигналы."""

import uuid
import json
import logging
from app.core.redis import redis_client
from app.config import MAX_SUBSCRIPTIONS

logger = logging.getLogger(__name__)

class SubscriptionService:

    """CRUD-операции по подпискам: создать, получить, удалить, список пользователей."""

    @staticmethod
    async def create_subscription(user_id: int, data: dict):
        """Создаёт подписку для пользователя, проверяя лимит и дубликаты по паре."""
        key = f"user:{user_id}:subscriptions"

        subs = await redis_client.hgetall(key)

        if len(subs) >= MAX_SUBSCRIPTIONS:
            raise Exception("Subscription limit reached")

        # защита от дубликатов
        for sub in subs.values():
            if json.loads(sub)["pair"] == data["pair"]:
                raise Exception("Already subscribed to this pair")

        sub_id = str(uuid.uuid4())
        await redis_client.hset(key, sub_id, json.dumps(data))
        logger.info("Создана подписка user=%s id=%s data=%s", user_id, sub_id, data)
        return sub_id

    @staticmethod
    async def get_user_subscriptions(user_id: int):
        """Возвращает список подписок пользователя (dict с id)."""
        key = f"user:{user_id}:subscriptions"
        subs = await redis_client.hgetall(key)

        result = []
        for sub_id, sub in subs.items():
            obj = json.loads(sub)
            obj["id"] = sub_id
            result.append(obj)

        return result

    @staticmethod
    async def delete_subscription(user_id: int, sub_id: str):
        """Удаляет подписку по идентификатору."""
        key = f"user:{user_id}:subscriptions"
        await redis_client.hdel(key, sub_id)
        logger.info("Удалена подписка user=%s id=%s", user_id, sub_id)

    @staticmethod
    async def get_all_users():
        """Возвращает список user_id, у которых есть хотя бы одна подписка."""
        keys = await redis_client.keys("user:*:subscriptions")
        users = [int(k.split(":")[1]) for k in keys]
        return users

import uuid
import json
from app.core.redis import redis_client
from app.config import MAX_SUBSCRIPTIONS

class SubscriptionService:

    @staticmethod
    async def create_subscription(user_id: int, data: dict):
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
        return sub_id

    @staticmethod
    async def get_user_subscriptions(user_id: int):
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
        key = f"user:{user_id}:subscriptions"
        await redis_client.hdel(key, sub_id)

    @staticmethod
    async def get_all_users():
        keys = await redis_client.keys("user:*:subscriptions")
        users = [int(k.split(":")[1]) for k in keys]
        return users

from app.redis_client import redis_client
from app.config import MAX_SUBSCRIPTIONS

async def add_subscription(user_id, symbol, tf):

    subs = await redis_client.smembers(f"user:{user_id}:subs")
    if len(subs) >= MAX_SUBSCRIPTIONS:
        return False

    key = f"{symbol}:{tf}"

    await redis_client.sadd(f"user:{user_id}:subs", key)
    await redis_client.sadd(f"sub:{key}", user_id)

    return True


async def get_user_subscriptions(user_id):
    return await redis_client.smembers(f"user:{user_id}:subs")


async def remove_subscription(user_id, pair):
    await redis_client.srem(f"user:{user_id}:subs", pair)
    await redis_client.srem(f"sub:{pair}", user_id)

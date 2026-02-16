from app.redis_client import redis_client

async def rate_limit(user_id):
    key = f"rate:{user_id}"
    count = await redis_client.incr(key)

    if count == 1:
        await redis_client.expire(key, 10)

    return count <= 5

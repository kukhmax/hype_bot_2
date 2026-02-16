import redis.asyncio as redis
from app.config import REDIS_HOST

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True
)

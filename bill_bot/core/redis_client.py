import redis.asyncio as redis

from bill_bot.core.config import Config


def build_redis_url(cfg: Config) -> str:
    if cfg.redis_password:
        return f"redis://:{cfg.redis_password}@{cfg.redis_host}:{cfg.redis_port}/0"
    return f"redis://{cfg.redis_host}:{cfg.redis_port}/0"


def get_redis(cfg: Config) -> redis.Redis:
    return redis.from_url(build_redis_url(cfg), decode_responses=True)

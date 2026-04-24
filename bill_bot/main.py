import asyncio
import logging

from bill_bot.core.config import Config
from bill_bot.core.logger import setup_logger
from bill_bot.core.redis_client import get_redis


async def main():
    cfg = Config.from_env()
    logger = setup_logger(cfg.log_level)

    r = get_redis(cfg)
    try:
        pong = await r.ping()
        logger.info("Redis ping=%s host=%s port=%s", pong, cfg.redis_host, cfg.redis_port)
    except Exception as e:
        logger.error("Redis connection failed: %s", e)
        raise

    logger.info("Bill Bot bootstrap started (dry-run). Waiting...")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    asyncio.run(main())

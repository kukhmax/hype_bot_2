"""
Точка входа. Запускает Telegram бот + Worker параллельно.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from config import config
from core.redis_client import redis_client
from core.worker import init_worker
from bot.handlers import start, subscribe, subscriptions, trading

from logging.handlers import RotatingFileHandler

# Настройка логирования
logging_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=logging_format,
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=2),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def main():
    # Подключаем Redis
    await redis_client.connect()
    logger.info("Redis connected")

    # FSM хранилище в Redis
    storage = RedisStorage.from_url(config.REDIS_URL)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # Регистрируем роутеры
    dp.include_router(start.router)
    dp.include_router(subscribe.router)
    dp.include_router(subscriptions.router)
    dp.include_router(trading.router)

    # Инициализируем воркер (восстанавливает подписки + запускает WS)
    await init_worker(bot)
    logger.info("Worker initialized")

    # Запускаем polling
    logger.info("Bot started. Polling...")
    try:
        # Старт поллинга новых событий
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Корректное закрытие ресурсов при завершении работы бота
        await redis_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
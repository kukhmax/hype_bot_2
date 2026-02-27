import asyncio
import sys
import os
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from core.config import settings
from core.logger import setup_logger
from bot.handlers import router as main_router

from bot.settings import bot_settings

logger = setup_logger("telegram_bot")

async def main():
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен в .env! Выход.")
        return

    # Инициализация бота и диспетчера
    bot_instance = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode='Markdown'))
    bot_settings["bot_instance"] = bot_instance
    dp = Dispatcher()
    
    # Подключаем роутеры (обработчики команд)
    dp.include_router(main_router)
    
    # Middleware для ограничения доступа только по ADMIN_CHAT_ID можно добавить здесь.
    # В MVP мы просто будем проверять ID внутри хэндлеров или напишем простую middleware позже.
    
    logger.info("🤖 Telegram Bot запущен!")
    
    try:
        # Уведомляем админа о старте
        if settings.ADMIN_CHAT_ID:
            await bot_instance.send_message(chat_id=settings.ADMIN_CHAT_ID, text="🤖 Telegram интерфейс Hype Bot запущен и готов к работе!")
            
        await dp.start_polling(bot_instance)
    except Exception as e:
        logger.error(f"Ошибка Polling: {e}")
    finally:
        await bot_instance.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Telegram Bot остановлен (Ctrl+C).")

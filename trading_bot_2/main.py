"""
Точка входа: запуск Telegram бота.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import logger
from config import config


async def main():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не задан! Скопируй .env.example → .env и заполни.")
        sys.exit(1)
    if not config.GEMINI_API_KEY:
        logger.warning("⚠️  GEMINI_API_KEY не задан — AI-анализ будет недоступен (сигналы всё равно придут)")

    os.makedirs("logs", exist_ok=True)

    from bot.telegram_bot import TradingBot
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from aiogram import Bot, Dispatcher
from app.config import BOT_TOKEN
from app.core.logger import setup_logger
from app.handlers import start, subscriptions
from app.handlers import trade
from app.services.market_ws import MarketWS

logger = setup_logger()

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(subscriptions.router)
    dp.include_router(trade.router)

    ws = MarketWS()

    asyncio.create_task(ws.connect())

    while True:
        try:
            logger.info("Bot started")
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Reconnect due to: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())

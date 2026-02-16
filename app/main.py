import asyncio
from aiogram import Bot, Dispatcher
from app.config import BOT_TOKEN
from app.handlers import start, subscriptions, callbacks
from app.services.ws_engine import ws_loop

async def main():

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(subscriptions.router)
    dp.include_router(callbacks.router)

    asyncio.create_task(ws_loop(bot))

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

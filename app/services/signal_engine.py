from app.redis_client import redis_client
from app.keyboards import signal_keyboard
from aiogram import Bot

async def send_signal(bot: Bot, symbol, tf, direction, adx, atr):

    key = f"{symbol}:{tf}"
    users = await redis_client.smembers(f"sub:{key}")

    for user_id in users:

        settings = await redis_client.hgetall(f"user:{user_id}:settings")

        if float(adx) <= float(settings["adx_threshold"]):
            continue

        if float(atr) <= float(settings["atr_threshold"]):
            continue

        risk = settings["risk_percent"]

        await bot.send_message(
            user_id,
            f"{symbol} {tf}\n"
            f"Signal: {direction}\n"
            f"ADX: {adx:.2f}\n"
            f"ATR: {atr:.4f}",
            reply_markup=signal_keyboard(symbol, direction, risk)
        )

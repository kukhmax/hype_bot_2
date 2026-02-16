from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN)


class Notifier:

    @staticmethod
    async def send_signal(user_id: int, pair: str, signal: str, risk: float):

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Открыть {signal}",
                        callback_data=f"trade:{pair}:{signal}:{risk}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Открыть в Hyperliquid",
                        url=f"https://app.hyperliquid.xyz/trade/{pair}"
                    )
                ]
            ]
        )

        await bot.send_message(
            user_id,
            f"🔥 Сигнал {signal} по {pair}\nRisk: {risk}%",
            reply_markup=kb
        )

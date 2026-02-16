"""Отправка уведомлений пользователю в Telegram."""

import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN)
logger = logging.getLogger(__name__)


class Notifier:
    """Сервис-обёртка над Bot для отправки сообщений с инлайн-кнопками."""

    @staticmethod
    async def send_signal(user_id: int, pair: str, signal: str, risk: float):
        """Отправляет сообщение с кнопкой открытия позиции и ссылкой на торговый терминал."""

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

        logger.info("Уведомление пользователю %s: %s %s (risk=%s)", user_id, pair, signal, risk)
        await bot.send_message(
            user_id,
            f"🔥 Сигнал {signal} по {pair}\nRisk: {risk}%",
            reply_markup=kb
        )

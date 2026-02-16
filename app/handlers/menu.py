from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📡 Получать сигналы")],
            [KeyboardButton(text="📋 Активные подписки")],
            [KeyboardButton(text="💰 Торговать")]
        ],
        resize_keyboard=True
    )

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def signal_keyboard(pair: str, side: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Открыть {side}",
                    callback_data=f"trade:{pair}:{side}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Hyperliquid",
                    url=f"https://app.hyperliquid.xyz/trade/{pair}"
                )
            ]
        ]
    )

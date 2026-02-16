from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Получать сигналы")],
            [KeyboardButton(text="Мои подписки")]
        ],
        resize_keyboard=True
    )

def signal_keyboard(symbol, direction, risk):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Open {direction} {risk}%",
                callback_data=f"open:{symbol}:{direction}:{risk}"
            )
        ],
        [
            InlineKeyboardButton(
                text="Hyperliquid",
                url=f"https://app.hyperliquid.xyz/trade/{symbol}"
            )
        ]
    ])

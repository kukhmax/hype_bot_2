from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📡 Получать сигналы")],
            [KeyboardButton(text="📋 Активные подписки")]
        ],
        resize_keyboard=True
    )

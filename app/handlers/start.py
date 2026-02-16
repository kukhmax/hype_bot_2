from aiogram import Router, F
from aiogram.types import Message
from app.handlers.menu import main_menu

router = Router()

@router.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer(
        "Добро пожаловать в Trading Bot 🚀",
        reply_markup=main_menu()
    )

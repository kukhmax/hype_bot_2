from aiogram import Router, types
from app.keyboards import main_menu

router = Router()

@router.message()
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать 🚀",
        reply_markup=main_menu()
    )

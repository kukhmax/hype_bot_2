"""
Обработчик команды /start
"""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.keyboards import main_menu_kb, bottom_menu_kb
from bot.messages import WELCOME

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Отправляет приветственное сообщение и главное меню (закрепленные кнопки)."""
    await message.answer(WELCOME, parse_mode="HTML", reply_markup=bottom_menu_kb())
    await message.answer("Выберите действие:", reply_markup=main_menu_kb())
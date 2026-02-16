"""Стартовый хэндлер /start."""

import logging
from aiogram import Router, F
from aiogram.types import Message
from app.handlers.menu import main_menu

router = Router()
logger = logging.getLogger(__name__)

@router.message(F.text == "/start")
async def start_handler(message: Message):
    """Приветствие и вывод главного меню."""
    logger.info("Команда /start от user=%s", message.from_user.id)
    await message.answer(
        "Добро пожаловать в Trading Bot 🚀",
        reply_markup=main_menu()
    )

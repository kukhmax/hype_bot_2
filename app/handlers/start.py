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
        (
            "Добро пожаловать в Trading Bot 🚀\n\n"
            "Этот бот даёт сигналы по рынку Hyperliquid на основе индикаторов ADX/ATR.\n\n"
            "Что означают настройки при подписке:\n"
            "• Пара — тикер, по которому хотите получать сигналы (например, BTC, ETH).\n"
            "• Таймфрейм — размер свечи (например, 5m, 15m, 1h).\n"
            "• Минимальный ADX — сила тренда; чем выше, тем более выраженное движение.\n"
            "• Минимальный ATR — волатильность; фильтр по амплитуде движения цены.\n"
            "• Risk % — доля депозита, которую вы готовы рискнуть в сделке.\n\n"
            "Нажмите «📡 Получать сигналы», чтобы настроить свою первую подписку."
        ),
        reply_markup=main_menu()
    )

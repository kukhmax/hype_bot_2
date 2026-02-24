import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

logger = logging.getLogger("telegram_handlers")

router = Router()

# Главное меню в виде Reply-кнопок внизу экрана
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🧪 Тест Стратегий")],
            [KeyboardButton(text="🚀 ЗАПУСК БОТА"), KeyboardButton(text="🛑 СТОП")]
        ],
        resize_keyboard=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    """
    Обработчик команды /start. Приветствие и выдача клавиатуры.
    """
    welcome_text = (
        "👋 Добро пожаловать в **Hype Bot (v2)**!\n\n"
        "Я автономный торговый бот для фьючерсов на MEXC.\n"
        "Мой арсенал:\n"
        "📈 *Trend Pullback*\n"
        "⚡️ *Volatility Breakout*\n"
        "🧲 *Liquidity Sweep Reversal*\n\n"
        "Используйте меню ниже для тестирования стратегий на истории или запуска бота в Paper/Live режимах."
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message):
    await message.answer("Статус: 📴 Бот остановлен.\nРежим: Paper Trading\nМонета: SOL_USDT (15m)")

@router.message(F.text == "🚀 ЗАПУСК БОТА")
async def cmd_start_bot(message: Message):
    await message.answer("Запуск в разработке... (Перейдем к этому в Шаге 5)")

@router.message(F.text == "🛑 СТОП")
async def cmd_stop_bot(message: Message):
    await message.answer("Остановка движка...")

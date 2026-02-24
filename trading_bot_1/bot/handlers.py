import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from bot.states import SettingsFSM
from bot.settings import bot_settings

logger = logging.getLogger("telegram_handlers")

router = Router()

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
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    welcome_text = (
        "👋 Добро пожаловать в **Hype Bot (v2)**!\n\n"
        "Я автономный торговый бот для фьючерсов на MEXC.\n"
        "Мой арсенал:\n"
        "📈 *Trend Pullback*\n"
        "⚡️ *Volatility Breakout*\n"
        "🧲 *Liquidity Sweep Reversal*\n\n"
        "Используйте меню ниже для навигации."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "📊 **ТЕКУЩИЙ СТАТУС**\n\n"
        f"Режим: `{bot_settings['mode'].upper()}`\n"
        f"Монета: `{bot_settings['symbol']}`\n"
        f"Таймфрейм: `{bot_settings['timeframe']}m`\n"
        f"Риск на сделку: `{bot_settings['risk_percent']}%`\n"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

# --- НАСТРОЙКИ (FSM) ---

@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="PAPER (Тест)"), KeyboardButton(text="LIVE (Бой)")],
        [KeyboardButton(text="SIGNALS (Только сигналы)")]
    ], resize_keyboard=True)
    await message.answer("🛠 *НАСТРОЙКИ*\n\nВыберите Режим работы бота:", reply_markup=kb)
    await state.set_state(SettingsFSM.waiting_for_mode)

@router.message(SettingsFSM.waiting_for_mode)
async def process_mode(message: Message, state: FSMContext):
    mode_text = message.text.split()[0].lower() # "PAPER (Тест)" -> "paper"
    if mode_text not in ["paper", "live", "signals"]:
        await message.answer("Пожалуйста, выберите режим кнопкой.")
        return
        
    bot_settings["mode"] = mode_text
    await message.answer("Режим сохранен.\n\nВведите торговую пару (например, `SOL_USDT` или `BTC_USDT`):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SettingsFSM.waiting_for_symbol)

@router.message(SettingsFSM.waiting_for_symbol)
async def process_symbol(message: Message, state: FSMContext):
    bot_settings["symbol"] = message.text.strip().upper()
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="5"), KeyboardButton(text="15")]
    ], resize_keyboard=True)
    await message.answer("Сохранено.\n\nВыберите таймфрейм (в минутах):", reply_markup=kb)
    await state.set_state(SettingsFSM.waiting_for_timeframe)

@router.message(SettingsFSM.waiting_for_timeframe)
async def process_timeframe(message: Message, state: FSMContext):
    try:
        tf = int(message.text.strip())
        if tf not in [1, 5, 15, 30, 60]:
            raise ValueError
    except ValueError:
        await message.answer("Неверный формат. Выберите 5 или 15.")
        return
        
    bot_settings["timeframe"] = tf
    await message.answer("Сохранено.\n\nВведите риск на сделку в процентах (например, `2.0` для 2%):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SettingsFSM.waiting_for_risk)

@router.message(SettingsFSM.waiting_for_risk)
async def process_risk(message: Message, state: FSMContext):
    try:
        risk = float(message.text.strip())
        if risk <= 0 or risk > 100:
            raise ValueError
    except ValueError:
        await message.answer("Некорректное значение. Введите число (например, 2.0).")
        return
        
    bot_settings["risk_percent"] = risk
    await message.answer("✅ Отлично! Настройки сохранены.", reply_markup=get_main_keyboard())
    await state.clear()

# --- ПУСТЫШКИ ДЛЯ КНОПОК ЗАПУСКА И ТЕСТОВ (будут реализованы в сл. шагах) ---

@router.message(F.text == "🚀 ЗАПУСК БОТА")
async def cmd_start_bot(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Запуск в разработке... (Перейдем к этому в Шаге 5)")

@router.message(F.text == "🛑 СТОП")
async def cmd_stop_bot(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Остановка движка...")

@router.message(F.text == "🧪 Тест Стратегий")
async def cmd_test(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Обработчик тестов будет добавлен в Шаге 4...")

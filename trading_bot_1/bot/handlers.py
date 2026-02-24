import logging
import asyncio
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

from core.backtest.optimizer import StrategyOptimizer
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.strategies.liquidity_sweep import LiquiditySweepStrategy

@router.message(F.text == "🧪 Тест Стратегий")
async def cmd_test(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"⏳ Начинаю скачивание истории и бэктест для `{bot_settings['symbol']}` на `{bot_settings['timeframe']}m`...\nЭто займет около 10-15 секунд.")
    
    # Запускаем в фоне, чтобы не блочить бота
    asyncio.create_task(run_optimizer_and_report(message))

async def run_optimizer_and_report(message: Message):
    import pandas as pd
    from core.data.historical import MEXCHistoricalDownloader
    
    symbol = bot_settings['symbol']
    tf = bot_settings['timeframe']
    
    try:
        # 1. Скачиваем данные
        df = await MEXCHistoricalDownloader.get_klines(symbol, tf, limit=3000)
        
        # 2. Настраиваем сетки для оптимизатора (по одной стратегии для теста)
        # Для скорости возьмем TrendPullback
        param_grid = {
            'rsi_threshold': [30, 40],
            'sl_atr_mult': [1.0, 1.5],
            'rr_ratio': [1.5, 2.0]
        }
        
        optimizer = StrategyOptimizer(data=df, strategy_class=TrendPullbackStrategy, param_grid=param_grid)
        results = optimizer.run_optimization()
        
        if not results:
            await message.answer("❌ Бэктест не нашел прибыльных параметров (сделок нет). Market is dead.")
            return
            
        # Берем лучший по ROI
        best = max(results, key=lambda x: x['ROI_%'])
        
        report = (
            f"✅ **Бэктест Завершен!**\n"
            f"Пара: `{symbol}` ({tf}m)\n\n"
            f"🏆 **Лучшая Стратегия:** `Trend Pullback`\n"
            f"⚙️ **Параметры:** `RSI={best['params']['rsi_threshold']}, SL={best['params']['sl_atr_mult']}ATR, RR={best['params']['rr_ratio']}`\n\n"
            f"📊 **Результаты (на 3000 свечей):**\n"
            f"• Профит: `{best['ROI_%']:.2f}%`\n"
            f"• Винрейт: `{best['WinRate_%']:.2f}%`\n"
            f"• Сделок: `{best['Total_Trades']}`\n"
            f"• Макс Просадка: `{best['Max_Drawdown_%']:.2f}%`\n"
            f"• Профит Фактор: `{best['Profit_Factor']:.2f}`\n\n"
            f"💡 *Рекомендация:* Сохраните эти параметры."
        )
        
        await message.answer(report)
        
    except Exception as e:
        logger.error(f"Ошибка бэктеста: {e}")
        await message.answer(f"❌ Произошла ошибка при тестировании: {e}")

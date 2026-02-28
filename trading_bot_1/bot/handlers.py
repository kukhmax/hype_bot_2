import logging
import asyncio
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
import os
from aiogram.fsm.context import FSMContext
from contextlib import suppress
from aiogram.exceptions import TelegramBadRequest

from bot.states import SettingsFSM, PairFSM, TestFSM, StrategyFSM
from bot.settings import bot_settings

logger = logging.getLogger("telegram_handlers")

router = Router()

# --- МЕНЕДЖЕР ДВИЖКОВ ---
from core.execution.engine_manager import EngineManager

_engine_manager: EngineManager = None


# Описания параметров стратегий (для отображения в Telegram)
STRATEGY_PARAM_LABELS = {
    "rsi_threshold": ("RSI Порог", "Порог RSI для откатов (TrendPullback). Чем выше — тем чаще сигналы."),
    "bb_width_threshold": ("BB Width", "Порог сжатия Боллинджера (Breakout). Чем выше — тем чаще сигналы."),
    "adx_threshold": ("ADX Порог", "Мин. сила тренда для Breakout. Чем ниже — тем чаще сигналы."),
    "sl_atr_mult": ("SL × ATR", "Множитель ATR для Stop Loss. Больше = шире стоп."),
    "rr_ratio": ("R:R Ratio", "Risk/Reward. Больше = дальше TP, но реже достигается."),
}


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🎯 Стратегии")],
            [KeyboardButton(text="➕ Добавить пару"), KeyboardButton(text="➖ Убрать пару")],
            [KeyboardButton(text="🧪 Тест Стратегий")],
            [KeyboardButton(text="🚀 ЗАПУСК БОТА"), KeyboardButton(text="🛑 СТОП")]
        ],
        resize_keyboard=True
    )


def get_delete_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Скрыть", callback_data="delete_msg")]]
    )

@router.callback_query(F.data == "delete_msg")
async def process_delete_msg(callback: CallbackQuery):
    with suppress(TelegramBadRequest, Exception):
        await callback.message.delete()
        await callback.answer()

async def delete_user_msg(message: Message):
    with suppress(TelegramBadRequest):
        await message.delete()

async def delete_previous_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("prompt_msg_id")
    if prompt_id:
        with suppress(TelegramBadRequest):
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)

async def save_prompt_id(msg: Message, state: FSMContext):
    await state.update_data(prompt_msg_id=msg.message_id)

def _pairs_summary() -> str:
    """Формирует строку со списком пар и их настройками."""
    pairs = bot_settings["pairs"]
    if not pairs:
        return "Пары не добавлены"
    lines = []
    for sym, cfg in pairs.items():
        lines.append(f"• `{sym}` — {cfg['tf']}m, {cfg['leverage']}x")
    return "\n".join(lines)


# --- /start ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()
    
    # Отправляем клавиатуру и оставляем сообщение, чтобы меню не пропадало
    await message.answer("🕹 **Меню активно 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    
    welcome_text = (
        "👋 Добро пожаловать в **Hype Bot (v2)**!\n\n"
        "Я автономный торговый бот для фьючерсов на MEXC.\n"
        "Мой арсенал:\n"
        "📈 *Trend Pullback* | ⚡️ *Volatility Breakout* | 🧲 *Liquidity Sweep*\n\n"
        f"🔧 **Пары:**\n{_pairs_summary()}\n\n"
        "Используйте меню ниже для навигации."
    )
    await message.answer(welcome_text, reply_markup=get_delete_kb())


# --- СКАЧАТЬ СДЕЛКИ ---

@router.message(Command("trades"))
async def cmd_download_trades(message: Message):
    await delete_user_msg(message)
    csv_path = "trades_history.csv"
    if os.path.exists(csv_path):
        doc = FSInputFile(csv_path)
        await message.answer_document(doc, caption="📈 История сделок (CSV)", reply_markup=get_delete_kb())
    else:
        await message.answer("⚠️ Файл с историей сделок пока не создан (нет закрытых сделок).", reply_markup=get_delete_kb())


# --- СТАТУС ---

@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()

    if _engine_manager and _engine_manager.is_running:
        statuses = _engine_manager.get_status()
        lines = ["📊 **АКТИВНЫЕ ДВИЖКИ**\n"]
        for s in statuses:
            if s["has_position"]:
                pnl = s.get("pnl", 0.0)
                pnl_pct = s.get("pnl_percent", 0.0)
                pos_icon = "🟢" if pnl >= 0 else "🔴"
                pos_text = f"{s['position_side']} | {pos_icon} {pnl:.2f} USDT ({pnl_pct:+.2f}%)"
            else:
                pos_icon = "⚪️"
                pos_text = "—"
                
            lines.append(
                f"{pos_icon} `{s['symbol']}` | "
                f"`{s['tf']}m` | "
                f"`{s['regime']}` | "
                f"`{s['strategy']}` | "
                f"`{s['leverage']}x` | "
                f"Поз: `{pos_text}`"
            )
        lines.append(f"\n⚙️ Режим: `{bot_settings['mode'].upper()}` | Риск: `{bot_settings['risk_percent']}%`")
        text = "\n".join(lines)
    else:
        text = (
            "📊 **ТЕКУЩИЙ СТАТУС**\n\n"
            f"Движок: `🛑 Остановлен`\n"
            f"Режим: `{bot_settings['mode'].upper()}`\n"
            f"Риск: `{bot_settings['risk_percent']}%`\n\n"
            f"**Пары:**\n{_pairs_summary()}"
        )
    # Отправляем сообщение со статусом и кнопкой Скрыть
    await message.answer(text, reply_markup=get_delete_kb())


# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ (режим + риск) ---

@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message, state: FSMContext):
    await delete_user_msg(message)
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="PAPER (Тест)"), KeyboardButton(text="LIVE (Бой)")],
        [KeyboardButton(text="SIGNALS (Только сигналы)")]
    ], resize_keyboard=True)
    msg = await message.answer("🛠 *НАСТРОЙКИ*\n\nВыберите Режим работы бота:", reply_markup=kb)
    await state.set_state(SettingsFSM.waiting_for_mode)
    await save_prompt_id(msg, state)

@router.message(SettingsFSM.waiting_for_mode)
async def process_mode(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    mode_text = message.text.split()[0].lower()
    if mode_text not in ["paper", "live", "signals"]:
        msg = await message.answer("Пожалуйста, выберите режим кнопкой.")
        await save_prompt_id(msg, state)
        return

    bot_settings["mode"] = mode_text
    logger.info(f"Пользователь {message.from_user.id} изменил режим работы: {mode_text}")
    msg = await message.answer("Сохранено.\n\nВведите риск на сделку в % (например `2.0`):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(SettingsFSM.waiting_for_risk)
    await save_prompt_id(msg, state)

@router.message(SettingsFSM.waiting_for_risk)
async def process_risk(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    try:
        risk = float(message.text.strip())
        if risk <= 0 or risk > 100:
            raise ValueError
    except ValueError:
        msg = await message.answer("Некорректное значение. Введите число (например, 2.0).")
        await save_prompt_id(msg, state)
        return

    bot_settings["risk_percent"] = risk
    logger.info(f"Пользователь {message.from_user.id} установил риск: {risk}%")
    
    # Восстанавливаем клавиатуру, не удаляя сообщение
    await message.answer("🕹 **Меню обновлено 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    
    await message.answer(
        f"✅ Настройки сохранены!\n\n"
        f"Режим: `{bot_settings['mode'].upper()}`\n"
        f"Риск: `{risk}%`\n\n"
        f"**Пары:**\n{_pairs_summary()}",
        reply_markup=get_delete_kb()
    )
    await state.clear()


# --- УПРАВЛЕНИЕ ПАРАМИ (с индивидуальными TF и Leverage) ---

@router.message(F.text == "➕ Добавить пару")
async def cmd_add_pair(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()
    msg = await message.answer(
        f"📝 **Текущие пары:**\n{_pairs_summary()}\n\n"
        "Введите символ для добавления (например `BTC_USDT`):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(PairFSM.waiting_for_symbol)
    await save_prompt_id(msg, state)

@router.message(PairFSM.waiting_for_symbol)
async def process_pair_symbol(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    symbol = message.text.strip().upper()

    if not symbol.endswith("USDT") and not symbol.endswith("_USDT"):
        msg = await message.answer("Неверный формат. Пара должна оканчиваться на USDT (напр. `SOL_USDT`).")
        await save_prompt_id(msg, state)
        return

    # Нормализуем
    if "_" not in symbol and symbol.endswith("USDT"):
        symbol = f"{symbol[:-4]}_USDT"

    if symbol in bot_settings["pairs"]:
        await message.answer("🕹 **Меню обновлено 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
        await message.answer(f"⚠️ Пара `{symbol}` уже добавлена.", reply_markup=get_delete_kb())
        await state.clear()
        return

    await state.update_data(new_symbol=symbol)

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1"), KeyboardButton(text="5"), KeyboardButton(text="15")],
        [KeyboardButton(text="30"), KeyboardButton(text="60")]
    ], resize_keyboard=True)
    msg = await message.answer(f"Пара: `{symbol}`\n\nВыберите таймфрейм (в минутах):", reply_markup=kb)
    await state.set_state(PairFSM.waiting_for_timeframe)
    await save_prompt_id(msg, state)

@router.message(PairFSM.waiting_for_timeframe)
async def process_pair_tf(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    try:
        tf = int(message.text.strip())
        if tf not in [1, 5, 15, 30, 60]:
            raise ValueError
    except ValueError:
        msg = await message.answer("Неверный формат. Выберите кнопкой (1, 5, 15, 30, 60).")
        await save_prompt_id(msg, state)
        return

    await state.update_data(new_tf=tf)

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1x"), KeyboardButton(text="3x"), KeyboardButton(text="5x")],
        [KeyboardButton(text="10x"), KeyboardButton(text="20x")]
    ], resize_keyboard=True)
    msg = await message.answer(f"Таймфрейм: `{tf}m`\n\nВыберите кредитное плечо:", reply_markup=kb)
    await state.set_state(PairFSM.waiting_for_leverage)
    await save_prompt_id(msg, state)

@router.message(PairFSM.waiting_for_leverage)
async def process_pair_leverage(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    text = message.text.strip().lower().replace("x", "")
    try:
        lev = int(text)
        if lev not in [1, 3, 5, 10, 20]:
            raise ValueError
    except ValueError:
        msg = await message.answer("Пожалуйста, выберите плечо кнопкой.")
        await save_prompt_id(msg, state)
        return

    data = await state.get_data()
    symbol = data["new_symbol"]
    tf = data["new_tf"]

    bot_settings["pairs"][symbol] = {"tf": tf, "leverage": lev}

    await message.answer("🕹 **Меню обновлено 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")

    await message.answer(
        f"✅ Пара `{symbol}` добавлена!\n"
        f"Таймфрейм: `{tf}m` | Плечо: `{lev}x`\n\n"
        f"**Все пары:**\n{_pairs_summary()}",
        reply_markup=get_delete_kb()
    )
    await state.clear()


@router.message(F.text == "➖ Убрать пару")
async def cmd_remove_pair(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()
    pairs = bot_settings["pairs"]

    if len(pairs) == 0:
        msg = await message.answer("⚠️ Список пар пуст.", reply_markup=get_delete_kb())
        return

    if len(pairs) == 1:
        sym = list(pairs.keys())[0]
        msg = await message.answer(f"⚠️ Нельзя удалить последнюю пару (`{sym}`). Добавьте другую сначала.", reply_markup=get_delete_kb())
        return

    kb_buttons = [[KeyboardButton(text=f"🗑 {s}")] for s in pairs]
    kb_buttons.append([KeyboardButton(text="❌ Отмена")])
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True)
    msg = await message.answer("Выберите пару для удаления:", reply_markup=kb)
    await state.set_state(PairFSM.waiting_for_symbol) # Переиспользуем состояние, чтобы знать, что мы удаляем
    # (На самом деле мы просто ждем сообщение, начинающееся с 🗑, но State поможет нам удалить prompt)
    await save_prompt_id(msg, state)


@router.message(F.text.startswith("🗑 "))
async def process_remove_pair(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    symbol = message.text.replace("🗑 ", "").strip()

    await message.answer("🕹 **Меню обновлено 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")

    if symbol in bot_settings["pairs"]:
        del bot_settings["pairs"][symbol]

        global _engine_manager
        if _engine_manager and symbol in _engine_manager.engines:
            await _engine_manager.remove_pair(symbol)
            await message.answer(f"🛑 Движок для `{symbol}` остановлен.", reply_markup=get_delete_kb())

        await message.answer(
            f"✅ Пара `{symbol}` удалена.\n\n**Осталось:**\n{_pairs_summary()}",
            reply_markup=get_delete_kb()
        )
    else:
        await message.answer("⚠️ Пара не найдена.", reply_markup=get_delete_kb())
    await state.clear()

@router.message(F.text == "❌ Отмена")
async def process_cancel(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    await state.clear()
    
    await message.answer("🕹 **Меню активно 👇**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    await message.answer("Отменено.", reply_markup=get_delete_kb())


# --- ЗАПУСК / СТОП ---

async def send_tg_notification(text: str):
    """Callback для LiveEngine, чтобы он мог писать в Telegram"""
    bot = bot_settings.get("bot_instance")
    
    if bot and bot_settings.get("chat_id"):
        try:
            await bot.send_message(chat_id=bot_settings["chat_id"], text=text, parse_mode="Markdown", reply_markup=get_delete_kb())
        except Exception as e:
            # Fallback: отправляем без Markdown если парсинг сломался
            logger.error(f"Ошибка отправки в Telegram (Markdown): {e}")
            try:
                # Обязательно указываем parse_mode=None, иначе применится дефолтный Markdown из telegram_bot.py
                await bot.send_message(chat_id=bot_settings["chat_id"], text=text, parse_mode=None, reply_markup=get_delete_kb())
            except Exception as e2:
                logger.error(f"Ошибка отправки в Telegram (plain): {e2}")
    else:
        logger.warning(f"ПРОПУСК ОТПРАВКИ ТГ: bot={bot is not None}, chat_id={bot_settings.get('chat_id')}. Message preview: {text[:50]}")


@router.message(F.text == "🚀 ЗАПУСК БОТА")
async def cmd_start_bot(message: Message, state: FSMContext):
    await delete_user_msg(message)
    global _engine_manager
    await state.clear()

    if _engine_manager and _engine_manager.is_running:
        logger.warning(f"Пользователь {message.from_user.id} попытался запустить уже запущенного бота")
        await message.answer("⚠️ Бот УЖЕ запущен!", reply_markup=get_delete_kb())
        return

    logger.info(f"!!! ПОЛЬЗОВАТЕЛЬ {message.from_user.id} ДАЛ КОМАНДУ НА ЗАПУСК БОТА !!!")
    bot_settings["chat_id"] = message.chat.id
    mode = bot_settings["mode"]
    pairs = bot_settings["pairs"]

    if not pairs:
        await message.answer("⚠️ Список пар пуст. Добавьте хотя бы одну пару.", reply_markup=get_delete_kb())
        return

    lines = [f"🚀 Инициализация движков... Режим: `{mode.upper()}`\n"]
    for sym, cfg in pairs.items():
        lines.append(f"• `{sym}` — {cfg['tf']}m, {cfg['leverage']}x")
    await message.answer("\n".join(lines), reply_markup=get_delete_kb())

    paper_trading = mode != "live"
    _engine_manager = EngineManager()

    success_count = 0
    for symbol, cfg in pairs.items():
        ok = await _engine_manager.add_pair(
            symbol=symbol,
            timeframe=cfg["tf"],
            leverage=cfg["leverage"],
            paper_trading=paper_trading,
            tg_callback=send_tg_notification,
            strategy_params=bot_settings.get("strategy_params", {})
        )
        if ok:
            success_count += 1
            await message.answer(f"✅ `{symbol}` ({cfg['tf']}m, {cfg['leverage']}x) — запущен", reply_markup=get_delete_kb())
        else:
            await message.answer(f"❌ `{symbol}` — ошибка инициализации", reply_markup=get_delete_kb())

    if success_count > 0:
        await message.answer(
            f"🟢 **Запущено {success_count}/{len(pairs)} движков!**\nСлушаю потоки и жду сигналы...", reply_markup=get_delete_kb()
        )
    else:
        await message.answer("❌ Ни один движок не запустился. Проверьте логи.", reply_markup=get_delete_kb())
        _engine_manager = None


@router.message(F.text == "🛑 СТОП")
async def cmd_stop_bot(message: Message, state: FSMContext):
    await delete_user_msg(message)
    global _engine_manager
    await state.clear()

    if _engine_manager and _engine_manager.is_running:
        logger.info(f"!!! ПОЛЬЗОВАТЕЛЬ {message.from_user.id} ДАЛ КОМАНДУ СТОП !!!")
        count = len(_engine_manager.engines)
        await _engine_manager.stop_all()
        _engine_manager = None
        await message.answer(f"🛑 Остановлено {count} движков.", reply_markup=get_delete_kb())
    else:
        await message.answer("⚠️ Бот и так не запущен.", reply_markup=get_delete_kb())


from core.backtest.optimizer import StrategyOptimizer
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.strategies.liquidity_sweep import LiquiditySweepStrategy

@router.message(F.text == "🧪 Тест Стратегий")
async def cmd_test_strategies(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()
    pairs = bot_settings["pairs"]

    if not pairs:
        msg = await message.answer("⚠️ Добавьте хотя бы одну пару.", reply_markup=get_delete_kb())
        return

    if len(pairs) == 1:
        symbol = list(pairs.keys())[0]
        tf = pairs[symbol]["tf"]
        msg = await message.answer(
            f"⏳ Запускаю полный бэктест `{symbol}` ({tf}m)...\n"
            "Тестирую 3 стратегии. Это займёт 1-2 минуты.",
            reply_markup=get_delete_kb()
        )
        asyncio.create_task(run_optimizer_and_report(message, symbol, tf))
    else:
        kb_buttons = [[KeyboardButton(text=f"🧪 {s}")] for s in pairs]
        kb_buttons.append([KeyboardButton(text="❌ Отмена")])
        kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True)
        msg = await message.answer("Выберите пару для тестирования:", reply_markup=kb)
        await state.set_state(TestFSM.waiting_for_pair_choice)
        await save_prompt_id(msg, state)


@router.message(TestFSM.waiting_for_pair_choice)
async def process_test_pair_choice(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    if message.text == "❌ Отмена":
        await state.clear()
        
        kb_msg = await message.answer("🔄", reply_markup=get_main_keyboard())
        await delete_user_msg(kb_msg)
        await message.answer("Отменено.", reply_markup=get_delete_kb())
        return

    symbol = message.text.replace("🧪 ", "").strip()
    pairs = bot_settings["pairs"]

    if symbol not in pairs:
        msg = await message.answer("⚠️ Пара не найдена. Выберите кнопкой.")
        await save_prompt_id(msg, state)
        return

    tf = pairs[symbol]["tf"]
    await state.clear()
    
    kb_msg = await message.answer("🔄", reply_markup=get_main_keyboard())
    await delete_user_msg(kb_msg)
    
    await message.answer(
        f"⏳ Запускаю полный бэктест `{symbol}` ({tf}m)...\n"
        "Тестирую 3 стратегии. Это займёт 1-2 минуты.",
        reply_markup=get_delete_kb()
    )
    asyncio.create_task(run_optimizer_and_report(message, symbol, tf))


async def run_optimizer_and_report(message: Message, symbol: str, tf: int):
    from core.data.historical import MEXCHistoricalDownloader
    import pandas as pd

    try:
        df = await MEXCHistoricalDownloader.get_klines(symbol, tf, limit=3000)

        # Сохраняем сырые данные как dict — полностью изолировано от pandas
        raw_data = {col: df[col].tolist() for col in df.columns}
        del df  # Освобождаем оригинальный DataFrame

        # --- Тестируем все 3 стратегии ---
        strategies_config = [
            {
                "name": "Trend Pullback",
                "class": TrendPullbackStrategy,
                "grid": {
                    'rsi_threshold': [30, 35, 40, 45],
                    'sl_atr_mult': [1.0, 1.5, 2.0],
                    'rr_ratio': [1.5, 2.0, 2.5]
                }
            },
            {
                "name": "Volatility Breakout",
                "class": BreakoutStrategy,
                "grid": {
                    'bb_width_threshold': [0.01, 0.02, 0.03],
                    'adx_threshold': [15, 20, 25],
                    'sl_atr_mult': [1.0, 1.5],
                    'rr_ratio': [1.5, 2.0]
                }
            },
            {
                "name": "Liquidity Sweep",
                "class": LiquiditySweepStrategy,
                "grid": {}  # Без параметров (дефолтные)
            }
        ]

        all_results = []

        for strat_cfg in strategies_config:
            # Создаём абсолютно новый DataFrame из сырых данных для каждой стратегии
            fresh_df = pd.DataFrame(raw_data)
            optimizer = StrategyOptimizer(data=fresh_df, strategy_class=strat_cfg["class"])

            if strat_cfg["grid"]:
                results = optimizer.optimize(param_grid=strat_cfg["grid"])
            else:
                results = optimizer.optimize(param_grid={})

            # Фильтруем: оставляем только результаты с хотя бы 1 сделкой
            for r in results:
                if r["total_trades"] > 0:
                    r["strategy_name"] = strat_cfg["name"]
                    all_results.append(r)

        if not all_results:
            await message.answer(
                f"❌ **Бэктест `{symbol}` ({tf}m)**\n\n"
                "Ни одна стратегия не нашла сигналов на данных 3000 свечей.\n"
                "Попробуйте другой таймфрейм или монету.",
                reply_markup=get_delete_kb()
            )
            return

        # Сортируем по ROI
        all_results.sort(key=lambda x: x['roi'], reverse=True)
        best = all_results[0]

        params_str = ", ".join(f"{k}={v}" for k, v in best.get("params", {}).items())
        if not params_str:
            params_str = "default"

        report = (
            f"✅ **Бэктест Завершён!**\n"
            f"Пара: `{symbol}` ({tf}m)\n\n"
            f"🏆 **Лучшая:** `{best['strategy_name']}`\n"
            f"⚙️ **Параметры:** `{params_str}`\n\n"
            f"📊 **Результаты (3000 свечей):**\n"
            f"• Профит: `{best['roi']:.2f}%`\n"
            f"• Винрейт: `{best['winrate']:.1f}%`\n"
            f"• Сделок: `{best['total_trades']}`\n"
            f"• Макс Просадка: `{best['max_drawdown']:.2f}%`\n"
            f"• Профит Фактор: `{best['profit_factor']:.2f}`\n"
        )

        # Добавляем TOP-3 если есть
        if len(all_results) > 1:
            report += "\n📋 **TOP-3 Стратегий:**\n"
            for i, r in enumerate(all_results[:3]):
                p_str = ", ".join(f"{k}={v}" for k, v in r.get("params", {}).items()) or "default"
                report += (
                    f"`#{i+1}` {r['strategy_name']} | "
                    f"ROI: `{r['roi']:.2f}%` | "
                    f"WR: `{r['winrate']:.0f}%` | "
                    f"Сделок: `{r['total_trades']}`\n"
                )

        await message.answer(report, reply_markup=get_delete_kb())

    except Exception as e:
        logger.error(f"Ошибка бэктеста: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при тестировании. Проверьте логи.", parse_mode=None, reply_markup=get_delete_kb())


# --- НАСТРОЙКИ СТРАТЕГИЙ ---

def _strategy_params_summary() -> str:
    """Формирует строку с текущими параметрами стратегий."""
    sp = bot_settings.get("strategy_params", {})
    lines = []
    for key, (label, _desc) in STRATEGY_PARAM_LABELS.items():
        val = sp.get(key, "—")
        lines.append(f"• **{label}**: `{val}`")
    return "\n".join(lines)


@router.message(F.text == "🎯 Стратегии")
async def cmd_strategy_settings(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await state.clear()
    
    text = (
        "🎯 **ПАРАМЕТРЫ СТРАТЕГИЙ**\n\n"
        f"{_strategy_params_summary()}\n\n"
        "Выберите параметр для изменения:"
    )
    
    # Генерируем кнопки для каждого параметра
    buttons = []
    sp = bot_settings.get("strategy_params", {})
    for key, (label, _desc) in STRATEGY_PARAM_LABELS.items():
        val = sp.get(key, 0)
        buttons.append([KeyboardButton(text=f"{label} [{val}]")])
    buttons.append([KeyboardButton(text="🔙 Назад")])
    
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    msg = await message.answer(text, reply_markup=kb)
    await state.set_state(StrategyFSM.waiting_for_param_choice)
    await save_prompt_id(msg, state)


@router.message(StrategyFSM.waiting_for_param_choice)
async def process_strategy_param_choice(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    text = message.text.strip()
    
    if text == "🔙 Назад":
        await state.clear()
        
        kb_msg = await message.answer("🔄", reply_markup=get_main_keyboard())
        await delete_user_msg(kb_msg)
        await message.answer("Главное меню.", reply_markup=get_delete_kb())
        return
    
    # Ищем параметр по label
    chosen_key = None
    for key, (label, desc) in STRATEGY_PARAM_LABELS.items():
        if text.startswith(label):
            chosen_key = key
            break
    
    if not chosen_key:
        msg = await message.answer("Выберите параметр кнопкой.")
        await save_prompt_id(msg, state)
        return
    
    label, desc = STRATEGY_PARAM_LABELS[chosen_key]
    current = bot_settings.get("strategy_params", {}).get(chosen_key, 0)
    
    await state.update_data(editing_param=chosen_key)
    msg = await message.answer(
        f"✏️ **{label}**\n\n"
        f"{desc}\n\n"
        f"Текущее значение: `{current}`\n"
        f"Введите новое значение:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(StrategyFSM.waiting_for_value)
    await save_prompt_id(msg, state)


@router.message(StrategyFSM.waiting_for_value)
async def process_strategy_param_value(message: Message, state: FSMContext):
    await delete_user_msg(message)
    await delete_previous_prompt(message, state)
    
    try:
        value = float(message.text.strip())
        if value <= 0:
            raise ValueError
    except ValueError:
        msg = await message.answer("Некорректное значение. Введите положительное число (например, `45` или `0.025`).")
        await save_prompt_id(msg, state)
        return
    
    data = await state.get_data()
    param_key = data.get("editing_param")
    
    if not param_key or param_key not in STRATEGY_PARAM_LABELS:
        await state.clear()
        
        kb_msg = await message.answer("🔄", reply_markup=get_main_keyboard())
        await delete_user_msg(kb_msg)
        await message.answer("Ошибка. Попробуйте снова.", reply_markup=get_delete_kb())
        return
    
    # Сохраняем
    sp = bot_settings.get("strategy_params", {})
    old_value = sp.get(param_key, 0)
    sp[param_key] = value
    bot_settings["strategy_params"] = sp
    
    label, _ = STRATEGY_PARAM_LABELS[param_key]
    logger.info(f"Пользователь {message.from_user.id} изменил {param_key}: {old_value} → {value}")
    
    await state.clear()
    
    kb_msg = await message.answer("🔄", reply_markup=get_main_keyboard())
    await delete_user_msg(kb_msg)
    
    await message.answer(
        f"✅ **{label}** изменён: `{old_value}` → `{value}`\n\n"
        f"📋 Текущие параметры:\n{_strategy_params_summary()}\n\n"
        f"⚠️ Изменения применятся при следующем **🚀 ЗАПУСК БОТА**.",
        reply_markup=get_delete_kb()
    )

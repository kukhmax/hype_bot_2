"""
Telegram бот на aiogram 3.
Управляет несколькими MultiTFEngine (MEXC Futures).
Получает MTFContext → отправляет сигнал с кнопками подтверждения/пропуска.
Таймер: если пользователь не ответил за CONFIRM_TIMEOUT_SEC — сигнал устаревает.
"""
import asyncio
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

from bot.keyboards import (
    main_menu_kb, popular_pairs_kb, settings_kb,
    min_patterns_kb, min_confidence_kb, confirm_timeout_kb,
    signal_action_kb, signal_confirmed_kb, signal_skipped_kb,
    timeframe_select_kb, active_pairs_kb
)
from bot.signal_formatter import (
    format_mtf_signal, format_signal_confirmed,
    format_signal_skipped, format_signal_expired, format_status,
)
from core.multi_tf_engine import MultiTFEngine, MTFContext
from config import config
from utils.logger import logger


# ─── FSM States ───────────────────────────────────────────────────────────────

class BotStates(StatesGroup):
    entering_custom_pair = State()


# ─── Pending Signal ───────────────────────────────────────────────────────────

class PendingSignal:
    """Хранит активный сигнал + message_id для редактирования + таймер."""

    def __init__(self, ctx: MTFContext, message_id: int, chat_id: int):
        self.ctx = ctx
        self.message_id = message_id
        self.chat_id = chat_id
        self.signal_id = str(int(ctx.signal.entry * 1000))[-8:]  # короткий ID
        self._timer_task: Optional[asyncio.Task] = None

    def start_timer(self, timeout_sec: int, on_expire):
        self._timer_task = asyncio.create_task(self._expire(timeout_sec, on_expire))

    async def _expire(self, timeout_sec: int, on_expire):
        await asyncio.sleep(timeout_sec)
        await on_expire(self)

    def cancel_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()


# ─── Bot ──────────────────────────────────────────────────────────────────────

class TradingBot:
    def __init__(self):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        # Состояние сессии
        self.active_monitors: dict[str, dict] = {} # symbol -> {"engine": engine, "task": task}
        self.chat_id: Optional[int] = config.TELEGRAM_CHAT_ID

        # Активный pending-сигнал (только один одновременно)
        self._pending: Optional[PendingSignal] = None

        self._register_handlers()

    # ─── Регистрация хэндлеров ────────────────────────────────────────────────

    def _register_handlers(self):
        dp = self.dp

        # Команды
        dp.message.register(self.cmd_start,  Command("start"))
        dp.message.register(self.cmd_status, Command("status"))
        dp.message.register(self.cmd_stop_all, Command("stop_all"))

        # Главное меню
        dp.callback_query.register(self.cb_add_pair,      F.data == "add_pair")
        dp.callback_query.register(self.cb_stop_pair_menu,F.data == "stop_pair_menu")
        dp.callback_query.register(self.cb_stop_all,      F.data == "stop_all")
        dp.callback_query.register(self.cb_status,        F.data == "status")
        dp.callback_query.register(self.cb_back_main,     F.data == "back_main")

        # Добавление пары и ТФ
        dp.callback_query.register(self.cb_pair_select, F.data.startswith("pair_"))
        dp.callback_query.register(self.cb_tf_select,   F.data.startswith("tf_"))
        dp.message.register(self.fsm_custom_pair, BotStates.entering_custom_pair)
        
        # Остановка конкретной пары
        dp.callback_query.register(self.cb_stop_specific_pair, F.data.startswith("stop_"))

        # Настройки
        dp.callback_query.register(self.cb_settings,     F.data == "settings")
        dp.callback_query.register(self.cb_set_min_pat,  F.data == "set_min_patterns")
        dp.callback_query.register(self.cb_set_min_conf, F.data == "set_min_conf")
        dp.callback_query.register(self.cb_set_timeout,  F.data == "set_timeout")
        dp.callback_query.register(self.cb_mp,  F.data.startswith("mp_"))
        dp.callback_query.register(self.cb_mc,  F.data.startswith("mc_"))
        dp.callback_query.register(self.cb_ct,  F.data.startswith("ct_"))

        # Сигнал: ключевые кнопки
        dp.callback_query.register(self.cb_sig_confirm, F.data.startswith("sig_confirm_"))
        dp.callback_query.register(self.cb_sig_skip,    F.data.startswith("sig_skip_"))
        dp.callback_query.register(self.cb_sig_details, F.data.startswith("sig_details_"))

    # ─── Утилиты ──────────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        return len(self.active_monitors) > 0

    def active_pairs_list_str(self) -> str:
        if not self.active_monitors:
            return "🔴 Нет активных мониторингов."
        return "\n".join([f"🟢 `{pair}`" for pair in self.active_monitors])

    # ─── Команды ──────────────────────────────────────────────────────────────

    async def cmd_start(self, msg: Message, state: FSMContext):
        self.chat_id = msg.chat.id
        await state.clear()
        await msg.answer(
            f"👋 *Trading Signal Bot V2*\n\n"
            f"📡 MEXC Futures · Мульти-мониторинг\n"
            f"🔍 Свечные паттерны · Индикаторы · AI Gemini\n\n"
            f"Активные пары:\n{self.active_pairs_list_str()}\n\n"
            f"Выбери действие:",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )

    async def cmd_status(self, msg: Message):
        await msg.answer(format_status(self.active_monitors), parse_mode="Markdown")

    async def cmd_stop_all(self, msg: Message):
        await self._stop_all()
        await msg.answer("⏹ Все мониторинги остановлены.", reply_markup=main_menu_kb())

    # ─── Главное меню ─────────────────────────────────────────────────────────

    async def cb_status(self, cq: CallbackQuery):
        try:
            await cq.message.edit_text(
                format_status(self.active_monitors), reply_markup=main_menu_kb(), parse_mode="Markdown"
            )
        except Exception:
            pass
        await cq.answer()

    async def cb_back_main(self, cq: CallbackQuery, state: FSMContext):
        await state.clear()
        try:
            await cq.message.edit_text(
                f"Список пар:\n{self.active_pairs_list_str()}",
                reply_markup=main_menu_kb(), parse_mode="Markdown",
            )
        except Exception:
            pass
        await cq.answer()

    # ─── Добавление пары ──────────────────────────────────────────────────────

    async def cb_add_pair(self, cq: CallbackQuery):
        await cq.message.edit_text(
            "📊 Выбери пару для добавления:",
            reply_markup=popular_pairs_kb(),
        )
        await cq.answer()

    async def cb_pair_select(self, cq: CallbackQuery, state: FSMContext):
        data = cq.data.replace("pair_", "")
        if data == "custom":
            await cq.message.edit_text("✏️ Введи пару (например: `SOL_USDT`):", parse_mode="Markdown")
            await state.set_state(BotStates.entering_custom_pair)
            await cq.answer()
            return
        
        await cq.message.edit_text(
            f"✅ Пара: `{data}`\nВыбери таймфреймы (Вход + Тренд):",
            reply_markup=timeframe_select_kb(data),
            parse_mode="Markdown",
        )
        await cq.answer()

    async def fsm_custom_pair(self, msg: Message, state: FSMContext):
        pair = msg.text.strip().upper()
        if "_" not in pair:
            pair = pair.replace("USDT", "_USDT")
        await state.clear()
        await msg.answer(
            f"✅ Пара: `{pair}`\nВыбери таймфреймы (Вход + Тренд):",
            reply_markup=timeframe_select_kb(pair),
            parse_mode="Markdown",
        )

    async def cb_tf_select(self, cq: CallbackQuery):
        # Format: tf_BTC_USDT_15m_1h
        parts = cq.data.split("_")
        # tf_1 = "tf"
        # Since pair might have an underscore: tf, BTC, USDT, 15m, 1h
        entry_tf = parts[-2]
        trend_tf = parts[-1]
        pair = "_".join(parts[1:-2])

        if pair in self.active_monitors:
            await cq.answer(f"⚠️ {pair} уже мониторится!", show_alert=True)
            return

        await cq.message.edit_text(
            f"🚀 Запускаю мониторинг...\n\n"
            f"Пара: `{pair}`\n"
            f"ТФ: `{entry_tf} + {trend_tf}`\n"
            f"Мин. паттернов: {config.MIN_PATTERNS_TO_SIGNAL}\n"
            f"Мин. confluence: {config.MIN_CONFIDENCE}%",
            parse_mode="Markdown",
        )
        await cq.answer()
        await self._start_monitor(cq.message.chat.id, pair, entry_tf, trend_tf)

    # ─── Остановка пары ───────────────────────────────────────────────────────

    async def cb_stop_all(self, cq: CallbackQuery):
        await self._stop_all()
        await cq.message.edit_text("⏹ Все мониторинги остановлены.", reply_markup=main_menu_kb())
        await cq.answer("Остановлено")

    async def cb_stop_pair_menu(self, cq: CallbackQuery):
        if not self.active_monitors:
            await cq.answer("Нет активных пар.", show_alert=True)
            return
        
        await cq.message.edit_text(
            "Выбери пару для остановки:",
            reply_markup=active_pairs_kb(list(self.active_monitors.keys())),
        )
        await cq.answer()
        
    async def cb_stop_specific_pair(self, cq: CallbackQuery):
        pair = cq.data.replace("stop_", "")
        if pair not in self.active_monitors:
            await cq.answer("Пара уже остановлена.", show_alert=True)
            return
            
        await self._stop_monitor(pair)
        await cq.message.edit_text(
            f"⏹ `{pair}` остановлен.\n\n" + self.active_pairs_list_str(),
            reply_markup=main_menu_kb(),
            parse_mode="Markdown"
        )
        await cq.answer(f"{pair} остановлен")

    # ─── Настройки ────────────────────────────────────────────────────────────

    async def cb_settings(self, cq: CallbackQuery):
        await cq.message.edit_text(
            f"⚙️ *Настройки стратегии*\n\n"
            f"Мин. паттернов: `{config.MIN_PATTERNS_TO_SIGNAL}`\n"
            f"Мин. confluence: `{config.MIN_CONFIDENCE}%`\n"
            f"Таймаут сигнала: `{config.CONFIRM_TIMEOUT_SEC // 60} мин`",
            reply_markup=settings_kb(), parse_mode="Markdown",
        )
        await cq.answer()

    async def cb_set_min_pat(self, cq: CallbackQuery):
        await cq.message.edit_text("Выбери мин. кол-во паттернов:", reply_markup=min_patterns_kb())
        await cq.answer()

    async def cb_set_min_conf(self, cq: CallbackQuery):
        await cq.message.edit_text("Мин. Confluence Score для сигнала:", reply_markup=min_confidence_kb())
        await cq.answer()

    async def cb_set_timeout(self, cq: CallbackQuery):
        await cq.message.edit_text(
            "⏱ Через сколько сигнал устаревает\n(если не подтвердил):",
            reply_markup=confirm_timeout_kb(),
        )
        await cq.answer()

    async def cb_mp(self, cq: CallbackQuery):
        config.MIN_PATTERNS_TO_SIGNAL = int(cq.data.replace("mp_", ""))
        await cq.message.edit_text(
            f"✅ Мин. паттернов: `{config.MIN_PATTERNS_TO_SIGNAL}`",
            reply_markup=main_menu_kb(), parse_mode="Markdown",
        )
        await cq.answer()

    async def cb_mc(self, cq: CallbackQuery):
        config.MIN_CONFIDENCE = int(cq.data.replace("mc_", ""))
        await cq.message.edit_text(
            f"✅ Мин. confluence: `{config.MIN_CONFIDENCE}%`",
            reply_markup=main_menu_kb(), parse_mode="Markdown",
        )
        await cq.answer()

    async def cb_ct(self, cq: CallbackQuery):
        config.CONFIRM_TIMEOUT_SEC = int(cq.data.replace("ct_", ""))
        await cq.message.edit_text(
            f"✅ Таймаут: `{config.CONFIRM_TIMEOUT_SEC // 60} мин`",
            reply_markup=main_menu_kb(), parse_mode="Markdown",
        )
        await cq.answer()

    # ─── Обработка кнопок сигнала ─────────────────────────────────────────────

    async def cb_sig_confirm(self, cq: CallbackQuery):
        """Пользователь нажал «ВОЙТИ В СДЕЛКУ»."""
        signal_id = cq.data.replace("sig_confirm_", "")
        pending = self._pending

        if not pending or pending.signal_id != signal_id:
            await cq.answer("⚠️ Сигнал уже недействителен", show_alert=True)
            return

        pending.cancel_timer()
        self._pending = None

        # Редактируем сообщение — убираем кнопки, показываем подтверждение
        try:
            await cq.message.edit_text(
                format_signal_confirmed(pending.ctx),
                reply_markup=signal_confirmed_kb(),
                parse_mode="Markdown",
            )
        except Exception:
            await self.bot.send_message(
                pending.chat_id,
                format_signal_confirmed(pending.ctx),
                reply_markup=signal_confirmed_kb(),
                parse_mode="Markdown",
            )

        await cq.answer("✅ Вход подтверждён!")
        logger.info(f"[Bot] ✅ Пользователь подтвердил вход: {pending.ctx.signal.symbol}")

    async def cb_sig_skip(self, cq: CallbackQuery):
        """Пользователь нажал «Пропустить»."""
        signal_id = cq.data.replace("sig_skip_", "")
        pending = self._pending

        if not pending or pending.signal_id != signal_id:
            await cq.answer("⚠️ Сигнал уже недействителен", show_alert=True)
            return

        pending.cancel_timer()
        self._pending = None

        try:
            await cq.message.edit_text(
                format_signal_skipped(pending.ctx),
                reply_markup=signal_skipped_kb(),
                parse_mode="Markdown",
            )
        except Exception:
            pass

        await cq.answer("⏭ Пропущено")
        logger.info(f"[Bot] ⏭ Пользователь пропустил: {pending.ctx.signal.symbol}")

    async def cb_sig_details(self, cq: CallbackQuery):
        """Показывает детальную информацию о сигнале всплывающим окном."""
        signal_id = cq.data.replace("sig_details_", "")
        pending = self._pending

        if not pending or pending.signal_id != signal_id:
            await cq.answer("Сигнал недействителен", show_alert=True)
            return

        s = pending.ctx.signal
        ind = s.indicators
        detail = (
            f"🔍 Паттерны ({len(s.patterns)}):\n" +
            "\n".join(f"• {p.name} [{int(p.strength*100)}%] — {p.description}" for p in s.patterns) +
            f"\n\nГемини: {s.gemini_analysis[:200] if s.gemini_analysis else '—'}"
        )
        await cq.answer(detail[:200], show_alert=True)

    # ─── Сигнал из движка ─────────────────────────────────────────────────────

    async def _on_signal(self, ctx: MTFContext):
        """Вызывается MultiTFEngine при новом сигнале."""
        if not self.chat_id:
            return

        # Если есть активный pending — отменяем
        if self._pending:
            self._pending.cancel_timer()
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=self._pending.chat_id,
                    message_id=self._pending.message_id,
                    reply_markup=None,
                )
            except Exception:
                pass
            self._pending = None

        # Создаём pending
        pending = PendingSignal(ctx, message_id=0, chat_id=self.chat_id)
        text = format_mtf_signal(ctx)
        kb   = signal_action_kb(pending.signal_id)

        try:
            sent = await self.bot.send_message(
                self.chat_id,
                text,
                reply_markup=kb,
                parse_mode="Markdown",
            )
            pending.message_id = sent.message_id
            self._pending = pending

            # Запускаем таймер истечения
            pending.start_timer(config.CONFIRM_TIMEOUT_SEC, self._on_signal_expire)

        except Exception as e:
            logger.error(f"[Bot] Ошибка отправки сигнала: {e}")

    async def _on_signal_expire(self, pending: PendingSignal):
        """Вызывается когда таймаут истёк."""
        if self._pending is not pending:
            return  # уже обработан
        self._pending = None

        logger.info(f"[Bot] ⏰ Сигнал истёк: {pending.ctx.signal.symbol}")
        try:
            await self.bot.edit_message_text(
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                text=format_signal_expired(pending.ctx),
                reply_markup=None,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"[Bot] Не удалось обновить истёкший сигнал: {e}")

    # ─── Monitor ──────────────────────────────────────────────────────────────

    async def _start_monitor(self, chat_id: int, pair: str, entry_tf: str, trend_tf: str):
        self.chat_id = chat_id
        engine = MultiTFEngine(
            symbol=pair,
            entry_tf=entry_tf,
            trend_tf=trend_tf,
            on_signal=self._on_signal,
        )
        task = asyncio.create_task(engine.start())
        self.active_monitors[pair] = {
            "engine": engine,
            "task": task
        }

        await self.bot.send_message(
            chat_id,
            f"✅ *Мониторинг запущен*\n\n"
            f"Пара: `{pair}` · MEXC Futures\n"
            f"Таймфреймы: `{entry_tf}` + `{trend_tf}`\n"
            f"Мин. паттернов: `{config.MIN_PATTERNS_TO_SIGNAL}`\n"
            f"Мин. confluence: `{config.MIN_CONFIDENCE}%`\n"
            f"⏳ Загружаю историю свечей...",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )
        logger.info(f"[Bot] Мониторинг запущен: {pair} [{entry_tf} + {trend_tf}]")

    async def _stop_monitor(self, pair: str):
        if pair in self.active_monitors:
            monitor = self.active_monitors[pair]
            await monitor["engine"].stop()
            task = monitor["task"]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.active_monitors[pair]
            logger.info(f"[Bot] Мониторинг {pair} остановлен")

    async def _stop_all(self):
        if self._pending:
            self._pending.cancel_timer()
            self._pending = None
            
        pairs = list(self.active_monitors.keys())
        for pair in pairs:
            await self._stop_monitor(pair)
            
        logger.info("[Bot] Все мониторинги остановлены")

    # ─── Run ──────────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("[Bot] Запуск polling...")
        await self.dp.start_polling(
            self.bot,
            allowed_updates=["message", "callback_query"],
        )

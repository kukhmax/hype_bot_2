"""
Telegram бот на aiogram 3.
Управляет MultiTFEngine (MEXC Futures, 15m+1h).
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
        self.symbol: str = config.DEFAULT_SYMBOL
        self.is_running: bool = False
        self.chat_id: Optional[int] = None
        self._engine: Optional[MultiTFEngine] = None
        self._engine_task: Optional[asyncio.Task] = None

        # Активный pending-сигнал (только один одновременно)
        self._pending: Optional[PendingSignal] = None

        self._register_handlers()

    # ─── Регистрация хэндлеров ────────────────────────────────────────────────

    def _register_handlers(self):
        dp = self.dp

        # Команды
        dp.message.register(self.cmd_start,  Command("start"))
        dp.message.register(self.cmd_status, Command("status"))
        dp.message.register(self.cmd_stop,   Command("stop"))
        dp.message.register(self.cmd_pair,   Command("pair"))

        # Главное меню
        dp.callback_query.register(self.cb_start_monitor, F.data == "start_monitor")
        dp.callback_query.register(self.cb_stop_monitor,  F.data == "stop_monitor")
        dp.callback_query.register(self.cb_status,        F.data == "status")
        dp.callback_query.register(self.cb_change_pair,   F.data == "change_pair")
        dp.callback_query.register(self.cb_back_main,     F.data == "back_main")

        # Выбор пары
        dp.callback_query.register(self.cb_pair_select, F.data.startswith("pair_"))
        dp.message.register(self.fsm_custom_pair, BotStates.entering_custom_pair)

        # Настройки
        dp.callback_query.register(self.cb_settings,     F.data == "settings")
        dp.callback_query.register(self.cb_set_min_pat,  F.data == "set_min_patterns")
        dp.callback_query.register(self.cb_set_min_conf, F.data == "set_min_conf")
        dp.callback_query.register(self.cb_set_timeout,  F.data == "set_timeout")
        dp.callback_query.register(self.cb_mp,  F.data.startswith("mp_"))
        dp.callback_query.register(self.cb_mc,  F.data.startswith("mc_"))
        dp.callback_query.register(self.cb_ct,  F.data.startswith("ct_"))

        # ── Сигнал: ключевые кнопки ──
        dp.callback_query.register(self.cb_sig_confirm, F.data.startswith("sig_confirm_"))
        dp.callback_query.register(self.cb_sig_skip,    F.data.startswith("sig_skip_"))
        dp.callback_query.register(self.cb_sig_details, F.data.startswith("sig_details_"))

    # ─── Команды ──────────────────────────────────────────────────────────────

    async def cmd_start(self, msg: Message, state: FSMContext):
        self.chat_id = msg.chat.id
        await state.clear()
        await msg.answer(
            "👋 *Trading Signal Bot*\n\n"
            "📡 *MEXC Futures* · мультитаймфрейм `15m + 1h`\n"
            "🔍 12 свечных паттернов · ADX/RSI/CCI · AI Gemini\n\n"
            f"Текущая пара: `{self.symbol}`\n\n"
            "Выбери действие:",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )

    async def cmd_status(self, msg: Message):
        await msg.answer(self._status_text(), parse_mode="Markdown")

    async def cmd_stop(self, msg: Message):
        await self._stop_monitor()
        await msg.answer("⏹ Мониторинг остановлен.", reply_markup=main_menu_kb())

    async def cmd_pair(self, msg: Message):
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.answer("Использование: `/pair BTC_USDT`", parse_mode="Markdown")
            return
        self.symbol = parts[1].upper()
        await msg.answer(f"✅ Пара: `{self.symbol}`\nПерезапусти мониторинг.", parse_mode="Markdown")

    # ─── Главное меню ─────────────────────────────────────────────────────────

    async def cb_start_monitor(self, cq: CallbackQuery):
        if self.is_running:
            await cq.answer("Уже запущен!", show_alert=False)
            return
        await cq.message.edit_text(
            f"🚀 Запускаю мониторинг...\n\n"
            f"Пара: `{self.symbol}`\n"
            f"15m сигналы + 1h тренд-фильтр\n"
            f"Мин. паттернов: {config.MIN_PATTERNS_TO_SIGNAL}\n"
            f"Мин. confluence: {config.MIN_CONFIDENCE}%",
            parse_mode="Markdown",
        )
        await cq.answer()
        await self._start_monitor(cq.message.chat.id)

    async def cb_stop_monitor(self, cq: CallbackQuery):
        await self._stop_monitor()
        await cq.message.edit_text("⏹ Мониторинг остановлен.", reply_markup=main_menu_kb())
        await cq.answer("Остановлено")

    async def cb_status(self, cq: CallbackQuery):
        await cq.message.edit_text(
            self._status_text(), reply_markup=main_menu_kb(), parse_mode="Markdown"
        )
        await cq.answer()

    async def cb_back_main(self, cq: CallbackQuery, state: FSMContext):
        await state.clear()
        await cq.message.edit_text(
            f"Пара: `{self.symbol}` · {'🟢 Работает' if self.is_running else '🔴 Остановлен'}",
            reply_markup=main_menu_kb(), parse_mode="Markdown",
        )
        await cq.answer()

    # ─── Выбор пары ───────────────────────────────────────────────────────────

    async def cb_change_pair(self, cq: CallbackQuery):
        await cq.message.edit_text(
            "📊 Выбери пару для мониторинга:",
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

        self.symbol = data
        was_running = self.is_running
        if was_running:
            await self._stop_monitor()

        await cq.message.edit_text(
            f"✅ Пара: `{self.symbol}`"
            f"{chr(10)}Перезапускаю мониторинг..." if was_running else f"✅ Пара: `{self.symbol}`",
            reply_markup=main_menu_kb() if not was_running else None,
            parse_mode="Markdown",
        )
        await cq.answer(f"Выбрано: {self.symbol}")

        if was_running:
            await self._start_monitor(cq.message.chat.id)

    async def fsm_custom_pair(self, msg: Message, state: FSMContext):
        pair = msg.text.strip().upper()
        if "_" not in pair:
            pair = pair.replace("USDT", "_USDT")
        self.symbol = pair
        await state.clear()
        await msg.answer(
            f"✅ Пара: `{self.symbol}`\nИспользуй ▶️ Старт для запуска.",
            reply_markup=main_menu_kb(), parse_mode="Markdown",
        )

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

    async def _start_monitor(self, chat_id: int):
        self.chat_id = chat_id
        self._engine = MultiTFEngine(
            symbol=self.symbol,
            on_signal=self._on_signal,
        )
        self.is_running = True
        self._engine_task = asyncio.create_task(self._engine.start())

        await self.bot.send_message(
            chat_id,
            f"✅ *Мониторинг запущен*\n\n"
            f"Пара: `{self.symbol}` · MEXC Futures\n"
            f"Таймфреймы: `15m` + `1h`\n"
            f"Мин. паттернов: `{config.MIN_PATTERNS_TO_SIGNAL}`\n"
            f"Мин. confluence: `{config.MIN_CONFIDENCE}%`\n"
            f"Таймаут сигнала: `{config.CONFIRM_TIMEOUT_SEC // 60} мин`\n\n"
            f"⏳ Загружаю историю свечей...",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown",
        )
        logger.info(f"[Bot] Мониторинг запущен: {self.symbol}")

    async def _stop_monitor(self):
        if self._pending:
            self._pending.cancel_timer()
            self._pending = None
        if self._engine:
            await self._engine.stop()
        if self._engine_task and not self._engine_task.done():
            self._engine_task.cancel()
            try:
                await self._engine_task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        self._engine = None
        self._engine_task = None
        logger.info("[Bot] Мониторинг остановлен")

    def _status_text(self) -> str:
        buf_15m = len(self._engine.buf_15m) if self._engine else 0
        buf_1h  = len(self._engine.buf_1h)  if self._engine else 0
        return format_status(self.symbol, self.is_running, buf_15m, buf_1h)

    # ─── Run ──────────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("[Bot] Запуск polling...")
        await self.dp.start_polling(
            self.bot,
            allowed_updates=["message", "callback_query"],
        )

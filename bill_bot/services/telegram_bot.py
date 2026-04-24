from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bill_bot.core.config import Config
from bill_bot.services.subscriptions import SubscriptionStore


logger = logging.getLogger(__name__)


def _main_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="📌 Пары", callback_data="menu:pairs")
    kb.button(text="⚙️ Риск", callback_data="menu:risk")
    kb.button(text="▶️ Запуск", callback_data="menu:start")
    kb.button(text="⏸ Стоп", callback_data="menu:stop")
    kb.button(text="📊 Статус", callback_data="menu:status")
    kb.adjust(2)
    return kb


def _pairs_menu(cfg: Config, selected: set[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for p in cfg.pairs_available:
        mark = "✅" if p in selected else "⬜"
        kb.button(text=f"{mark} {p}", callback_data=f"pair:{p}")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(3)
    return kb


def _risk_menu(current: float | None) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for v in (0.5, 1.0, 2.0, 3.0, 5.0):
        mark = "✅" if current is not None and abs(current - v) < 1e-9 else "⬜"
        kb.button(text=f"{mark} {v}%", callback_data=f"risk:{v}")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(3)
    return kb


async def run_telegram(cfg: Config, subs: SubscriptionStore):
    if not cfg.telegram_token:
        logger.warning("TELEGRAM_TOKEN is not set, telegram bot disabled")
        return

    bot = Bot(token=cfg.telegram_token)
    dp = Dispatcher()

    @dp.message(F.text == "/start")
    async def start_handler(message: Message):
        st = await subs.dump_user_state(message.from_user.id)
        text = (
            "Bill Bot (Hyperliquid) запущен.\n\n"
            f"TF: {cfg.timeframe}\n"
            f"Активен: {st['active']}\n"
            f"Пары: {', '.join(st['pairs']) if st['pairs'] else '-'}\n"
            f"Risk%: {st['cfg'].get('risk_pct', cfg.default_risk_pct)}\n\n"
            "Выберите действие:"
        )
        await message.answer(text, reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:back")
    async def back(cb: CallbackQuery):
        st = await subs.dump_user_state(cb.from_user.id)
        text = (
            "Меню.\n\n"
            f"TF: {cfg.timeframe}\n"
            f"Активен: {st['active']}\n"
            f"Пары: {', '.join(st['pairs']) if st['pairs'] else '-'}\n"
            f"Risk%: {st['cfg'].get('risk_pct', cfg.default_risk_pct)}"
        )
        await cb.message.edit_text(text, reply_markup=_main_menu().as_markup())
        await cb.answer()

    @dp.callback_query(F.data == "menu:pairs")
    async def menu_pairs(cb: CallbackQuery):
        pairs = set(await subs.get_user_pairs(cb.from_user.id))
        await cb.message.edit_text("Выберите пары для отслеживания:", reply_markup=_pairs_menu(cfg, pairs).as_markup())
        await cb.answer()

    @dp.callback_query(F.data.startswith("pair:"))
    async def toggle_pair(cb: CallbackQuery):
        pair = cb.data.split(":", 1)[1].upper()
        await subs.toggle_pair(cb.from_user.id, pair)
        pairs = set(await subs.get_user_pairs(cb.from_user.id))
        await cb.message.edit_reply_markup(reply_markup=_pairs_menu(cfg, pairs).as_markup())
        await cb.answer()

    @dp.callback_query(F.data == "menu:risk")
    async def menu_risk(cb: CallbackQuery):
        st = await subs.get_user_cfg(cb.from_user.id)
        cur = st.get("risk_pct")
        await cb.message.edit_text("Выберите риск на сделку (% от виртуального депозита):", reply_markup=_risk_menu(cur).as_markup())
        await cb.answer()

    @dp.callback_query(F.data.startswith("risk:"))
    async def set_risk(cb: CallbackQuery):
        raw = cb.data.split(":", 1)[1]
        try:
            v = float(raw)
        except Exception:
            await cb.answer("Некорректное значение")
            return
        await subs.set_user_risk(cb.from_user.id, v)
        await cb.answer(f"Risk установлен: {v}%")
        st = await subs.get_user_cfg(cb.from_user.id)
        await cb.message.edit_reply_markup(reply_markup=_risk_menu(st.get("risk_pct")).as_markup())

    @dp.callback_query(F.data == "menu:start")
    async def start_tracking(cb: CallbackQuery):
        await subs.set_active(cb.from_user.id, True)
        await cb.answer("Отслеживание включено")
        st = await subs.dump_user_state(cb.from_user.id)
        text = f"Отслеживание включено.\nПары: {', '.join(st['pairs']) if st['pairs'] else '-'}"
        await cb.message.edit_text(text, reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:stop")
    async def stop_tracking(cb: CallbackQuery):
        await subs.set_active(cb.from_user.id, False)
        await cb.answer("Отслеживание выключено")
        await cb.message.edit_text("Отслеживание выключено.", reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:status")
    async def status(cb: CallbackQuery):
        st = await subs.dump_user_state(cb.from_user.id)
        await cb.answer()
        text = (
            "Статус.\n\n"
            f"TF: {cfg.timeframe}\n"
            f"Активен: {st['active']}\n"
            f"Пары: {', '.join(st['pairs']) if st['pairs'] else '-'}\n"
            f"Risk%: {st['cfg'].get('risk_pct', cfg.default_risk_pct)}"
        )
        await cb.message.edit_text(text, reply_markup=_main_menu().as_markup())

    logger.info("Telegram bot polling started")
    await dp.start_polling(bot)

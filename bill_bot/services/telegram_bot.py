from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.input_file import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bill_bot.core.config import Config
from bill_bot.services.candle_store import RedisCandleStore
from bill_bot.services.charting import FractalPoint, build_chart_png
from bill_bot.services.execution import RedisTradeState, Position, PendingOrder
from bill_bot.services.fractals import RedisFractalStore
from bill_bot.services.indicators import alligator_ema
from bill_bot.services.subscriptions import SubscriptionStore


logger = logging.getLogger(__name__)


def _main_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="📌 Пары", callback_data="menu:pairs")
    kb.button(text="⏱ TF", callback_data="menu:tf")
    kb.button(text="⚙️ Риск", callback_data="menu:risk")
    kb.button(text="▶️ Запуск", callback_data="menu:start")
    kb.button(text="⏸ Стоп", callback_data="menu:stop")
    kb.button(text="📈 Позиции", callback_data="menu:positions")
    kb.button(text="💰 P&L", callback_data="menu:pnl")
    kb.button(text="📉 График", callback_data="menu:chart")
    kb.button(text="📊 Статус", callback_data="menu:status")
    kb.adjust(2)
    return kb


def _reply_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Пары"), KeyboardButton(text="⏱ TF")],
            [KeyboardButton(text="⚙️ Риск"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="▶️ Запуск"), KeyboardButton(text="⏸ Стоп")],
            [KeyboardButton(text="📈 Позиции"), KeyboardButton(text="💰 P&L")],
            [KeyboardButton(text="📉 График")],
        ],
        resize_keyboard=True,
    )


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


def _tf_menu(timeframes: list[str], current: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for tf in timeframes:
        mark = "✅" if tf == current else "⬜"
        kb.button(text=f"{mark} {tf}", callback_data=f"tf:{tf}")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(3)
    return kb


def _chart_pairs_menu(pairs: list[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for p in pairs:
        kb.button(text=p, callback_data=f"chart:{p}")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(3)
    return kb


async def run_telegram(
    cfg: Config,
    subs: SubscriptionStore,
    trade_state: RedisTradeState,
    candle_store: RedisCandleStore,
    fractals_store: RedisFractalStore,
):
    if not cfg.telegram_token:
        logger.warning("TELEGRAM_TOKEN is not set, telegram bot disabled")
        return

    bot = Bot(token=cfg.telegram_token)
    dp = Dispatcher()

    async def user_ctx(user_id: int) -> tuple[str, SubscriptionStore]:
        tf = await subs.get_user_timeframe(user_id, cfg.timeframe)
        if tf not in cfg.timeframes_available:
            tf = cfg.timeframe
        return tf, SubscriptionStore(subs.r, tf=tf)

    async def send_menu(message: Message) -> None:
        uid = message.from_user.id
        tf, user_subs = await user_ctx(uid)
        st = await user_subs.dump_user_state(uid)
        text = (
            "Меню.\n\n"
            f"TF: {tf}\n"
            f"Активен: {st['active']}\n"
            f"Пары: {', '.join(st['pairs']) if st['pairs'] else '-'}\n"
            f"Risk%: {st['cfg'].get('risk_pct', cfg.default_risk_pct)}"
        )
        await message.answer(text, reply_markup=_reply_main_menu())

    @dp.message(F.text == "/start")
    async def start_handler(message: Message):
        text = (
            "Bill Bot (Hyperliquid) запущен.\n\n"
            "Выберите действие кнопками снизу:"
        )
        await message.answer(text, reply_markup=_reply_main_menu())
        await send_menu(message)

    @dp.callback_query(F.data == "menu:back")
    async def back(cb: CallbackQuery):
        await cb.answer()
        await send_menu(cb.message)

    @dp.message(F.text == "📊 Статус")
    async def menu_status_msg(message: Message):
        await send_menu(message)
        return

    @dp.message(F.text == "📌 Пары")
    async def menu_pairs_msg(message: Message):
        uid = message.from_user.id
        _, user_subs = await user_ctx(uid)
        pairs = set(await user_subs.get_user_pairs(uid))
        await message.answer("Выберите пары для отслеживания:", reply_markup=_pairs_menu(cfg, pairs).as_markup())
        return

    @dp.message(F.text == "⚙️ Риск")
    async def menu_risk_msg(message: Message):
        uid = message.from_user.id
        _, user_subs = await user_ctx(uid)
        st = await user_subs.get_user_cfg(uid)
        cur = st.get("risk_pct")
        await message.answer("Выберите риск на сделку (% от виртуального депозита):", reply_markup=_risk_menu(cur).as_markup())
        return

    @dp.message(F.text == "⏱ TF")
    async def menu_tf_msg(message: Message):
        tf, _ = await user_ctx(message.from_user.id)
        await message.answer("Выберите таймфрейм:", reply_markup=_tf_menu(cfg.timeframes_available, tf).as_markup())
        return

    @dp.message(F.text == "▶️ Запуск")
    async def start_tracking_msg(message: Message):
        uid = message.from_user.id
        _, user_subs = await user_ctx(uid)
        await user_subs.set_active(uid, True)
        await message.answer("Отслеживание включено.")
        await send_menu(message)
        return

    @dp.message(F.text == "⏸ Стоп")
    async def stop_tracking_msg(message: Message):
        uid = message.from_user.id
        _, user_subs = await user_ctx(uid)
        await user_subs.set_active(uid, False)
        await message.answer("Отслеживание выключено.")
        await send_menu(message)
        return

    @dp.message(F.text == "📈 Позиции")
    async def positions_msg(message: Message):
        uid = message.from_user.id
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            await message.answer("Нет выбранных пар.")
            await send_menu(message)
            return
        lines: list[str] = ["Позиции / ордера:\n"]
        for p in pairs:
            st = await trade_state.get(uid, p, tf)
            if st.get("pos"):
                pos = Position.from_dict(st["pos"])
                pnl = await trade_state.get_pnl(uid, p, tf)
                lines.append(
                    f"{p}: POS {pos.side} entry={pos.entry:.4f} sl={pos.stop_loss:.4f} tp={pos.take_profit:.4f} qty={pos.qty:.6f} uPnL={float(pnl.get('unrealized', 0.0)):.2f}"
                )
            elif st.get("ord"):
                o = PendingOrder.from_dict(st["ord"])
                lines.append(f"{p}: ORD {o.side} trigger={o.trigger:.4f} sl={o.stop_loss:.4f} tp={o.take_profit:.4f} qty={o.qty:.6f}")
            else:
                lines.append(f"{p}: —")
        await message.answer("\n".join(lines))
        return

    @dp.message(F.text == "💰 P&L")
    async def pnl_msg(message: Message):
        uid = message.from_user.id
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        total_u = 0.0
        total_r = 0.0
        for p in pairs:
            pnl = await trade_state.get_pnl(uid, p, tf)
            try:
                total_u += float(pnl.get("unrealized", 0.0))
            except Exception:
                pass
            try:
                total_r += float(pnl.get("realized", 0.0))
            except Exception:
                pass
        await message.answer(f"P&L по TF {tf}\n\nUnrealized: {total_u:.2f}\nRealized: {total_r:.2f}")
        return

    @dp.message(F.text == "📉 График")
    async def chart_menu_msg(message: Message):
        uid = message.from_user.id
        _, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            await message.answer("Нет выбранных пар.")
            await send_menu(message)
            return
        await message.answer("Выбери пару для графика:", reply_markup=_chart_pairs_menu(pairs).as_markup())
        return
        await cb.answer()

    @dp.callback_query(F.data == "menu:tf")
    async def menu_tf(cb: CallbackQuery):
        tf, _ = await user_ctx(cb.from_user.id)
        await cb.message.edit_text("Выберите таймфрейм:", reply_markup=_tf_menu(cfg.timeframes_available, tf).as_markup())
        await cb.answer()

    @dp.callback_query(F.data.startswith("tf:"))
    async def set_tf(cb: CallbackQuery):
        uid = cb.from_user.id
        tf = cb.data.split(":", 1)[1]
        if tf not in cfg.timeframes_available:
            await cb.answer("Неподдерживаемый TF")
            return
        old_tf = await subs.get_user_timeframe(uid, cfg.timeframe)
        if old_tf != tf:
            await subs.set_user_timeframe(uid, tf)
            old_subs = SubscriptionStore(subs.r, tf=old_tf)
            new_subs = SubscriptionStore(subs.r, tf=tf)

            old_pairs = await old_subs.get_user_pairs(uid)
            new_pairs = await new_subs.get_user_pairs(uid)
            if old_pairs and not new_pairs:
                for p in old_pairs:
                    await new_subs.toggle_pair(uid, p)

            old_cfg = await old_subs.get_user_cfg(uid)
            new_cfg = await new_subs.get_user_cfg(uid)
            if "risk_pct" in old_cfg and "risk_pct" not in new_cfg:
                try:
                    await new_subs.set_user_risk(uid, float(old_cfg["risk_pct"]))
                except Exception:
                    pass

            await old_subs.set_active(uid, False)
            await new_subs.set_active(uid, False)

        await cb.answer(f"TF установлен: {tf}. Трекинг выключен.")
        await cb.message.edit_reply_markup(reply_markup=_tf_menu(cfg.timeframes_available, tf).as_markup())

    @dp.callback_query(F.data == "menu:pairs")
    async def menu_pairs(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        pairs = set(await user_subs.get_user_pairs(uid))
        await cb.message.edit_text("Выберите пары для отслеживания:", reply_markup=_pairs_menu(cfg, pairs).as_markup())
        await cb.answer()

    @dp.callback_query(F.data.startswith("pair:"))
    async def toggle_pair(cb: CallbackQuery):
        uid = cb.from_user.id
        pair = cb.data.split(":", 1)[1].upper()
        _, user_subs = await user_ctx(uid)
        await user_subs.toggle_pair(uid, pair)
        pairs = set(await user_subs.get_user_pairs(uid))
        await cb.message.edit_reply_markup(reply_markup=_pairs_menu(cfg, pairs).as_markup())
        await cb.answer()

    @dp.callback_query(F.data == "menu:risk")
    async def menu_risk(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        st = await user_subs.get_user_cfg(uid)
        cur = st.get("risk_pct")
        await cb.message.edit_text("Выберите риск на сделку (% от виртуального депозита):", reply_markup=_risk_menu(cur).as_markup())
        await cb.answer()

    @dp.callback_query(F.data.startswith("risk:"))
    async def set_risk(cb: CallbackQuery):
        uid = cb.from_user.id
        raw = cb.data.split(":", 1)[1]
        try:
            v = float(raw)
        except Exception:
            await cb.answer("Некорректное значение")
            return
        _, user_subs = await user_ctx(uid)
        await user_subs.set_user_risk(uid, v)
        await cb.answer(f"Risk установлен: {v}%")
        st = await user_subs.get_user_cfg(uid)
        await cb.message.edit_reply_markup(reply_markup=_risk_menu(st.get("risk_pct")).as_markup())

    @dp.callback_query(F.data == "menu:start")
    async def start_tracking(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        await user_subs.set_active(uid, True)
        await cb.answer("Отслеживание включено")
        st = await user_subs.dump_user_state(uid)
        text = f"Отслеживание включено.\nПары: {', '.join(st['pairs']) if st['pairs'] else '-'}"
        await cb.message.edit_text(text, reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:stop")
    async def stop_tracking(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        await user_subs.set_active(uid, False)
        await cb.answer("Отслеживание выключено")
        await cb.message.edit_text("Отслеживание выключено.", reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:status")
    async def status(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, user_subs = await user_ctx(uid)
        st = await user_subs.dump_user_state(uid)
        await cb.answer()
        text = (
            "Статус.\n\n"
            f"TF: {tf}\n"
            f"Активен: {st['active']}\n"
            f"Пары: {', '.join(st['pairs']) if st['pairs'] else '-'}\n"
            f"Risk%: {st['cfg'].get('risk_pct', cfg.default_risk_pct)}"
        )
        await cb.message.edit_text(text, reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:positions")
    async def positions(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            await cb.answer()
            await cb.message.edit_text("Нет выбранных пар.", reply_markup=_main_menu().as_markup())
            return
        lines: list[str] = ["Позиции / ордера:\n"]
        for p in pairs:
            st = await trade_state.get(uid, p, tf)
            if st.get("pos"):
                pos = Position.from_dict(st["pos"])
                pnl = await trade_state.get_pnl(uid, p, tf)
                lines.append(
                    f"{p}: POS {pos.side} entry={pos.entry:.4f} sl={pos.stop_loss:.4f} tp={pos.take_profit:.4f} qty={pos.qty:.6f} uPnL={float(pnl.get('unrealized', 0.0)):.2f}"
                )
            elif st.get("ord"):
                o = PendingOrder.from_dict(st["ord"])
                lines.append(f"{p}: ORD {o.side} trigger={o.trigger:.4f} sl={o.stop_loss:.4f} tp={o.take_profit:.4f} qty={o.qty:.6f}")
            else:
                lines.append(f"{p}: —")
        await cb.answer()
        await cb.message.edit_text("\n".join(lines), reply_markup=_main_menu().as_markup())

    @dp.callback_query(F.data == "menu:pnl")
    async def pnl(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        total_u = 0.0
        total_r = 0.0
        for p in pairs:
            pnl = await trade_state.get_pnl(uid, p, tf)
            try:
                total_u += float(pnl.get("unrealized", 0.0))
            except Exception:
                pass
            try:
                total_r += float(pnl.get("realized", 0.0))
            except Exception:
                pass
        await cb.answer()
        await cb.message.edit_text(
            f"P&L по TF {tf}\n\nUnrealized: {total_u:.2f}\nRealized: {total_r:.2f}",
            reply_markup=_main_menu().as_markup(),
        )

    @dp.callback_query(F.data == "menu:chart")
    async def chart_menu(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            await cb.answer()
            await cb.message.edit_text("Нет выбранных пар.", reply_markup=_main_menu().as_markup())
            return
        await cb.answer()
        await cb.message.edit_text("Выбери пару для графика:", reply_markup=_chart_pairs_menu(pairs).as_markup())

    @dp.callback_query(F.data.startswith("chart:"))
    async def chart_pair(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, _ = await user_ctx(uid)
        pair = cb.data.split(":", 1)[1].upper()
        candles = await candle_store.get_window(pair, tf)
        if not candles:
            await cb.answer("Нет свечей")
            return
        closes = [c.c for c in candles]
        jaw = alligator_ema(closes, 13)
        teeth = alligator_ema(closes, 8)
        lips = alligator_ema(closes, 5)
        fr = await fractals_store.get_all(pair, tf)
        fpts = [FractalPoint(kind=f.kind, t=f.t, price=f.price) for f in fr]
        data = build_chart_png(pair, tf, candles, jaw, teeth, lips, fpts)
        await cb.answer()
        await cb.message.answer_photo(BufferedInputFile(data, filename=f"{pair}_{tf}.png"))

    logger.info("Telegram bot polling started")
    await dp.start_polling(bot)

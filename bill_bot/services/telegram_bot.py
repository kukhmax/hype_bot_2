from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
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
    kb.button(text="⚙️ Риск", callback_data="menu:risk")
    kb.button(text="▶️ Запуск", callback_data="menu:start")
    kb.button(text="⏸ Стоп", callback_data="menu:stop")
    kb.button(text="📈 Позиции", callback_data="menu:positions")
    kb.button(text="💰 P&L", callback_data="menu:pnl")
    kb.button(text="📉 График", callback_data="menu:chart")
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

    @dp.callback_query(F.data == "menu:positions")
    async def positions(cb: CallbackQuery):
        uid = cb.from_user.id
        pairs = await subs.get_user_pairs(uid)
        if not pairs:
            await cb.answer()
            await cb.message.edit_text("Нет выбранных пар.", reply_markup=_main_menu().as_markup())
            return
        lines: list[str] = ["Позиции / ордера:\n"]
        for p in pairs:
            st = await trade_state.get(uid, p, cfg.timeframe)
            if st.get("pos"):
                pos = Position.from_dict(st["pos"])
                pnl = await trade_state.get_pnl(uid, p, cfg.timeframe)
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
        pairs = await subs.get_user_pairs(uid)
        total_u = 0.0
        total_r = 0.0
        for p in pairs:
            pnl = await trade_state.get_pnl(uid, p, cfg.timeframe)
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
            f"P&L по TF {cfg.timeframe}\n\nUnrealized: {total_u:.2f}\nRealized: {total_r:.2f}",
            reply_markup=_main_menu().as_markup(),
        )

    @dp.callback_query(F.data == "menu:chart")
    async def chart_menu(cb: CallbackQuery):
        uid = cb.from_user.id
        pairs = await subs.get_user_pairs(uid)
        if not pairs:
            await cb.answer()
            await cb.message.edit_text("Нет выбранных пар.", reply_markup=_main_menu().as_markup())
            return
        await cb.answer()
        await cb.message.edit_text("Выбери пару для графика:", reply_markup=_chart_pairs_menu(pairs).as_markup())

    @dp.callback_query(F.data.startswith("chart:"))
    async def chart_pair(cb: CallbackQuery):
        pair = cb.data.split(":", 1)[1].upper()
        candles = await candle_store.get_window(pair, cfg.timeframe)
        if not candles:
            await cb.answer("Нет свечей")
            return
        closes = [c.c for c in candles]
        jaw = alligator_ema(closes, 13)
        teeth = alligator_ema(closes, 8)
        lips = alligator_ema(closes, 5)
        fr = await fractals_store.get_all(pair, cfg.timeframe)
        fpts = [FractalPoint(kind=f.kind, t=f.t, price=f.price) for f in fr]
        data = build_chart_png(pair, cfg.timeframe, candles, jaw, teeth, lips, fpts)
        await cb.answer()
        await cb.message.answer_photo(BufferedInputFile(data, filename=f"{pair}_{cfg.timeframe}.png"))

    logger.info("Telegram bot polling started")
    await dp.start_polling(bot)

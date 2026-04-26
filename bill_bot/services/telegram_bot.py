from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import logging
import re
import time

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.input_file import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from bill_bot.core.config import Config
from bill_bot.services.candle_store import Candle, RedisCandleStore
from bill_bot.services.charting import FractalPoint, PriceLevel, build_chart_png
from bill_bot.services.execution import RedisTradeState, Position, PendingOrder
from bill_bot.services.fractals import RedisFractalStore, detect_confirmed_fractal
from bill_bot.services.hyperliquid_api import HyperliquidInfoClient
from bill_bot.services.indicators import alligator_ema
from bill_bot.services.subscriptions import SubscriptionStore


logger = logging.getLogger(__name__)


class AddPairFlow(StatesGroup):
    waiting_pair = State()


class ChartPairFlow(StatesGroup):
    waiting_pair = State()


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


def _reply_main_menu(active: bool) -> ReplyKeyboardMarkup:
    start_stop = "⏸ Стоп" if active else "▶️ Запуск"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Пары"), KeyboardButton(text="⏱ TF")],
            [KeyboardButton(text="⚙️ Риск"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text=start_stop)],
            [KeyboardButton(text="📈 Позиции"), KeyboardButton(text="💰 P&L")],
            [KeyboardButton(text="📉 График"), KeyboardButton(text="📜 Сделки")],
            [KeyboardButton(text="🗑 Удалить")],
        ],
        resize_keyboard=True,
    )


def _display_pair(coin: str) -> str:
    coin = str(coin).upper().strip()
    return f"{coin}-USDC"


def _parse_pair_input(raw: str) -> str | None:
    s = str(raw or "").strip().upper()
    s = s.replace(" ", "")
    if s.endswith("/USDC"):
        s = s[:-5] + "-USDC"
    if s.endswith("-PERP"):
        s = s[:-5]
    if s.endswith("-USDC"):
        s = s[:-5]
    if not re.fullmatch(r"[A-Z0-9]{2,20}", s):
        return None
    return s


def _fmt_ts_ms(ts_ms: int) -> str:
    try:
        ts_ms = int(ts_ms)
    except Exception:
        return str(ts_ms)
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(ZoneInfo("Europe/Warsaw"))
    except Exception:
        return str(ts_ms)
    return dt.strftime("%H:%M %d/%m/%y")


def _timeframe_ms(tf: str) -> int:
    tf = str(tf).strip()
    if tf.endswith("m"):
        minutes = int(tf[:-1])
        if minutes <= 0:
            raise ValueError(f"Unsupported timeframe: {tf}")
        return minutes * 60_000
    if tf.endswith("h"):
        hours = int(tf[:-1])
        if hours <= 0:
            raise ValueError(f"Unsupported timeframe: {tf}")
        return hours * 60 * 60_000
    raise ValueError(f"Unsupported timeframe: {tf}")


def _calc_fractal_points(candles, teeth_series) -> list[FractalPoint]:
    out: list[FractalPoint] = []
    for i in range(2, max(2, len(candles) - 2)):
        c = candles[i]
        l2 = candles[i - 2]
        l1 = candles[i - 1]
        r1 = candles[i + 1]
        r2 = candles[i + 2]

        if c.h > l2.h and c.h > l1.h and c.h > r1.h and c.h > r2.h:
            out.append(FractalPoint(kind="HIGH", t=c.t, price=c.h, idx=i))
        if c.l < l2.l and c.l < l1.l and c.l < r1.l and c.l < r2.l:
            out.append(FractalPoint(kind="LOW", t=c.t, price=c.l, idx=i))
    out.sort(key=lambda x: x.t)
    return out


def _pairs_menu(cfg: Config, selected: set[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    base = [p for p in cfg.pairs_available]
    extras = sorted([p for p in selected if p not in base])
    for p in [*base, *extras]:
        mark = "✅" if p in selected else "⬜"
        kb.button(text=f"{mark} {_display_pair(p)}", callback_data=f"pair:{p}")
    kb.button(text="➕ Вручную", callback_data="pair_add:prompt")
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
        kb.button(text=_display_pair(p), callback_data=f"chart:{p}")
    kb.button(text="⌨️ Ввести пару", callback_data="chart_input:prompt")
    kb.button(text="⬅️ Назад", callback_data="menu:back")
    kb.adjust(3)
    return kb


def _msg_controls_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить", callback_data="msg:delete")
    kb.adjust(1)
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
    dp = Dispatcher(storage=MemoryStorage())
    hl = HyperliquidInfoClient()
    last_msg_key = lambda uid: f"tg:last_bot_msg:{int(uid)}"

    def _extract_orders(st: dict) -> list[PendingOrder]:
        if isinstance(st.get("ords"), list):
            out: list[PendingOrder] = []
            for x in st["ords"]:
                if isinstance(x, dict):
                    try:
                        out.append(PendingOrder.from_dict(x))
                    except Exception:
                        pass
            return out
        if isinstance(st.get("ord"), dict):
            try:
                return [PendingOrder.from_dict(st["ord"])]
            except Exception:
                return []
        return []

    def _pnl_realized(side: str, entry: float, exit_price: float, qty: float) -> float:
        if str(side).upper() == "LONG":
            return (float(exit_price) - float(entry)) * float(qty)
        return (float(entry) - float(exit_price)) * float(qty)

    async def _remember_last(uid: int, msg_id: int) -> None:
        try:
            await subs.r.set(last_msg_key(uid), int(msg_id))
        except Exception:
            pass

    async def _get_last(uid: int) -> int | None:
        try:
            raw = await subs.r.get(last_msg_key(uid))
        except Exception:
            return None
        if not raw:
            return None
        try:
            return int(raw)
        except Exception:
            return None

    async def _delete_last(uid: int) -> bool:
        mid = await _get_last(uid)
        if not mid:
            return False
        try:
            await bot.delete_message(chat_id=int(uid), message_id=int(mid))
            return True
        except Exception:
            return False

    async def _build_positions_view(uid: int) -> tuple[str, InlineKeyboardBuilder]:
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            kb = InlineKeyboardBuilder()
            kb.button(text="⬅️ Назад", callback_data="menu:back")
            kb.adjust(1)
            return "Нет выбранных пар.", kb

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
        balance = float(cfg.virtual_equity) + float(total_r) + float(total_u)

        lines: list[str] = [
            f"📈 Позиции / ордера (TF {tf})",
            f"💼 Баланс: {balance:.2f} USDC",
            f"💰 Realized: {total_r:.2f} USDC",
            f"📈 Unrealized: {total_u:.2f} USDC",
            "",
        ]

        kb = InlineKeyboardBuilder()
        for p in pairs:
            st = await trade_state.get(uid, p, tf)
            pair_txt = _display_pair(p)
            if st.get("pos"):
                pos = Position.from_dict(st["pos"])
                pnl = await trade_state.get_pnl(uid, p, tf)
                upnl = float(pnl.get("unrealized", 0.0))
                side_u = pos.side.upper()
                side_emoji = "🟢" if side_u == "LONG" else "🔴"
                upnl_emoji = "🟩" if upnl >= 0 else "🟥"
                lines.append(
                    f"{side_emoji} [POS] {pair_txt} — {side_u}\n"
                    f"🎯 Entry {pos.entry:.4f} | 🛑 SL {pos.stop_loss:.4f} | ✅ TP {pos.take_profit:.4f}\n"
                    f"📦 Qty {pos.qty:.6f} | {upnl_emoji} uPnL {upnl:+.2f}"
                )
                kb.button(text=f"🔻 Закрыть {pair_txt}", callback_data=f"pos_close:{p}")
            else:
                orders = _extract_orders(st)
                if orders:
                    for o in orders:
                        side_u = o.side.upper()
                        side_emoji = "🟢" if side_u == "LONG" else "🔴"
                        lines.append(
                            f"🚩[ORD] {pair_txt} — {side_emoji} {side_u}\n"
                            f"🎯 Trigger {o.trigger:.4f} | 🛑 SL {o.stop_loss:.4f} | ✅ TP {o.take_profit:.4f}\n"
                            f"📦 Qty {o.qty:.6f}"
                        )
                        kb.button(text=f"❌ Отменить {side_u} {pair_txt}", callback_data=f"ord_cancel:{p}:{side_u}")
                else:
                    lines.append(f"⚪ {pair_txt} — нет позиции/ордера")
            lines.append("")

        kb.button(text="🔄 Обновить", callback_data="positions:refresh")
        kb.button(text="🗑 Удалить", callback_data="msg:delete")
        kb.button(text="⬅️ Назад", callback_data="menu:back")
        kb.adjust(2)
        return "\n".join(lines), kb

    async def user_ctx(user_id: int) -> tuple[str, SubscriptionStore]:
        tf = await subs.get_user_timeframe(user_id, cfg.timeframe)
        if tf not in cfg.timeframes_available:
            tf = cfg.timeframe
        return tf, SubscriptionStore(subs.r, tf=tf)

    async def send_menu(message: Message, user_id: int | None = None) -> None:
        uid = int(user_id) if user_id is not None else int(message.from_user.id)
        tf, user_subs = await user_ctx(uid)
        st = await user_subs.dump_user_state(uid)
        active = bool(st.get("active", False))
        pairs = list(st.get("pairs") or [])
        pairs_text = ", ".join(_display_pair(p) for p in pairs) if pairs else "—"
        risk = float(st.get("cfg", {}).get("risk_pct", cfg.default_risk_pct))

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
        balance = float(cfg.virtual_equity) + float(total_r) + float(total_u)

        active_txt = "✅ ВКЛ" if active else "⏸ ВЫКЛ"
        text = (
            "🏠 Меню\n\n"
            f"⏱ TF: {tf}\n"
            f"▶️ Отслеживание: {active_txt}\n"
            f"📌 Пары: {pairs_text}\n"
            f"⚙️ Риск: {risk:.2f}%\n\n"
            f"💼 Баланс: {balance:.2f} USDC\n"
            f"💰 Realized: {total_r:.2f} USDC\n"
            f"📈 Unrealized: {total_u:.2f} USDC"
        )
        sent = await message.answer(text, reply_markup=_reply_main_menu(active=active))
        await _remember_last(uid, sent.message_id)

    @dp.message(F.text == "/start")
    async def start_handler(message: Message, state: FSMContext):
        await state.clear()
        logger.info("tg:cmd user=%s text=%s", message.from_user.id, message.text)
        text = (
            "Bill Bot (Hyperliquid) запущен.\n\n"
            "Выберите действие кнопками снизу:"
        )
        await message.answer(text, reply_markup=_reply_main_menu(active=False))
        await send_menu(message)

    @dp.message(F.text == "🗑 Удалить")
    async def delete_last_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        ok = await _delete_last(uid)
        if not ok:
            tf, user_subs = await user_ctx(uid)
            st = await user_subs.dump_user_state(uid)
            await message.answer("Нечего удалять.", reply_markup=_reply_main_menu(active=bool(st.get("active", False))))
        return

    @dp.callback_query(F.data == "menu:back")
    async def back(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        logger.info("tg:cb user=%s data=%s", cb.from_user.id, cb.data)
        await cb.answer()
        await send_menu(cb.message, user_id=cb.from_user.id)

    @dp.message(F.text == "📊 Статус")
    async def menu_status_msg(message: Message, state: FSMContext):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", message.from_user.id, message.text)
        await send_menu(message)
        return

    @dp.message(F.text == "📌 Пары")
    async def menu_pairs_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
        _, user_subs = await user_ctx(uid)
        pairs = set(await user_subs.get_user_pairs(uid))
        await message.answer(
            "Выберите пары для отслеживания.\n\nМожно добавить вручную: отправьте символ (например HYPE-USDC).",
            reply_markup=_pairs_menu(cfg, pairs).as_markup(),
        )
        return

    @dp.message(F.text == "⚙️ Риск")
    async def menu_risk_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
        _, user_subs = await user_ctx(uid)
        st = await user_subs.get_user_cfg(uid)
        cur = st.get("risk_pct")
        await message.answer("Выберите риск на сделку (% от виртуального депозита):", reply_markup=_risk_menu(cur).as_markup())
        return

    @dp.message(F.text == "⏱ TF")
    async def menu_tf_msg(message: Message, state: FSMContext):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", message.from_user.id, message.text)
        tf, _ = await user_ctx(message.from_user.id)
        await message.answer("Выберите таймфрейм:", reply_markup=_tf_menu(cfg.timeframes_available, tf).as_markup())
        return

    @dp.message(F.text.in_(["▶️ Запуск", "⏸ Стоп"]))
    async def start_stop_tracking_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        _, user_subs = await user_ctx(uid)
        st = await user_subs.dump_user_state(uid)
        active = bool(st["active"])
        if active:
            logger.info("tg:btn user=%s action=stop", uid)
            await user_subs.set_active(uid, False)
            await message.answer("Отслеживание выключено.")
        else:
            logger.info("tg:btn user=%s action=start", uid)
            await user_subs.set_active(uid, True)
            await message.answer("Отслеживание включено.")
        await send_menu(message)
        return

    @dp.message(F.text == "📈 Позиции")
    async def positions_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
        text, kb = await _build_positions_view(uid)
        await message.answer(text, reply_markup=kb.as_markup())
        return

    @dp.message(F.text == "💰 P&L")
    async def pnl_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
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
        balance = float(cfg.virtual_equity) + float(total_r) + float(total_u)
        u_emoji = "🟩" if total_u >= 0 else "🟥"
        r_emoji = "🟩" if total_r >= 0 else "🟥"
        b_emoji = "💰" if balance >= float(cfg.virtual_equity) else "💸"
        text = (
            f"💰 P&L (TF {tf})\n\n"
            f"{b_emoji} Баланс: {balance:.2f} USDC\n"
            f"{r_emoji} Realized: {total_r:+.2f} USDC\n"
            f"{u_emoji} Unrealized: {total_u:+.2f} USDC\n"
            f"🏦 База: {float(cfg.virtual_equity):.2f} USDC"
        )
        sent = await message.answer(text, reply_markup=_msg_controls_menu().as_markup())
        await _remember_last(uid, sent.message_id)
        return

    @dp.message(F.text == "📜 Сделки")
    async def trades_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
        tf, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        if not pairs:
            await message.answer("Нет выбранных пар.")
            await send_menu(message)
            return

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
        balance = float(cfg.virtual_equity) + float(total_r) + float(total_u)

        lines: list[str] = [
            f"📜 Сделки (TF {tf})",
            f"💼 Баланс: {balance:.2f} USDC",
            f"💰 Realized: {total_r:.2f} USDC",
            f"📈 Unrealized: {total_u:.2f} USDC",
            "",
        ]
        any_rows = False
        for p in pairs:
            trades = await trade_state.get_trades(uid, p, tf, limit=3)
            if not trades:
                continue
            any_rows = True
            lines.append(f"🔹 {_display_pair(p)}")
            for t in trades:
                try:
                    side = str(t.get("side", ""))
                    pnl = float(t.get("pnl", 0.0))
                    entry = float(t.get("entry", 0.0))
                    exit_px = float(t.get("exit", 0.0))
                    qty = float(t.get("qty", 0.0))
                    reason = str(t.get("reason", ""))
                    opened_t = int(t.get("opened_t", 0))
                    closed_t = int(t.get("closed_t", 0))
                except Exception:
                    continue
                when = _fmt_ts_ms(closed_t)
                opened_when = _fmt_ts_ms(opened_t) if opened_t else ""
                side_u = side.upper()
                side_emoji = "🟢" if side_u == "LONG" else "🔴"
                pnl_emoji = "🟩" if pnl >= 0 else "🟥"
                reason_u = reason.upper()
                if reason_u == "TP":
                    reason_txt = "✅ TP"
                elif reason_u == "SL":
                    reason_txt = "🛑 SL"
                elif "SL_AND_TP" in reason_u:
                    reason_txt = "⚠️ SL/TP (в одной свече)"
                elif "MANUAL" in reason_u:
                    reason_txt = "✋ Закрыто вручную"
                else:
                    reason_txt = reason
                if opened_when:
                    lines.append(f"🕒 {opened_when} → {when}")
                lines.append(
                    f"{side_emoji} {side_u} | 🎯 {entry:.4f} → {exit_px:.4f} | 📦 {qty:.6f} | {pnl_emoji} PnL {pnl:+.2f} | {reason_txt}"
                )
            lines.append("")
        if not any_rows:
            await message.answer("Пока нет закрытых сделок.")
            return
        await message.answer("\n".join(lines), reply_markup=_msg_controls_menu().as_markup())
        return

    @dp.callback_query(F.data == "positions:refresh")
    async def positions_refresh(cb: CallbackQuery):
        uid = cb.from_user.id
        text, kb = await _build_positions_view(uid)
        await cb.answer()
        await cb.message.edit_text(text, reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("ord_cancel:"))
    async def cancel_order(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, _ = await user_ctx(uid)
        _, pair, side = cb.data.split(":", 2)
        pair = pair.upper()
        side = side.upper()
        st = await trade_state.get(uid, pair, tf)
        orders = _extract_orders(st)
        kept = [o for o in orders if o.side.upper() != side]
        if len(kept) == len(orders):
            await cb.answer("Нет ордера")
            return
        st.pop("ord", None)
        if kept:
            st["ords"] = [o.to_dict() for o in kept]
        else:
            st.pop("ords", None)
        await trade_state.set(uid, pair, tf, st)
        await cb.answer("Ордер отменён")
        text, kb = await _build_positions_view(uid)
        await cb.message.edit_text(text, reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("pos_close:"))
    async def close_position(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, _ = await user_ctx(uid)
        pair = cb.data.split(":", 1)[1].upper()
        st = await trade_state.get(uid, pair, tf)
        if not st.get("pos"):
            await cb.answer("Нет позиции")
            return
        candles = await candle_store.get_window(pair, tf)
        if not candles:
            await cb.answer("Нет свечей")
            return
        last = candles[-1]
        pos = Position.from_dict(st["pos"])
        exit_px = float(last.c)
        pnl = _pnl_realized(pos.side, pos.entry, exit_px, pos.qty)
        realized = float(st.get("realized", 0.0)) + float(pnl)
        st["realized"] = realized
        trade = {
            "user_id": uid,
            "pair": pair,
            "tf": tf,
            "side": pos.side,
            "entry": pos.entry,
            "exit": exit_px,
            "qty": pos.qty,
            "pnl": pnl,
            "opened_t": pos.opened_t,
            "closed_t": int(last.t),
            "reason": "MANUAL_CLOSE",
            "cluster_t": pos.cluster_t,
        }
        st["last_trade"] = trade
        st.pop("pos", None)
        st["last_t"] = int(last.t)
        await trade_state.append_trade(uid, pair, tf, trade)
        await trade_state.set(uid, pair, tf, st)
        await trade_state.set_pnl(uid, pair, tf, {"unrealized": 0.0, "realized": realized})
        await cb.answer("Позиция закрыта")
        text, kb = await _build_positions_view(uid)
        await cb.message.edit_text(text, reply_markup=kb.as_markup())

    @dp.message(F.text == "📉 График")
    async def chart_menu_msg(message: Message, state: FSMContext):
        await state.clear()
        uid = message.from_user.id
        try:
            await message.delete()
        except Exception:
            pass
        logger.info("tg:btn user=%s text=%s", uid, message.text)
        _, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        await message.answer("Выбери пару для графика (или введи вручную):", reply_markup=_chart_pairs_menu(pairs).as_markup())
        return

    @dp.callback_query(F.data == "chart_input:prompt")
    async def chart_input_prompt(cb: CallbackQuery, state: FSMContext):
        logger.info("tg:cb user=%s data=%s", cb.from_user.id, cb.data)
        await state.set_state(ChartPairFlow.waiting_pair)
        await cb.answer()
        await cb.message.answer("Введи пару в формате HYPE-USDC (можно просто HYPE).")

    @dp.message(ChartPairFlow.waiting_pair)
    async def chart_input_message(message: Message, state: FSMContext):
        uid = message.from_user.id
        tf, _ = await user_ctx(uid)
        raw = message.text or ""
        coin = _parse_pair_input(raw)
        logger.info("tg:input user=%s flow=chart raw=%s parsed=%s tf=%s", uid, raw, coin, tf)
        if not coin:
            await message.answer("Не понял формат. Пример: HYPE-USDC")
            return
        try:
            await message.delete()
        except Exception:
            pass
        await state.clear()

        candles = await candle_store.get_window(coin, tf)
        if not candles:
            try:
                now = int(time.time() * 1000)
                tf_ms = _timeframe_ms(tf)
                start = now - tf_ms * max(cfg.history_bars * 3, 300)
                raw_c = await hl.candle_snapshot(coin, tf, start, now)
                candles = [Candle.from_hl(x) for x in raw_c]
                candles.sort(key=lambda c: c.t)
                candles = [c for c in candles if c.T <= now]
                candles = candles[-cfg.history_bars :]
            except Exception as e:
                logger.info("tg:chart_fetch_failed user=%s tf=%s coin=%s err=%s", uid, tf, coin, e)
                await message.answer("Не удалось загрузить свечи по этой паре.")
                return

        if len(candles) < 5:
            await message.answer("Недостаточно свечей для построения графика.")
            return

        closes = [c.c for c in candles]
        alli = alligator_ema(closes)
        jaw = alli["jaw"]
        teeth = alli["teeth"]
        lips = alli["lips"]
        fpts = _calc_fractal_points(candles, teeth_series=teeth)
        data = build_chart_png(coin, tf, candles, jaw, teeth, lips, fpts, levels=[])
        await message.answer_photo(BufferedInputFile(data, filename=f"{coin}_{tf}.png"), reply_markup=_msg_controls_menu().as_markup())

    @dp.callback_query(F.data == "pair_add:prompt")
    async def pair_add_prompt(cb: CallbackQuery, state: FSMContext):
        logger.info("tg:cb user=%s data=%s", cb.from_user.id, cb.data)
        await state.set_state(AddPairFlow.waiting_pair)
        await cb.answer()
        await cb.message.answer("Введи пару в формате HYPE-USDC (можно просто HYPE).")

    @dp.message(AddPairFlow.waiting_pair)
    async def pair_add_input(message: Message, state: FSMContext):
        uid = message.from_user.id
        raw = message.text or ""
        coin = _parse_pair_input(raw)
        logger.info("tg:input user=%s flow=add_pair raw=%s parsed=%s", uid, raw, coin)
        if not coin:
            await message.answer("Не понял формат. Пример: HYPE-USDC")
            return
        try:
            await message.delete()
        except Exception:
            pass
        _, user_subs = await user_ctx(uid)
        current = set(await user_subs.get_user_pairs(uid))
        if coin not in current:
            await user_subs.toggle_pair(uid, coin)
            logger.info("tg:pair user=%s action=add pair=%s", uid, coin)
        else:
            logger.info("tg:pair user=%s action=already_selected pair=%s", uid, coin)
        await state.clear()
        pairs = set(await user_subs.get_user_pairs(uid))
        await message.answer("Пара добавлена.", reply_markup=_pairs_menu(cfg, pairs).as_markup())

    @dp.callback_query(F.data == "menu:tf")
    async def menu_tf(cb: CallbackQuery):
        logger.info("tg:cb user=%s data=%s", cb.from_user.id, cb.data)
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
        logger.info("tg:tf user=%s old=%s new=%s", uid, old_tf, tf)
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
        logger.info("tg:cb user=%s data=%s", uid, cb.data)
        _, user_subs = await user_ctx(uid)
        pairs = set(await user_subs.get_user_pairs(uid))
        await cb.message.edit_text(
            "Выберите пары для отслеживания.\n\nМожно добавить вручную: отправьте символ (например HYPE-USDC).",
            reply_markup=_pairs_menu(cfg, pairs).as_markup(),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("pair:"))
    async def toggle_pair(cb: CallbackQuery):
        uid = cb.from_user.id
        pair = cb.data.split(":", 1)[1].upper()
        logger.info("tg:pair user=%s action=toggle pair=%s", uid, pair)
        _, user_subs = await user_ctx(uid)
        now_selected = await user_subs.toggle_pair(uid, pair)
        logger.info("tg:pair user=%s action=toggled pair=%s selected=%s", uid, pair, now_selected)
        pairs = set(await user_subs.get_user_pairs(uid))
        await cb.message.edit_reply_markup(reply_markup=_pairs_menu(cfg, pairs).as_markup())
        await cb.answer()

    @dp.callback_query(F.data == "menu:risk")
    async def menu_risk(cb: CallbackQuery):
        uid = cb.from_user.id
        logger.info("tg:cb user=%s data=%s", uid, cb.data)
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
        logger.info("tg:risk user=%s risk_pct=%s", uid, v)
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
        await cb.message.edit_text(text, reply_markup=_msg_controls_menu().as_markup())
        await _remember_last(uid, cb.message.message_id)

    @dp.callback_query(F.data == "menu:positions")
    async def positions(cb: CallbackQuery):
        uid = cb.from_user.id
        await cb.answer()
        text, kb = await _build_positions_view(uid)
        await cb.message.edit_text(text, reply_markup=kb.as_markup())

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
        balance = float(cfg.virtual_equity) + float(total_r) + float(total_u)
        u_emoji = "🟩" if total_u >= 0 else "🟥"
        r_emoji = "🟩" if total_r >= 0 else "🟥"
        b_emoji = "💰" if balance >= float(cfg.virtual_equity) else "💸"
        await cb.answer()
        await cb.message.edit_text(
            f"💰 P&L (TF {tf})\n\n{b_emoji} Баланс: {balance:.2f} USDC\n{r_emoji} Realized: {total_r:+.2f} USDC\n{u_emoji} Unrealized: {total_u:+.2f} USDC\n🏦 База: {float(cfg.virtual_equity):.2f} USDC",
            reply_markup=_msg_controls_menu().as_markup(),
        )
        await _remember_last(uid, cb.message.message_id)

    @dp.callback_query(F.data == "menu:chart")
    async def chart_menu(cb: CallbackQuery):
        uid = cb.from_user.id
        _, user_subs = await user_ctx(uid)
        pairs = await user_subs.get_user_pairs(uid)
        await cb.answer()
        await cb.message.edit_text("Выбери пару для графика (или введи вручную):", reply_markup=_chart_pairs_menu(pairs).as_markup())

    @dp.callback_query(F.data.startswith("chart:"))
    async def chart_pair(cb: CallbackQuery):
        uid = cb.from_user.id
        tf, _ = await user_ctx(uid)
        pair = cb.data.split(":", 1)[1].upper()
        logger.info("tg:chart user=%s tf=%s pair=%s", uid, tf, pair)
        candles = await candle_store.get_window(pair, tf)
        if not candles:
            await cb.answer("Нет свечей")
            return
        st = await trade_state.get(uid, pair, tf)
        levels: list[PriceLevel] = []
        orders = _extract_orders(st)
        for o in orders:
            side_u = o.side.upper()
            levels.extend(
                [
                    PriceLevel(price=o.trigger, label=f"Trigger {side_u}", color="#0ea5e9", linestyle="--", linewidth=1.2),
                    PriceLevel(price=o.stop_loss, label=f"SL {side_u}", color="#ef4444", linestyle="-", linewidth=1.0),
                    PriceLevel(price=o.take_profit, label=f"TP {side_u}", color="#22c55e", linestyle="-", linewidth=1.0),
                ]
            )
        if st.get("pos"):
            p = Position.from_dict(st["pos"])
            levels.extend(
                [
                    PriceLevel(price=p.entry, label="Entry", color="#0ea5e9", linestyle="-", linewidth=1.2),
                    PriceLevel(price=p.stop_loss, label="SL", color="#ef4444", linestyle="-", linewidth=1.0),
                    PriceLevel(price=p.take_profit, label="TP", color="#22c55e", linestyle="-", linewidth=1.0),
                ]
            )
        closes = [c.c for c in candles]
        alli = alligator_ema(closes)
        jaw = alli["jaw"]
        teeth = alli["teeth"]
        lips = alli["lips"]
        fpts = _calc_fractal_points(candles, teeth_series=teeth)
        logger.info("tg:chart user=%s tf=%s pair=%s fractals=%s candles=%s", uid, tf, pair, len(fpts), len(candles))
        data = build_chart_png(pair, tf, candles, jaw, teeth, lips, fpts, levels=levels)
        await cb.answer()
        await cb.message.answer_photo(BufferedInputFile(data, filename=f"{pair}_{tf}.png"), reply_markup=_msg_controls_menu().as_markup())

    @dp.callback_query(F.data == "msg:delete")
    async def delete_message(cb: CallbackQuery):
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.answer()

    logger.info("Telegram bot polling started")
    await dp.start_polling(bot)

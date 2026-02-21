import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards import timeframe_kb, cancel_kb, main_menu_kb
from bot.messages import (
    SUB_ASK_TOKEN, SUB_ASK_TF, SUB_ALREADY, SUB_SUCCESS, WELCOME
)
from core.redis_client import redis_client
from core.worker import get_ws_client
from core.hyperliquid_ws import fetch_historical_candles
from config import config

logger = logging.getLogger(__name__)
router = Router()


class SubscribeStates(StatesGroup):
    waiting_token = State()
    waiting_tf = State()


# ── Шаг 1: нажали "Подписаться" ──────────────────────────────────────────────

@router.callback_query(F.data == "subscribe")
async def start_subscribe(callback: CallbackQuery, state: FSMContext):
    logger.info(f"User {callback.from_user.id} started subscription process")
    await state.set_state(SubscribeStates.waiting_token)
    await callback.message.edit_text(
        SUB_ASK_TOKEN, parse_mode="HTML", reply_markup=cancel_kb()
    )
    await callback.answer()


# ── Шаг 2: ввели токен ───────────────────────────────────────────────────────

@router.message(SubscribeStates.waiting_token)
async def got_token(message: Message, state: FSMContext):
    token = message.text.strip().upper()

    # Валидация — только буквы и цифры, до 10 символов
    if not token.isalnum() or len(token) > 10:
        await message.answer(
            "⚠️ Некорректный тикер. Введи, например: <code>ETH</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(token=token)
    await state.set_state(SubscribeStates.waiting_tf)
    await message.answer(SUB_ASK_TF, reply_markup=timeframe_kb())


# ── Шаг 3: выбрали таймфрейм ─────────────────────────────────────────────────

@router.callback_query(SubscribeStates.waiting_tf, F.data.startswith("tf:"))
async def got_timeframe(callback: CallbackQuery, state: FSMContext):
    tf = callback.data.split(":", 1)[1]
    data = await state.get_data()
    token = data.get("token", "")
    user_id = callback.from_user.id

    await state.clear()

    tf_display = config.TF_DISPLAY.get(tf, tf)

    # Проверяем дубликат
    added = await redis_client.add_subscription(user_id, token, tf)
    if not added:
        logger.info(f"User {user_id} already subscribed to {token}/{tf}")
        await callback.message.edit_text(
            SUB_ALREADY.format(token=token, tf_display=tf_display),
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
        await callback.answer()
        return

    logger.info(f"User {user_id} successfully subscribed to {token}/{tf}")

    # Инициализируем исторические свечи
    await callback.message.edit_text(
        f"⏳ Загружаю историю для <b>{token}</b> | {tf_display}...",
        parse_mode="HTML",
    )
    try:
        hist = await fetch_historical_candles(token, tf, count=200)
        if hist:
            await redis_client.set_candles_bulk(user_id, token, tf, hist)
            logger.info(f"Loaded {len(hist)} candles for {token}/{tf}")
    except Exception as e:
        logger.error(f"Failed to load history {token}/{tf}: {e}")

    # Подписываемся на WebSocket
    ws = get_ws_client()
    if ws:
        await ws.subscribe(user_id, token, tf)

    await callback.message.edit_text(
        SUB_SUCCESS.format(token=token, tf_display=tf_display),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()
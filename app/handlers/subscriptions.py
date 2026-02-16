"""Диалог оформления подписки на сигналы и просмотр активных подписок."""

import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.services.subscription_service import SubscriptionService

router = Router()
logger = logging.getLogger(__name__)

class SubscriptionFSM(StatesGroup):
    pair = State()
    timeframe = State()
    adx = State()
    atr = State()
    risk = State()


@router.message(F.text == "📡 Получать сигналы")
async def start_subscription(message: Message, state: FSMContext):
    """Старт диалога: запрос пары (тикера)."""
    logger.info("Старт оформления подписки user=%s", message.from_user.id)
    await state.set_state(SubscriptionFSM.pair)
    await message.answer("Введите пару:")


@router.message(F.text == "📋 Активные подписки")
async def show_subscriptions(message: Message, state: FSMContext):
    """Показывает список действующих подписок пользователя."""
    subs = await SubscriptionService.get_user_subscriptions(message.from_user.id)

    if not subs:
        await message.answer("У вас пока нет активных подписок.")
        return

    lines = []
    for sub in subs:
        lines.append(
            f"• {sub['pair']} | TF: {sub['timeframe']} | ADX ≥ {sub['adx']} | ATR ≥ {sub['atr']} | Risk: {sub['risk']}%"
        )

    text = "📋 Ваши активные подписки:\n\n" + "\n".join(lines)
    await message.answer(text)
    logger.info("Отправлен список подписок user=%s count=%s", message.from_user.id, len(subs))


@router.message(SubscriptionFSM.pair)
async def process_pair(message: Message, state: FSMContext):
    """Принимает пару и запрашивает таймфрейм."""
    await state.update_data(pair=message.text.upper())
    await state.set_state(SubscriptionFSM.timeframe)
    await message.answer("Введите таймфрейм:")


@router.message(SubscriptionFSM.timeframe)
async def process_tf(message: Message, state: FSMContext):
    """Принимает таймфрейм и запрашивает порог ADX."""
    await state.update_data(timeframe=message.text)
    await state.set_state(SubscriptionFSM.adx)
    await message.answer("Минимальный ADX:")


@router.message(SubscriptionFSM.adx)
async def process_adx(message: Message, state: FSMContext):
    """Принимает порог ADX и запрашивает порог ATR."""
    try:
        value = float(message.text.replace("%", "").replace(",", "."))
    except ValueError:
        await message.answer("Введите числовое значение ADX (например, 25 или 25.5).")
        return

    await state.update_data(adx=value)
    await state.set_state(SubscriptionFSM.atr)
    await message.answer("Минимальный ATR:")
    logger.debug("user=%s adx=%.4f", message.from_user.id, value)


@router.message(SubscriptionFSM.atr)
async def process_atr(message: Message, state: FSMContext):
    """Принимает порог ATR и запрашивает риск в процентах."""
    try:
        value = float(message.text.replace("%", "").replace(",", "."))
    except ValueError:
        await message.answer("Введите числовое значение ATR (например, 0.5 или 1.2).")
        return

    await state.update_data(atr=value)
    await state.set_state(SubscriptionFSM.risk)
    await message.answer("Risk %:")
    logger.debug("user=%s atr=%.4f", message.from_user.id, value)


@router.message(SubscriptionFSM.risk)
async def process_risk(message: Message, state: FSMContext):
    """Принимает риск и создаёт подписку."""
    data = await state.get_data()
    try:
        value = float(message.text.replace("%", "").replace(",", "."))
    except ValueError:
        await message.answer("Введите риск в процентах числом (например, 2 или 2.5).")
        return

    data["risk"] = value

    await SubscriptionService.create_subscription(
        user_id=message.from_user.id,
        data=data
    )

    await message.answer("✅ Подписка создана")
    await state.clear()
    logger.info("Создана подписка user=%s data=%s", message.from_user.id, data)

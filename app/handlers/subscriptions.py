"""Диалог оформления подписки на сигналы и просмотр активных подписок."""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.services.subscription_service import SubscriptionService

router = Router()
logger = logging.getLogger(__name__)

class SubscriptionFSM(StatesGroup):
    pair = State()
    timeframe = State()
    risk = State()


@router.message(F.text == "📡 Получать сигналы")
async def start_subscription(message: Message, state: FSMContext):
    """Старт диалога: запрос пары (тикера)."""
    logger.info("Старт оформления подписки user=%s", message.from_user.id)
    await state.set_state(SubscriptionFSM.pair)
    await message.answer("Введите тикер (например, BTC, ETH):")


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
            f"• {sub['pair']} | TF: {sub['timeframe']} | Risk: {sub['risk']}%"
        )

    text = "📋 Ваши активные подписки:\n\n" + "\n".join(lines)

    keyboard_rows = []
    for sub in subs:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить {sub['pair']} | TF: {sub['timeframe']}",
                    callback_data=f"cancel_sub:{sub['id']}"
                )
            ]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    await message.answer(text, reply_markup=kb)
    logger.info("Отправлен список подписок user=%s count=%s", message.from_user.id, len(subs))


@router.callback_query(F.data.startswith("cancel_sub:"))
async def cancel_subscription(cb: CallbackQuery):
    """Обрабатывает нажатие кнопки отмены подписки и обновляет список в сообщении."""
    _, sub_id = cb.data.split(":", 1)
    await SubscriptionService.delete_subscription(cb.from_user.id, sub_id)
    logger.info("Отменена подписка user=%s id=%s", cb.from_user.id, sub_id)

    subs = await SubscriptionService.get_user_subscriptions(cb.from_user.id)

    if not subs:
        await cb.message.edit_text("У вас пока нет активных подписок.")
        await cb.answer("Подписка отменена", show_alert=True)
        return

    lines = []
    for sub in subs:
        lines.append(
            f"• {sub['pair']} | TF: {sub['timeframe']} | Risk: {sub['risk']}%"
        )

    text = "📋 Ваши активные подписки:\n\n" + "\n".join(lines)

    keyboard_rows = []
    for sub in subs:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить {sub['pair']} | TF: {sub['timeframe']}",
                    callback_data=f"cancel_sub:{sub['id']}"
                )
            ]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Подписка отменена", show_alert=True)


@router.message(SubscriptionFSM.pair)
async def process_pair(message: Message, state: FSMContext):
    """Принимает пару и запрашивает таймфрейм."""
    await state.update_data(pair=message.text.upper())
    await state.set_state(SubscriptionFSM.timeframe)
    await message.answer("Введите таймфрейм:")


@router.message(SubscriptionFSM.timeframe)
async def process_tf(message: Message, state: FSMContext):
    """Принимает таймфрейм и запрашивает риск."""
    await state.update_data(timeframe=message.text)
    await state.set_state(SubscriptionFSM.risk)
    await message.answer("Risk %:")





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

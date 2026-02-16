from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.services.subscription_service import SubscriptionService

router = Router()

class SubscriptionFSM(StatesGroup):
    pair = State()
    timeframe = State()
    adx = State()
    atr = State()
    risk = State()


@router.message(F.text == "📡 Получать сигналы")
async def start_subscription(message: Message, state: FSMContext):
    await state.set_state(SubscriptionFSM.pair)
    await message.answer("Введите пару:")


@router.message(SubscriptionFSM.pair)
async def process_pair(message: Message, state: FSMContext):
    await state.update_data(pair=message.text.upper())
    await state.set_state(SubscriptionFSM.timeframe)
    await message.answer("Введите таймфрейм:")


@router.message(SubscriptionFSM.timeframe)
async def process_tf(message: Message, state: FSMContext):
    await state.update_data(timeframe=message.text)
    await state.set_state(SubscriptionFSM.adx)
    await message.answer("Минимальный ADX:")


@router.message(SubscriptionFSM.adx)
async def process_adx(message: Message, state: FSMContext):
    await state.update_data(adx=float(message.text))
    await state.set_state(SubscriptionFSM.atr)
    await message.answer("Минимальный ATR:")


@router.message(SubscriptionFSM.atr)
async def process_atr(message: Message, state: FSMContext):
    await state.update_data(atr=float(message.text))
    await state.set_state(SubscriptionFSM.risk)
    await message.answer("Risk %:")


@router.message(SubscriptionFSM.risk)
async def process_risk(message: Message, state: FSMContext):
    data = await state.get_data()
    data["risk"] = float(message.text)

    await SubscriptionService.create_subscription(
        user_id=message.from_user.id,
        data=data
    )

    await message.answer("✅ Подписка создана")
    await state.clear()

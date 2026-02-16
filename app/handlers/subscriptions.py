from aiogram.fsm.state import State, StatesGroup

class SubscriptionFSM(StatesGroup):
    pair = State()
    timeframe = State()
    adx_threshold = State()
    atr_threshold = State()
    risk_percent = State()

@router.message(F.text == "📡 Получать сигналы")
async def start_subscription(message: Message, state: FSMContext):
    await state.set_state(SubscriptionFSM.pair)
    await message.answer("Введите торговую пару (например BTCUSDC):")

@router.message(SubscriptionFSM.pair)
async def process_pair(message: Message, state: FSMContext):
    pair = message.text.upper()

    if not is_valid_pair(pair):
        return await message.answer("❌ Неверная пара")

    if await subscription_service.count_user_subs(message.from_user.id) >= 3:
        return await message.answer("⚠️ Лимит 3 пары на пользователя")

    await state.update_data(pair=pair)
    await state.set_state(SubscriptionFSM.timeframe)

    await message.answer("Введите таймфрейм (5, 15, 30, 60):")

@router.message(SubscriptionFSM.timeframe)
async def process_tf(message: Message, state: FSMContext):
    tf = message.text

    if tf not in ["5", "15", "30", "60"]:
        return await message.answer("❌ Допустимо: 5, 15, 30, 60")

    await state.update_data(timeframe=tf)
    await state.set_state(SubscriptionFSM.adx_threshold)

    await message.answer("Введите минимальный ADX threshold (например 20):")

@router.message(SubscriptionFSM.adx_threshold)
async def process_adx(message: Message, state: FSMContext):
    try:
        adx = float(message.text)
    except:
        return await message.answer("Введите число")

    await state.update_data(adx_threshold=adx)
    await state.set_state(SubscriptionFSM.atr_threshold)

    await message.answer("Введите минимальный ATR фильтр (например 0.5):")

@router.message(SubscriptionFSM.atr_threshold)
async def process_atr(message: Message, state: FSMContext):
    try:
        atr = float(message.text)
    except:
        return await message.answer("Введите число")

    await state.update_data(atr_threshold=atr)
    await state.set_state(SubscriptionFSM.risk_percent)

    await message.answer("Введите риск на сделку % (например 2):")

@router.message(SubscriptionFSM.risk_percent)
async def process_risk(message: Message, state: FSMContext):
    try:
        risk = float(message.text)
    except:
        return await message.answer("Введите число")

    data = await state.get_data()

    subscription = {
        "pair": data["pair"],
        "timeframe": data["timeframe"],
        "adx_threshold": data["adx_threshold"],
        "atr_threshold": data["atr_threshold"],
        "risk_percent": risk
    }

    await subscription_service.create_subscription(
        user_id=message.from_user.id,
        subscription=subscription
    )

    await message.answer("✅ Подписка создана")
    await state.clear()

@router.message(F.text == "📋 Активные подписки")
async def show_subscriptions(message: Message):
    subs = await subscription_service.get_user_subscriptions(message.from_user.id)

    if not subs:
        return await message.answer("У вас нет активных подписок")

    text = "Ваши подписки:\n"
    for s in subs:
        text += f"\n{s['pair']} | {s['timeframe']}m | ADX>{s['adx_threshold']}"

    await message.answer(text)

@router.callback_query(F.data.startswith("unsubscribe:"))
async def unsubscribe(call: CallbackQuery):
    sub_id = call.data.split(":")[1]

    await subscription_service.delete_subscription(call.from_user.id, sub_id)

    await call.answer("Удалено")
    await call.message.edit_text("Подписка удалена")

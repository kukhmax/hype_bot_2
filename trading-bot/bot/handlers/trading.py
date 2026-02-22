import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from bot.keyboards import cancel_kb
from core.trader import execute_trade

logger = logging.getLogger(__name__)

router = Router()


class TradeStates(StatesGroup):
    waiting_for_percentage = State()


@router.callback_query(F.data.startswith("trade:"))
async def cmd_trade_start(callback: CallbackQuery, state: FSMContext):
    """Нажатие на кнопку 'Открыть позицию' под сигналом."""
    await callback.answer()
    
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.message.answer("Ошибка в данных кнопки.")
        return
        
    _, token, direction, sl_str, tp_str = parts
    
    # Сохраняем данные для сделки
    await state.update_data(
        token=token,
        direction=direction,
        sl=float(sl_str),
        tp=float(tp_str)
    )
    
    await state.set_state(TradeStates.waiting_for_percentage)
    
    text = (
        f"Вы собираетесь открыть <b>{direction}</b> по <b>{token}</b>.\n"
        f"Стоп-Лосс: {sl_str}\n"
        f"Тейк-Профит: {tp_str}\n\n"
        f"👇 Введите <b>процент от баланса</b> (например, 10 или 2.5), который хотите использовать для маржи:"
    )
    await callback.message.reply(text, parse_mode="HTML", reply_markup=cancel_kb())


@router.message(TradeStates.waiting_for_percentage)
async def process_trade_percentage(message: Message, state: FSMContext):
    """Получение процента депозита и выполнение сделки."""
    text = message.text.strip().replace(",", ".")
    try:
        percentage = float(text)
        if percentage <= 0 or percentage > 100:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число от 0.1 до 100.")
        return

    data = await state.get_data()
    token = data["token"]
    direction = data["direction"]
    sl = data["sl"]
    tp = data["tp"]
    
    await state.clear()
    
    # Отправляем сообщение о процессе
    wait_msg = await message.answer(f"⏳ Отправка ордера на Hyperliquid для {token}...")
    
    # Выполняем сделку
    result = await execute_trade(
        token=token,
        direction=direction,
        percentage=percentage,
        sl_price=sl,
        tp_price=tp
    )
    
    await wait_msg.edit_text(result, parse_mode="HTML")

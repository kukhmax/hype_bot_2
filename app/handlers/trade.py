from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.services.order_executor import OrderExecutor
from app.services.position_manager import PositionManager

router = Router()


@router.callback_query(F.data.startswith("trade:"))
async def trade_handler(cb: CallbackQuery):
    # data format: trade:{pair}:{signal}:{risk}
    _, pair, signal, risk = cb.data.split(":")

    # простая логика: открываем позицию размером 1 контракт
    side = "BUY" if signal == "LONG" else "SELL"
    qty = 1.0

    pos = await PositionManager.get(cb.from_user.id, pair)
    if pos and pos.get("status") == "OPEN":
        await cb.answer("Позиция уже открыта", show_alert=True)
        return

    executor = OrderExecutor()
    res = await executor.market_order(pair, side, qty)

    # здесь можно получить цену исполнения — пока ставим 0.0
    await PositionManager.open(cb.from_user.id, pair, side, qty, entry=0.0)

    await cb.answer("Ордер отправлен", show_alert=True)


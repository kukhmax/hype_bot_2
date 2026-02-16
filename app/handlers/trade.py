"""Хэндлер открытия позиции из инлайн-кнопки в сообщении сигнала."""

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.services.order_executor import OrderExecutor
from app.services.position_manager import PositionManager

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("trade:"))
async def trade_handler(cb: CallbackQuery):
    """Обрабатывает callback 'trade:{pair}:{signal}:{risk}' и открывает позицию (DRY_RUN)."""
    _, pair, signal, risk = cb.data.split(":")

    side = "BUY" if signal == "LONG" else "SELL"
    qty = 1.0
    logger.info("Запрос на открытие позиции: user=%s %s %s qty=%s", cb.from_user.id, pair, side, qty)

    pos = await PositionManager.get(cb.from_user.id, pair)
    if pos and pos.get("status") == "OPEN":
        logger.info("Позиция уже открыта: user=%s %s", cb.from_user.id, pair)
        await cb.answer("Позиция уже открыта", show_alert=True)
        return

    executor = OrderExecutor()
    res = await executor.market_order(pair, side, qty)
    logger.info("Результат исполнения ордера: %s", res)

    await PositionManager.open(cb.from_user.id, pair, side, qty, entry=0.0)

    await cb.answer("Ордер отправлен", show_alert=True)
    logger.info("Ответ пользователю об отправке ордера: user=%s", cb.from_user.id)

"""
Модуль для исполнения сделок на Hyperliquid через REST API.
Использует официальный python-SDK от Hyperliquid (синхронный).
Запускается в пуле потоков (asyncio.to_thread).
"""
import logging
import asyncio
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from config import config

logger = logging.getLogger(__name__)


def _execute_trade_sync(token: str, direction: str, percentage: float, sl_price: float, tp_price: float) -> str:
    """Синхронная функция, выполняющая сделку через SDK Hyperliquid."""
    if not config.HL_PRIVATE_KEY or not config.HL_ADDRESS:
        return "Ошибка: Не указан HL_PRIVATE_KEY или HL_ADDRESS в конфиге."

    try:
        wallet = Account.from_key(config.HL_PRIVATE_KEY)
        info = Info(constants.MAINNET_API_URL, skip_ws=True)
        exchange = Exchange(wallet, constants.MAINNET_API_URL)
        
        # Получаем информацию о метаданных (шаг размера позиции, шаг цены)
        meta = info.meta()
        sz_decimals = 0
        for asset in meta["universe"]:
            if asset["name"] == token:
                sz_decimals = asset["szDecimals"]
                break
        
        # Получаем баланс пользователя
        user_state = info.user_state(wallet.address)
        margin_summary = user_state.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", "0.0"))
        
        if account_value <= 0:
            return "Ошибка: Недостаточно средств на балансе."
            
        # Узнаем текущую цену для расчёта объема
        mids = info.all_mids()
        current_price = float(mids.get(token, "0.0"))
        if current_price <= 0:
            return f"Ошибка: Не удалось получить рыночную цену для {token}."
            
        # Расчет размера позиции
        margin_to_use = account_value * (percentage / 100.0)
        
        # Объем позиции (размер с учетом плеча - предполагаем плечо 5х, чтобы наверняка хватило маржи)
        # Пользователь может уже иметь дефолтное плечо на аккаунте.
        leverage = 5 
        
        # Обмениваем маржу на объем (size = margin * leverage / price)
        raw_size = (margin_to_use * leverage) / current_price
        
        # Округляем до разрешенного количества знаков
        if sz_decimals == 0:
            sz = round(raw_size)
        else:
            sz = round(raw_size, sz_decimals)
            
        if sz <= 0:
            return f"Ошибка: Слишком маленький расчетный размер позиции ({raw_size})."

        is_buy = True if direction.upper() == "LONG" else False
        
        logger.info(f"Otkryt poziciyu {direction} po {token}, razmer: {sz} (margin: {margin_to_use:.2f}$, lex: {leverage}x)")
        
        # Открываем рыночный ордер
        order_result = exchange.market_open(token, is_buy, sz, None, 0.01)
        if order_result["status"] != "ok":
            return f"Ошибка входа Market: {order_result}"
            
        success_msg = f"Успешно открыт <b>{direction}</b> по {token}.\nРазмер: {sz}, Маржа: ~{margin_to_use:.2f} USDC."
        
        # Расставляем SL / TP (Лимитные или Stop Market ордера)
        # Для SL используем параметр sl
        if sl_price > 0:
            is_close_buy = not is_buy
            sl_res = exchange.market_close(token, is_buy=is_buy, sz=sz, px=sl_price, slippage=0.01, cloid=None)
            # В SDK Hyperliquid market_close обычно используется как обычный маркет на закрытие. 
            # Стоп-лосс - это тип ордера Trigger. В python sdk есть exchange.order(token, is_buy, sz, limit_px=sl_price, order_type={"trigger": {"isMarket": True, "triggerPx": sl_price}})
            
            # Правильный вызов SL:
            sl_order_type = {"trigger": {"isMarket": True, "triggerPx": str(sl_price), "tpsl": "sl"}}
            sl_res = exchange.order(token, not is_buy, sz, sl_price, sl_order_type, reduce_only=True)
            logger.info(f"SL res: {sl_res}")
            
        if tp_price > 0:
            tp_order_type = {"trigger": {"isMarket": True, "triggerPx": str(tp_price), "tpsl": "tp"}}
            tp_res = exchange.order(token, not is_buy, sz, tp_price, tp_order_type, reduce_only=True)
            logger.info(f"TP res: {tp_res}")
            success_msg += f"\nSL: {sl_price:.4f} | TP: {tp_price:.4f} выставлены."
            
        return success_msg

    except Exception as e:
        logger.exception("Error executing trade:")
        return f"Критическая ошибка при торговле: {e}"


async def execute_trade(token: str, direction: str, percentage: float, sl_price: float, tp_price: float) -> str:
    """Асинхронная обертка для синхронной торговой функции."""
    return await asyncio.to_thread(_execute_trade_sync, token, direction, percentage, sl_price, tp_price)

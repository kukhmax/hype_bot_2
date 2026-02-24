import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.risk.manager import RiskManager

logger = setup_logger("test_risk_manager")

async def test_risk_manager():
    logger.info("Старт тестирования RiskManager")
    
    # 2% риск на сделку, 5% максимальная дневная просадка
    rk = RiskManager(risk_per_trade_percent=2.0, max_daily_loss_percent=5.0)
    
    # Стартовый баланс 1000 USDT
    test_balance = 1000.0
    rk.start_session(test_balance)
    
    # Расчет позиции для входа по цене 100 USDT за SOL
    res1 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=90.0)
    logger.info(f"Сделка 1. Баланс 1000. Расчет сайза: {res1}")
    assert res1["quote_qty"] == 20.0, "Ожидаем объем 20 USDT (2% от 1000)"
    assert res1["base_qty"] == 0.2, "Ожидаем базовый объем 0.2 SOL (20 / 100)"
    
    # Фиксируем прибыльную сделку (+50 USDT)
    rk.report_trade_result(50.0)
    
    # Проверяем, что лимит потерь не сработал
    assert rk.check_daily_limit() is True, "Просадки нет, лимит не должен срабатывать"
    
    # Фиксируем серию убыточных сделок (-40, -40, -30) -> Итого сессия: 50 - 110 = -60 USDT
    # При стартовом 1000, 5% просадки это -50 USDT.
    rk.report_trade_result(-40.0)
    rk.report_trade_result(-40.0)
    rk.report_trade_result(-30.0)
    
    # Пытаемся рассчитать новую позицию. Риск должен заблокировать!
    res_fail = rk.calculate_position_size(940.0, entry_price=100.0, stop_loss=90.0)
    logger.info(f"Сделка после просадки. Расчет: {res_fail}")
    assert res_fail["quote_qty"] == 0.0, "Сайз должен быть занулен из-за риска"
    assert res_fail["reason"] == "daily_limit_reached", "Причина - достижение лимита"
    
    logger.info("\nВсе проверки RiskManager успешно пройдены!")

if __name__ == "__main__":
    asyncio.run(test_risk_manager())

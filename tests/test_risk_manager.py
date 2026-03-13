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
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 1: Entry=100, SL=90 (дистанция 10%)
    # risk_amount = 1000 * 0.02 = 20 USDT
    # sl_distance_pct = |100 - 90| / 100 = 0.10
    # quote_qty = 20 / 0.10 = 200 USDT
    # base_qty = 200 / 100 = 2.0 SOL
    # Убыток при SL: 200 * 0.10 = 20 USDT = 2% от депозита ✓
    # ═══════════════════════════════════════════════════════════════════
    res1 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=90.0)
    logger.info(f"Тест 1 (SL 10%): {res1}")
    assert res1["quote_qty"] == 200.0, f"Ожидаем 200 USDT, получили {res1['quote_qty']}"
    assert res1["base_qty"] == 2.0, f"Ожидаем 2.0 SOL, получили {res1['base_qty']}"
    logger.info("✅ Тест 1 пройден")
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 2: Entry=100, SL=95 (дистанция 5%)
    # risk_amount = 20, sl_dist = 0.05, quote_qty = 20 / 0.05 = 400 USDT
    # Убыток при SL: 400 * 0.05 = 20 USDT = 2% от депозита ✓
    # ═══════════════════════════════════════════════════════════════════
    res2 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=95.0)
    logger.info(f"Тест 2 (SL 5%): {res2}")
    assert res2["quote_qty"] == 400.0, f"Ожидаем 400 USDT, получили {res2['quote_qty']}"
    assert res2["base_qty"] == 4.0, f"Ожидаем 4.0 SOL, получили {res2['base_qty']}"
    logger.info("✅ Тест 2 пройден")
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 3: Entry=100, SL=99 (дистанция 1%)
    # risk_amount = 20, sl_dist = 0.01, quote_qty = 20 / 0.01 = 2000 USDT
    # Но баланс = 1000, значит cap на 1000 USDT
    # ═══════════════════════════════════════════════════════════════════
    res3 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=99.0)
    logger.info(f"Тест 3 (SL 1%, capped): {res3}")
    assert res3["quote_qty"] == 1000.0, f"Ожидаем 1000 USDT (cap балансом), получили {res3['quote_qty']}"
    assert res3["base_qty"] == 10.0, f"Ожидаем 10.0 SOL, получили {res3['base_qty']}"
    logger.info("✅ Тест 3 пройден (позиция ограничена балансом)")
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 4: SHORT — Entry=100, SL=110 (дистанция 10%)
    # Должно работать так же: quote_qty = 20 / 0.10 = 200 USDT
    # ═══════════════════════════════════════════════════════════════════
    res4 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=110.0)
    logger.info(f"Тест 4 (SHORT, SL 10%): {res4}")
    assert res4["quote_qty"] == 200.0, f"Ожидаем 200 USDT, получили {res4['quote_qty']}"
    logger.info("✅ Тест 4 пройден (SHORT)")
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 5: SL = Entry (нулевая дистанция) → должен вернуть invalid_sl
    # ═══════════════════════════════════════════════════════════════════
    res5 = rk.calculate_position_size(test_balance, entry_price=100.0, stop_loss=100.0)
    logger.info(f"Тест 5 (SL=Entry): {res5}")
    assert res5["quote_qty"] == 0.0, "Объем должен быть 0"
    assert res5["reason"] == "invalid_sl", f"Ожидаем reason=invalid_sl, получили {res5['reason']}"
    logger.info("✅ Тест 5 пройден (invalid SL)")
    
    # ═══════════════════════════════════════════════════════════════════
    # Тест 6: Дневной лимит потерь — серия убытков
    # ═══════════════════════════════════════════════════════════════════
    rk.report_trade_result(50.0)
    assert rk.check_daily_limit() is True, "Просадки нет, лимит не должен срабатывать"
    
    rk.report_trade_result(-40.0)
    rk.report_trade_result(-40.0)
    rk.report_trade_result(-30.0)
    
    # Итого PnL сессии: 50 - 40 - 40 - 30 = -60 USDT. Лимит 5% от 1000 = -50 USDT → превышен
    res_fail = rk.calculate_position_size(940.0, entry_price=100.0, stop_loss=90.0)
    logger.info(f"Тест 6 (дневной лимит): {res_fail}")
    assert res_fail["quote_qty"] == 0.0, "Сайз должен быть занулен из-за риска"
    assert res_fail["reason"] == "daily_limit_reached", f"Ожидаем daily_limit_reached, получили {res_fail['reason']}"
    logger.info("✅ Тест 6 пройден (дневной лимит)")
    
    logger.info("\n🎉 Все проверки RiskManager успешно пройдены!")

if __name__ == "__main__":
    asyncio.run(test_risk_manager())

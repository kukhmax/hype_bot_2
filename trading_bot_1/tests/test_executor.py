import asyncio
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.execution.mexc_executor import MEXCExecutor
from core.config import settings

logger = setup_logger("test_executor")

# Чтобы не зависеть от .env, принудительно ставим ключи для теста
settings.API_KEY = "test_key_123"
settings.API_SECRET = "test_secret_456"

async def test_mexc_executor():
    logger.info("Старт тестирования MEXCExecutor (с Mock-ом API)")
    
    executor = MEXCExecutor()
    
    # 1. Проверяем HMAC-генерацию (должна быть детерминированной)
    query_str = "symbol=SOLUSDT&side=BUY&type=MARKET&quoteOrderQty=10"
    signature = executor._generate_signature(query_str)
    
    logger.info(f"Сгенерированная подпись: {signature}")
    assert signature is not None and len(signature) == 64, "Длина HMAC SHA256 должна быть 64 символа (hex)"
    logger.info("[+] Подпись формируется корректно.")

    # 2. Мокаем _request метод, чтобы он не слал реальные запросы на биржу
    # (ведь ключи "test_secret_456" не пройдут аутентификацию)
    executor._request = AsyncMock(return_value={"orderId": "mock_id_999", "status": "NEW"})
    
    # Вызываем метод выставления Маркет Ордера
    resp = await executor.place_market_order("SOLUSDT", "BUY", quote_quantity=20.5)
    
    assert resp["orderId"] == "mock_id_999", "Ожидали ответ из Mock-объекта"
    logger.info(f"[+] Market Ордер успешно смоделирован. Ответ: {resp}")
    
    # Вызываем Лимитный ордер
    resp_limit = await executor.place_limit_order("BTCUSDT", "SELL", quantity=0.1, price=95000)
    assert resp_limit["status"] == "NEW", "Ожидали статус NEW"
    logger.info(f"[+] Limit Ордер успешно смоделирован. Ответ: {resp_limit}")
    
    # Закрываем сессию
    await executor.close()
    
    logger.info("\nВсе проверки MEXCExecutor успешно пройдены!")

if __name__ == "__main__":
    asyncio.run(test_mexc_executor())

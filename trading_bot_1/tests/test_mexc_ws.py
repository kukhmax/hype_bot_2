import asyncio
import os
import sys

# Добавляем корневую папку в sys.path для импорта модулей core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.mexc_client import MEXCWebSocketClient

logger = setup_logger("mexc_test")

async def test_mexc_deals():
    symbols = ["SOL_USDT"]
    logger.info(f"Начинаем тест подключения к MEXC для пар: {symbols}")
    
    # Инициализируем клиент
    client = MEXCWebSocketClient(symbols=symbols)
    
    # Функция-обработчик входящих сделок
    def on_deal_received(msg: dict):
        # Отфильтруем только реальные сделки (push.deal)
        channel = msg.get("channel", "")
        if channel == "push.deal":
            # У MEXC данные сделок приходят в msg["data"] как список словарей
            deal_list = msg.get("data", [])
            symbol = msg.get("symbol", "UNKNOWN")
            
            for deal_data in deal_list:
                # deal_data обычно содержит p (price), v (volume), T (1 - buy, 2 - sell), t (timestamp)
                price = deal_data.get("p")
                volume = deal_data.get("v")
                side = "BUY" if deal_data.get("T") == 1 else "SELL"
                
                logger.info(f"[{symbol}] Сделка: {side} {volume} по цене {price}")
        elif channel == "rs.sub.deal":
            logger.info(f"Подписка успешна: {msg}")
        else:
            # Логируем остальные сообщения для отладки
            logger.debug(f"Получено сообщение: {msg}")

    # Регистрируем callback
    client.add_callback(on_deal_received)
    
    # Запускаем клиент (в фоне)
    asyncio.create_task(client.connect())
    
    # Даем поработать 15 секунд и останавливаем
    await asyncio.sleep(15)
    
    logger.info("Тест завершен, останавливаем клиента...")
    await client.stop()

if __name__ == "__main__":
    try:
        asyncio.run(test_mexc_deals())
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")

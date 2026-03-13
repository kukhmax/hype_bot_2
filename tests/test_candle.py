import asyncio
import os
import sys
import time

# Добавляем корневую папку в sys.path для импорта модулей core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.candle_builder import CandleBuilder

logger = setup_logger("test_candle")

async def test_candle_builder():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем проверку CandleBuilder для {symbol} (1m свечи) с Redis")
    
    # Создаем 1-минутный билдер
    builder = CandleBuilder(symbol=symbol, timeframe_minutes=1)
    
    # Инициализация (попытка восстановить из Redis)
    await builder.init_state()
    
    # Callback для закрытых свечей
    def on_candle_closed(candle: dict):
        logger.info(f"!!! ПОЛУЧЕНА ЗАКРЫТАЯ СВЕЧА: {candle}")
        
    builder.add_callback(on_candle_closed)
    
    # Симулируем тики
    # Текущее время округлим к началу минуты для наглядности
    current_ms = int(time.time() * 1000)
    start_of_minute = (current_ms // 60000) * 60000
    
    ticks = [
        {"p": 100.0, "v": 1.5, "t": start_of_minute + 1000},   # Свеча 1, 1-я сек
        {"p": 101.5, "v": 2.0, "t": start_of_minute + 15000},  # Свеча 1, 15-я сек (High)
        {"p": 99.0,  "v": 0.5, "t": start_of_minute + 35000},  # Свеча 1, 35-я сек (Low)
        {"p": 100.5, "v": 1.0, "t": start_of_minute + 55000},  # Свеча 1, 55-я сек (Close)
        
        {"p": 102.0, "v": 3.0, "t": start_of_minute + 65000},  # Свеча 2! Тут закроется Свеча 1
        {"p": 103.0, "v": 1.0, "t": start_of_minute + 75000},  # Свеча 2
    ]
    
    for tk in ticks:
        await builder.process_tick(price=tk["p"], volume=tk["v"], timestamp_ms=tk["t"])
        # Небольшая пауза, чтобы было видно в логах
        await asyncio.sleep(0.5)

    # Проверим, что лежит в Redis для Свечи 2
    saved = await builder.redis.get(builder.redis_key)
    logger.info(f"Состояние незаконченной Свечи 2 в Redis: {saved}")
    
    # Закрываем пул Redis
    await builder.redis.aclose()
    logger.info("Тест завершен.")

if __name__ == "__main__":
    asyncio.run(test_candle_builder())

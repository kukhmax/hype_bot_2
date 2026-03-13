import asyncio
import os
import sys

# Добавляем корневую папку в sys.path для импорта модулей core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader

logger = setup_logger("test_historical")

async def test_historical_downloader():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем тест загрузки истории для {symbol}")
    
    # Пытаемся загрузить 1-минутные свечи (без start_ts: MEXC отдаст по умолчанию последние)
    # По умолчанию MEXC V1 отдает до 2000 или сколько-то свечей, проверим.
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=1)
    
    if df.empty:
        logger.error("DataFrame пуст! Данные не загрузились.")
        return
        
    logger.info(f"Получено записей: {len(df)}")
    
    # Выведем типы колонок
    logger.info(f"Типы данных:\n{df.dtypes}")
    
    # Выведем первые 5 и последние 5 записей
    logger.info("Первые свечи:")
    print(df.head())
    
    logger.info("\nПоследние свечи (наиболее свежие):")
    print(df.tail())
    
    # Проверим, в мс или сек приходит timestamp, и преобразуем в читаемую дату
    import pandas as pd
    first_ts = df["timestamp"].iloc[0]
    
    # Если таймстамп больше 10-значного числа (например 16xxxxxxxxx), скорее всего секунды 
    # Если 13 знаков - мс.
    logger.info(f"Пример Timestamp: {first_ts}")
    if len(str(int(first_ts))) == 13:
        logger.info("Похоже, Timestamp в миллисекундах. Конвертируем как мс.")
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    elif len(str(int(first_ts))) == 10:
        logger.info("Похоже, Timestamp в секундах. Конвертируем как s.")
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
        # Для внутренних нужд (совместимость с CandleBuilder) переведем в мс:
        df['timestamp_ms'] = df['timestamp'] * 1000
    else:
        logger.info(f"Неизвестный формат timestamp: {first_ts}")
        
    print(df[['datetime', 'open', 'high', 'low', 'close', 'volume']].tail())
    logger.info("Тест исторического API завершен.")

if __name__ == "__main__":
    asyncio.run(test_historical_downloader())

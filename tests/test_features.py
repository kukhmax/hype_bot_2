import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.features.indicators import FeatureEngineer

logger = setup_logger("test_features")

async def test_feature_engineer():
    logger.info("Начинаем тест Feature Engineer (Индикаторов)")
    
    # 1. Загружаем свечи
    symbol = "SOL_USDT"
    logger.info(f"Загрузка истории 15m для {symbol}")
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=15)
    
    if df.empty:
        logger.error("Нет данных!")
        return
        
    logger.info(f"Загружено свечей до обработки: {len(df)}")
    
    # 2. Прогоняем Feature Engineer
    df_with_features = FeatureEngineer.process_all_features(df)
    
    # 3. Выводим результаты
    logger.info(f"Свечей после очистки NaN: {len(df_with_features)}")
    
    logger.info("\nКолонки, которые у нас получились (features):")
    cols = sorted(list(df_with_features.columns))
    print(cols)
    
    logger.info("\nПример последних 2 строк:")
    print(df_with_features[['timestamp', 'close', 'ema_200', 'ema_50', 'rsi', 'atr', 'adx']].tail(2))
    
    logger.info("\nПроверка пройдена!")

if __name__ == "__main__":
    asyncio.run(test_feature_engineer())

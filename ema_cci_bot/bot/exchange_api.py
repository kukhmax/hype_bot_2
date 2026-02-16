import aiohttp
import pandas as pd
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"

async def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 500):
    """
    Асинхронно получает исторические свечи с Hyperliquid.
    """
    async with aiohttp.ClientSession() as session:
        try:
            # Формируем запрос к API Hyperliquid
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": timeframe,
                    "startTime": 0 # Получить последние доступные
                }
            }
            
            async with session.post(HYPERLIQUID_API_URL, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Ошибка API Hyperliquid для {symbol}: Status {response.status}")
                    return None
                
                data = await response.json()
                
                if not data or not isinstance(data, list):
                     logger.error(f"Некорректные данные от API для {symbol}")
                     return None

                # Hyperliquid возвращает данные в обратном порядке (новые в начале)
                # и берет слишком много. Отрезаем нужное количество.
                data = data[:limit]
                
                # Преобразуем в DataFrame
                df = pd.DataFrame(data)
                
                # Переименовываем столбцы для совместимости с pandas-ta
                # Формат HL: {'t': timestamp_ms, 'o': open, 'h': high, 'l': low, 'c': close, 'v': volume, ...}
                df = df.rename(columns={
                    't': 'timestamp', 
                    'o': 'open', 
                    'h': 'high', 
                    'l': 'low', 
                    'c': 'close', 
                    'v': 'volume'
                })
                
                # Приводим типы данных
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                
                # Сортируем по времени (старые вверху, новые внизу)
                df = df.sort_values('timestamp').reset_index(drop=True)
                
                return df

        except Exception as e:
            logger.error(f"Исключение при запросе к Hyperliquid для {symbol}: {e}")
            return None
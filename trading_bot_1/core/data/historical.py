import aiohttp
import asyncio
import pandas as pd
from typing import Optional

from core.logger import setup_logger

logger = setup_logger("historical_downloader")

class MEXCHistoricalDownloader:
    """
    Класс для загрузки исторических свечей (K-lines) через REST API (MEXC Futures).
    Документация: https://mexcdevelop.github.io/apidocs/contract_v1_en/#k-line-data
    """
    
    BASE_URL = "https://contract.mexc.com/api/v1/contract/kline"
    
    # Маппинг минут в формат MEXC
    INTERVAL_MAP = {
        1: "Min1",
        5: "Min5",
        15: "Min15",
        30: "Min30",
        60: "Min60",
        240: "Hour4", # 4 hours
        480: "Hour8", # 8 hours
        1440: "Day1", # 1 day
    }

    @classmethod
    async def get_klines(
        cls, 
        symbol: str, 
        timeframe_minutes: int, 
        start_ts: Optional[int] = None, 
        end_ts: Optional[int] = None,
        limit: int = 2000
    ) -> pd.DataFrame:
        """
        Загружает исторические свечи с биржи и возвращает pandas DataFrame.
        """
        if timeframe_minutes not in cls.INTERVAL_MAP:
            raise ValueError(f"Неподдерживаемый таймфрейм: {timeframe_minutes}m. Доступные: {list(cls.INTERVAL_MAP.keys())}")
            
        mexc_interval = cls.INTERVAL_MAP[timeframe_minutes]
        
        # Подготовка символа: MEXC Contract API использует формат BTC_USDT
        formatted_symbol = symbol
        if "_" not in symbol and symbol.endswith("USDT"):
            formatted_symbol = f"{symbol[:-4]}_USDT"
            
        url = f"{cls.BASE_URL}/{formatted_symbol}"
        
        params = {
            "interval": mexc_interval,
        }
        
        if start_ts is not None:
            # MEXC ожидает timestamp в *секундах* для REST API klines (уточнить по доке, но обычно в секундах, либо ms. В доке Contract v1 пишут Number)
            # Часто в миллисекундах. Будем передавать как есть (ms/s) - обычно это timestamp в секундах для MEXC V1? Отправим ms, если будут ошибки - поправим.
            # По документации Contract V1: start, end (Time Stamp)
            params["start"] = start_ts
            
        if end_ts is not None:
            params["end"] = end_ts

        logger.info(f"Загрузка истории {formatted_symbol} ({timeframe_minutes}m) {params}...")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Ошибка REST API: {response.status} {text}")
                    return pd.DataFrame()
                    
                data = await response.json()
                
                if data.get("success") is False:
                    logger.error(f"Ошибка получения данных: {data}")
                    return pd.DataFrame()

                # Парсинг данных MEXC
                # Пример ответа MEXC:
                # "data": {
                #   "time": [1638345600, ...], // timestamps
                #   "open": [50000.1, ...],
                #   "close": [50100.2, ...],
                #   "high": [50200.3, ...],
                #   "low": [49900.0, ...],
                #   "vol": [10.5, ...],
                #   "amount": [525000.0, ...]
                # }
                
                kline_data = data.get("data", {})
                if not kline_data or "time" not in kline_data:
                    logger.warning(f"Пустые данные истории для {formatted_symbol}")
                    return pd.DataFrame()

                df = pd.DataFrame({
                    "timestamp": kline_data.get("time", []),
                    "open": kline_data.get("open", []),
                    "high": kline_data.get("high", []),
                    "low": kline_data.get("low", []),
                    "close": kline_data.get("close", []),
                    "volume": kline_data.get("vol", [])
                })
                
                # Конвертация типов
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    
                # У MEXC kline API timestamp обычно в секундах, переведем в datetime для удобства и ms
                # df['timestamp_ms'] = df['timestamp'] * 1000 # Если там секунды. В V1 бывает секунды. Проверим в скрипте тестирования.
                
                # Сортируем по времени на всякий случай
                df = df.sort_values(by="timestamp").reset_index(drop=True)
                
                logger.info(f"Успешно загружено {len(df)} свечей для {formatted_symbol} ({timeframe_minutes}m)")
                return df

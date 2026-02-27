import asyncio
import json
from typing import Dict, Any, Callable, List
import redis.asyncio as redis

from core.logger import setup_logger
from core.config import settings

logger = setup_logger("candle_builder")

class CandleBuilder:
    """
    Класс для агрегации тиков (сделок) в свечи (OHLCV).
    - Сохраняет промежуточное состояние незакрытой свечи в Redis
    - Генерирует событие закрытия свечи
    """
    
    def __init__(self, symbol: str, timeframe_minutes: int = 1):
        self.symbol = symbol
        self.timeframe = timeframe_minutes
        self.tf_ms = timeframe_minutes * 60 * 1000
        
        # Подключаемся к Redis асинхронно
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.redis_key = f"trading_bot:candle:{self.symbol}:{self.timeframe}m"
        
        self.current_candle: Dict[str, Any] | None = None
        self.callbacks: List[Callable] = []
        self._tick_count = 0  # Счётчик тиков для периодического логирования

    async def init_state(self):
        """Восстанавливает состояние текущей свечи из Redis, если скрипт перезапустился."""
        try:
            saved = await self.redis.get(self.redis_key)
            if saved:
                self.current_candle = json.loads(saved)
                logger.info(f"[{self.symbol}] Загружена ранее не закрытая свеча из Redis: {self.current_candle}")
            else:
                self.current_candle = None
        except Exception as e:
            logger.error(f"Не удалось подключиться к Redis: {e}")
            self.current_candle = None

    def add_callback(self, callback: Callable):
        """Добавить функцию, вызываемую при ЗАКРЫТИИ свечи."""
        self.callbacks.append(callback)

    async def process_tick(self, price: float, volume: float, timestamp_ms: int):
        """Главный метод обработки приходящего тика из WebSocket."""
        # Определение начала интервала свечи (округление вниз)
        candle_start_time = (timestamp_ms // self.tf_ms) * self.tf_ms
        
        if self.current_candle is None:
            self.current_candle = self._create_new_candle(candle_start_time, price, volume)
        elif candle_start_time > self.current_candle["timestamp"]:
            # Время вышло за пределы текущей свечи -> закрываем её
            await self._emit_candle(self.current_candle)
            self.current_candle = self._create_new_candle(candle_start_time, price, volume)
        else:
            # Обновляем текущую свечу
            self._update_candle(self.current_candle, price, volume)

        self._tick_count += 1
        if self._tick_count % 100 == 0:
            logger.debug(f"[{self.symbol}] Обработано {self._tick_count} тиков. Текущая свеча (незакрытая): {self.current_candle}")

        # Сохраняем промежуточное состояние в Redis каждые 10 тиков
        if self._tick_count % 10 == 0:
            try:
                await self.redis.set(self.redis_key, json.dumps(self.current_candle))
            except Exception as e:
                # Логируем только каждую 100-ю ошибку, чтобы не спамить
                if not hasattr(self, '_redis_err_count'):
                    self._redis_err_count = 0
                self._redis_err_count += 1
                if self._redis_err_count <= 1 or self._redis_err_count % 100 == 0:
                    logger.warning(f"Ошибка записи в Redis (#{self._redis_err_count}): {e}")

    def _create_new_candle(self, timestamp: int, price: float, volume: float) -> dict:
        return {
            "timestamp": timestamp,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume
        }

    def _update_candle(self, candle: dict, price: float, volume: float):
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["volume"] += volume

    async def _emit_candle(self, candle: dict):
        """Рассылка готовой (закрытой) свечи подписчикам."""
        logger.info(f"[{self.symbol} {self.timeframe}m] Свеча ЗАКРЫТА: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']} Vol={candle['volume']}")
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(candle)
                else:
                    callback(candle)
            except Exception as e:
                logger.error(f"Ошибка в callback обработчике свечи: {e}")

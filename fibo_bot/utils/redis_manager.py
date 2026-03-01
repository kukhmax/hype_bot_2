"""
Fibo Bot — Redis State Manager.

Хранение текущего состояния бота в Redis:
- активные торговые пары
- настройки пользователя
- последние сигналы
- состояние pause/resume
"""

import json
from typing import Optional, Dict, Any

import redis.asyncio as aioredis

from config import config
from utils.logger import get_logger

logger = get_logger("redis_manager")


class RedisManager:
    """Менеджер состояния через Redis."""

    PREFIX = "fibo_bot:"

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        """Подключение к Redis."""
        try:
            self._redis = aioredis.from_url(
                config.redis.get_url(),
                decode_responses=True,
            )
            await self._redis.ping()
            logger.info("✅ Redis подключен")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Redis: {e}")
            raise

    async def disconnect(self):
        """Закрытие соединения."""
        if self._redis:
            await self._redis.close()
            logger.info("Redis отключен")

    def _key(self, name: str) -> str:
        return f"{self.PREFIX}{name}"

    # ─── Состояние бота ──────────────────────────────────────────────────

    async def set_bot_state(self, key: str, value: Any, expire: int = 0):
        """Записать значение."""
        data = json.dumps(value) if not isinstance(value, str) else value
        if expire > 0:
            await self._redis.setex(self._key(key), expire, data)
        else:
            await self._redis.set(self._key(key), data)

    async def get_bot_state(self, key: str) -> Optional[str]:
        """Прочитать значение."""
        return await self._redis.get(self._key(key))

    async def get_bot_state_json(self, key: str) -> Optional[Any]:
        """Прочитать JSON значение."""
        raw = await self._redis.get(self._key(key))
        if raw:
            return json.loads(raw)
        return None

    async def delete_state(self, key: str):
        """Удалить значение."""
        await self._redis.delete(self._key(key))

    # ─── Пауза / Активность ─────────────────────────────────────────────

    async def is_paused(self) -> bool:
        """Проверка: бот на паузе?"""
        val = await self.get_bot_state("paused")
        return val == "true"

    async def set_paused(self, paused: bool):
        """Установить паузу."""
        await self.set_bot_state("paused", "true" if paused else "false")

    # ─── Настройки пользователя ──────────────────────────────────────────

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Получить настройки пользователя."""
        data = await self.get_bot_state_json(f"user:{user_id}:settings")
        return data or {
            "symbol": config.trading.default_symbol,
            "timeframe": config.trading.default_timeframe,
            "risk": config.trading.risk_per_trade,
            "mode": config.trading.default_mode,
            "min_probability": config.trading.ml_threshold_balanced,
        }

    async def set_user_settings(self, user_id: int, settings: Dict[str, Any]):
        """Сохранить настройки пользователя."""
        await self.set_bot_state(f"user:{user_id}:settings", settings)

    # ─── Последний сигнал ────────────────────────────────────────────────

    async def save_last_signal(self, signal_data: Dict[str, Any]):
        """Сохранить последний сигнал."""
        await self.set_bot_state("last_signal", signal_data)

    async def get_last_signal(self) -> Optional[Dict[str, Any]]:
        """Получить последний сигнал."""
        return await self.get_bot_state_json("last_signal")

    # ─── Статистика ──────────────────────────────────────────────────────

    async def increment_signal_count(self):
        """Инкремент счётчика сигналов."""
        await self._redis.incr(self._key("stats:signals_total"))

    async def get_signal_count(self) -> int:
        """Получить количество сигналов."""
        val = await self._redis.get(self._key("stats:signals_total"))
        return int(val) if val else 0


# Глобальный экземпляр
redis_manager = RedisManager()

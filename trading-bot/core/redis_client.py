import json
import redis.asyncio as aioredis
from config import config


class RedisClient:
    """
    Клиент для взаимодействия с Redis. Обрабатывает подписки пользователей, 
    историю свечей и кулдауны сигналов.
    """
    def __init__(self):
        self._pool: aioredis.Redis | None = None

    async def connect(self):
        """Создает пул соединений с Redis по заданному URL."""
        self._pool = aioredis.from_url(
            config.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

    async def close(self):
        if self._pool:
            await self._pool.aclose()

    @property
    def r(self) -> aioredis.Redis:
        """Возвращает активный инстанс соединения; вызывает ошибку, если пул не инициализирован."""
        if not self._pool:
            raise RuntimeError("Redis not connected")
        return self._pool

    # ── Subscriptions ──────────────────────────────────────────────────────────

    async def add_subscription(self, user_id: int, token: str, tf: str) -> bool:
        """Добавить подписку. Возвращает True если добавлена, False если уже есть."""
        key = config.SUBS_KEY.format(user_id=user_id)
        member = f"{token.upper()}:{tf}"
        result = await self.r.sadd(key, member)
        return bool(result)

    async def remove_subscription(self, user_id: int, token: str, tf: str):
        key = config.SUBS_KEY.format(user_id=user_id)
        member = f"{token.upper()}:{tf}"
        await self.r.srem(key, member)
        # Чистим кэш свечей
        candle_key = config.CANDLES_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        await self.r.delete(candle_key)

    async def get_subscriptions(self, user_id: int) -> list[tuple[str, str]]:
        """Вернуть список (token, tf) для пользователя."""
        key = config.SUBS_KEY.format(user_id=user_id)
        members = await self.r.smembers(key)
        result = []
        for m in members:
            parts = m.split(":", 1)
            if len(parts) == 2:
                result.append((parts[0], parts[1]))
        return result

    async def get_all_subscriptions(self) -> dict[int, list[tuple[str, str]]]:
        """Вернуть все подписки всех пользователей."""
        pattern = config.SUBS_KEY.replace("{user_id}", "*")
        keys = await self.r.keys(pattern)
        result: dict[int, list[tuple[str, str]]] = {}
        for key in keys:
            user_id = int(key.split(":")[-1])
            members = await self.r.smembers(key)
            subs = []
            for m in members:
                parts = m.split(":", 1)
                if len(parts) == 2:
                    subs.append((parts[0], parts[1]))
            result[user_id] = subs
        return result

    # ── Candles ────────────────────────────────────────────────────────────────

    async def update_or_append_candle(self, user_id: int, token: str, tf: str, new_candle: dict, max_len: int = 1000):
        """
        Обновить последнюю свечу или добавить новую, сохраняя не более max_len элементов.
        Hyperliquid WS не присылает флаг закрытия, поэтому мы ориентируемся на timestamp `t`.
        """
        key = config.CANDLES_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        
        # Получаем последнюю свечу
        last_item = await self.r.lindex(key, -1)
        
        pipe = self.r.pipeline()
        if last_item:
            last_candle = json.loads(last_item)
            if last_candle.get("t") == new_candle.get("t"):
                # Обновляем текущую (не закрытую) свечу
                pipe.rpop(key)
        
        # Добавляем свечу (новую или обновленную)
        pipe.rpush(key, json.dumps(new_candle))
        pipe.ltrim(key, -max_len, -1)
        await pipe.execute()

    async def get_candles(self, user_id: int, token: str, tf: str) -> list[dict]:
        """Получить все сохраненные свечи (до max_len) для конкретной пары пользователя."""
        key = config.CANDLES_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        raw = await self.r.lrange(key, 0, -1)
        return [json.loads(c) for c in raw]

    async def set_candles_bulk(self, user_id: int, token: str, tf: str, candles: list[dict]):
        """Установить список свечей разом (для инициализации)."""
        key = config.CANDLES_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        await self.r.delete(key)
        if candles:
            pipe = self.r.pipeline()
            for c in candles:
                pipe.rpush(key, json.dumps(c))
            await pipe.execute()

    # ── AI Checks Setup Cache ──────────────────────────────────────────────────

    async def save_latest_setup(self, user_id: int, token: str, tf: str, setup_data: dict):
        """Сохранить детали последнего сетапа во временный ключ (на 1 час) для ручной проверки AI."""
        key = f"setup:{user_id}:{token.upper()}:{tf}"
        await self.r.set(key, json.dumps(setup_data), ex=3600)

    async def get_latest_setup(self, user_id: int, token: str, tf: str) -> dict | None:
        """Получить данные последнего сохраненного сетапа."""
        key = f"setup:{user_id}:{token.upper()}:{tf}"
        data = await self.r.get(key)
        if data:
            return json.loads(data)
        return None

    # ── Signal cooldown ────────────────────────────────────────────────────────

    async def is_on_cooldown(self, user_id: int, token: str, tf: str) -> bool:
        key = config.SIGNAL_COOLDOWN_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        return bool(await self.r.exists(key))

    async def set_cooldown(self, user_id: int, token: str, tf: str):
        key = config.SIGNAL_COOLDOWN_KEY.format(user_id=user_id, token=token.upper(), tf=tf)
        await self.r.set(key, "1", ex=config.SIGNAL_COOLDOWN_SEC)


redis_client = RedisClient()
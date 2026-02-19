import redis.asyncio as redis
import json
import os
from typing import List, Optional
from ..models.signal import Signal, Subscription
from datetime import datetime

class RedisService:
    def __init__(self):
        self.redis = None
    
    async def connect(self):
        """Подключение к Redis"""
        self.redis = await redis.from_url(
            f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/{os.getenv('REDIS_DB')}",
            decode_responses=True
        )
    
    async def add_subscription(self, user_id: int, token: str, timeframe: str):
        """Добавление подписки"""
        sub_key = f"user:{user_id}:subscriptions"
        sub_data = {
            "user_id": user_id,
            "token": token,
            "timeframe": timeframe,
            "created_at": datetime.now().isoformat(),
            "active": True
        }
        
        await self.redis.hset(
            sub_key,
            f"{token}_{timeframe}",
            json.dumps(sub_data)
        )
        
        # Также храним список всех активных токенов для обработки
        await self.redis.sadd("active_tokens", f"{token}_{timeframe}")
    
    async def remove_subscription(self, user_id: int, token: str, timeframe: str):
        """Удаление подписки"""
        sub_key = f"user:{user_id}:subscriptions"
        await self.redis.hdel(sub_key, f"{token}_{timeframe}")
        
        # Проверяем, остались ли еще подписчики на этот токен
        remaining = await self._check_token_subscribers(token, timeframe)
        if not remaining:
            await self.redis.srem("active_tokens", f"{token}_{timeframe}")
    
    async def get_user_subscriptions(self, user_id: int) -> List[Subscription]:
        """Получение всех подписок пользователя"""
        sub_key = f"user:{user_id}:subscriptions"
        subs_data = await self.redis.hgetall(sub_key)
        
        subscriptions = []
        for data in subs_data.values():
            sub_dict = json.loads(data)
            subscriptions.append(Subscription(**sub_dict))
        
        return subscriptions
    
    async def get_all_active_subscriptions(self) -> List[Subscription]:
        """Получение всех активных подписок (для обработчика)"""
        active_tokens = await self.redis.smembers("active_tokens")
        subscriptions = []
        
        for token_key in active_tokens:
            token, timeframe = token_key.split("_")
            # Сложная логика для сбора всех пользователей на этот токен
            # В реальном проекте нужно оптимизировать
            keys = await self.redis.keys("user:*:subscriptions")
            for key in keys:
                sub_data = await self.redis.hget(key, token_key)
                if sub_data:
                    subscriptions.append(Subscription(**json.loads(sub_data)))
        
        return subscriptions
    
    async def _check_token_subscribers(self, token: str, timeframe: str) -> bool:
        """Проверка, есть ли еще подписчики на токен"""
        token_key = f"{token}_{timeframe}"
        keys = await self.redis.keys("user:*:subscriptions")
        
        for key in keys:
            if await self.redis.hexists(key, token_key):
                return True
        return False
    
    async def save_signal(self, signal: Signal):
        """Сохранение сигнала в историю"""
        signal_key = f"signals:{signal.token}:{signal.timestamp.timestamp()}"
        await self.redis.setex(
            signal_key,
            86400 * 7,  # Храним 7 дней
            signal.model_dump_json()
        )

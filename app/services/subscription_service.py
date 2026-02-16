"""Сервис управления подписками пользователей на сигналы."""

import asyncio
import uuid
import json
import logging
from app.core.redis import redis_client
from app.config import MAX_SUBSCRIPTIONS
from app.services.history_loader import HistoryLoader
from app.services.candles import RedisCandleStore
from app.services.signal_lock import SignalLock
from app.services.notifier import Notifier
from app.services.ml_supertrend import MLAdaptiveSupertrendEngine

logger = logging.getLogger(__name__)

# --- КЭШ ОТСЛЕЖИВАЕМЫХ ПАР (в процессе) ---
_pair_counts: dict[str, int] = {}
_cache_built = False

async def _build_pair_cache():
    global _pair_counts, _cache_built
    _pair_counts = {}
    keys = await redis_client.keys("user:*:subscriptions")
    for k in keys:
        subs = await redis_client.hgetall(k)
        for sub in subs.values():
            d = json.loads(sub)
            sp = str(d["pair"]).upper()
            base = sp[:-4] if sp.endswith("USDC") else sp
            _pair_counts[base] = _pair_counts.get(base, 0) + 1
    _cache_built = True

async def get_tracked_pairs_cached() -> set[str]:
    if not _cache_built:
        await _build_pair_cache()
    return set(_pair_counts.keys())

def _cache_inc_pair(pair: str):
    base = pair.upper()
    if base.endswith("USDC"):
        base = base[:-4]
    _pair_counts[base] = _pair_counts.get(base, 0) + 1

async def _cache_dec_pair_by_sub(user_id: int, sub_id: str):
    key = f"user:{user_id}:subscriptions"
    raw = await redis_client.hget(key, sub_id)
    if not raw:
        return
    d = json.loads(raw)
    base = str(d["pair"]).upper()
    if base.endswith("USDC"):
        base = base[:-4]
    if base in _pair_counts:
        _pair_counts[base] -= 1
        if _pair_counts[base] <= 0:
            _pair_counts.pop(base, None)

class SubscriptionService:

    """CRUD-операции по подпискам: создать, получить, удалить, список пользователей."""

    @staticmethod
    async def create_subscription(user_id: int, data: dict):
        """Создаёт подписку и запускает фоновую предзагрузку истории + первичный расчёт."""
        key = f"user:{user_id}:subscriptions"

        subs = await redis_client.hgetall(key)

        if len(subs) >= MAX_SUBSCRIPTIONS:
            raise Exception("Subscription limit reached")

        for sub in subs.values():
            if json.loads(sub)["pair"] == data["pair"]:
                raise Exception("Already subscribed to this pair")

        sub_id = str(uuid.uuid4())
        await redis_client.hset(key, sub_id, json.dumps(data))
        logger.info("Создана подписка user=%s id=%s data=%s", user_id, sub_id, data)

        # обновим кэш отслеживаемых пар
        _cache_inc_pair(str(data["pair"]))

        # фоновая задача истории + первичного расчёта
        async def _post_create():
            try:
                tf = int(str(data["timeframe"]).replace("m", ""))
                pair = str(data["pair"]).upper()
            except Exception:
                return

            try:
                loaded = await HistoryLoader.preload_for_pair_tf(pair, tf, n=150)
                logger.info("История для подписки user=%s pair=%s tf=%sm загружена, свечей: %s", user_id, pair, tf, loaded)
            except Exception as e:
                logger.error("Ошибка загрузки истории для подписки user=%s pair=%s tf=%sm: %s", user_id, pair, tf, e)
                return

            base_pair = pair[:-4] if pair.endswith("USDC") else pair

            df = await RedisCandleStore.to_df(base_pair, tf, n=220)
            if df is None or len(df) < 120:
                logger.debug("Недостаточно истории для первичного сигнала user=%s pair=%s tf=%sm", user_id, base_pair, tf)
                return
            res = MLAdaptiveSupertrendEngine.evaluate(df, factor=3.0, atr_len=10, training_len=100, adx_confirm=20.0)
            logger.info("Первичный расчёт по подписке user=%s pair=%s tf=%sm -> %s", user_id, base_pair, tf, (res or {}).get("signal") or "нет сигнала")
            if not res or not res["signal"]:
                return

            ttl = tf * 60
            acquired = await SignalLock.acquire(user_id, base_pair, tf, ttl_seconds=ttl)
            if not acquired:
                logger.debug("Первичный сигнал подавлен дедупом user=%s pair=%s tf=%sm", user_id, base_pair, tf)
                return

            await Notifier.send_signal(
                user_id=user_id,
                pair=base_pair,
                signal=res["signal"],
                risk=data["risk"]
            )

        asyncio.create_task(_post_create())
        return sub_id

    @staticmethod
    async def get_user_subscriptions(user_id: int):
        """Возвращает список подписок пользователя (dict с id)."""
        key = f"user:{user_id}:subscriptions"
        subs = await redis_client.hgetall(key)

        result = []
        for sub_id, sub in subs.items():
            obj = json.loads(sub)
            obj["id"] = sub_id
            result.append(obj)

        return result

    @staticmethod
    async def delete_subscription(user_id: int, sub_id: str):
        """Удаляет подписку по идентификатору."""
        key = f"user:{user_id}:subscriptions"
        await _cache_dec_pair_by_sub(user_id, sub_id)
        await redis_client.hdel(key, sub_id)
        logger.info("Удалена подписка user=%s id=%s", user_id, sub_id)

    @staticmethod
    async def get_all_users():
        """Возвращает список user_id, у которых есть хотя бы одна подписка."""
        keys = await redis_client.keys("user:*:subscriptions")
        users = [int(k.split(":")[1]) for k in keys]
        return users

"""Работа с рыночным WebSocket Hyperliquid и генерация сигналов в реальном времени.

Функции:
- Подключение к WS и восстановление при разрывах.
- Подписка на поток mid-цен (allMids).
- Построение свечей через CandlePipeline и хранение в Redis.
- Расчёт сигналов ML Adaptive Supertrend и рассылка уведомлений с защитой от дубликатов.
"""

import asyncio
import json
import time
import websockets
from app.config import HYPERLIQUID_WS
from app.core.logger import setup_logger
from app.services.subscription_service import SubscriptionService
from app.services.notifier import Notifier
from app.services.candles import CandlePipeline, RedisCandleStore
from app.services.signal_lock import SignalLock
from app.services.indicator_cache import IndicatorCache
from app.services.subscription_service import get_tracked_pairs_cached
from app.services.ml_supertrend import MLAdaptiveSupertrendEngine

logger = setup_logger()


class MarketWS:
    """Класс подключения к Hyperliquid WS и маршрутизации данных."""

    def __init__(self):
        self.ws = None
        self.pipeline = CandlePipeline()
        self._raw_debug_count = 0
        self._last_ws_log_ts_ms = 0

    async def connect(self):
        """Бесконечный цикл подключения и прослушивания сообщений с авто-переподключением."""
        while True:
            try:
                logger.info("Connecting to Hyperliquid WS...")
                async with websockets.connect(HYPERLIQUID_WS, ping_interval=20) as ws:
                    self.ws = ws
                    await self.subscribe_all_pairs()
                    logger.info("Начинаем отслеживать цены через WS для подписанных пар")
                    await self.listen()

            except Exception as e:
                logger.error(f"WS reconnecting: {e}")
                await asyncio.sleep(5)

    async def subscribe_all_pairs(self):
        """Подписка на поток mid-цен по всем инструментам (allMids)."""
        sub_msg = {
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }
        logger.info("Отправляем подписку allMids")
        await self.ws.send(json.dumps(sub_msg))

    async def listen(self):
        """Читает сообщения WS и передаёт в обработчик."""
        async for message in self.ws:
            if self._raw_debug_count < 5:
                logger.debug("WS raw: %s", message[:500])
                self._raw_debug_count += 1
            data = json.loads(message)
            await self.process_message(data)

    async def process_message(self, data):
        """Обрабатывает тик: строит свечи, рассчитывает сигналы, рассылает уведомления."""
        payload = data.get("data") or {}
        if isinstance(payload, dict) and "mids" in payload:
            mids = payload.get("mids") or {}
            tracked = await get_tracked_pairs_cached()

            for pair, price in mids.items():
                coin = str(pair).upper()
                if coin not in tracked:
                    continue
                try:
                    await self._handle_mid(coin, float(price))
                except Exception as e:
                    logger.error("Ошибка обработки mid %s: %s", pair, e)
            return

        pair = data.get("coin") or payload.get("coin")
        price = data.get("mid") or payload.get("mid")
        if pair is None or price is None:
            return
        await self._handle_mid(pair, float(price))

    async def _handle_mid(self, pair: str, price: float):
        ts_ms = int(time.time() * 1000)
        if ts_ms - self._last_ws_log_ts_ms >= 5 * 60 * 1000:
            logger.info("WS: пример тика %s @ %.6f (раз в 5 минут)", pair, price)
            self._last_ws_log_ts_ms = ts_ms

        users = await SubscriptionService.get_all_users()
        tfs = set()
        user_subs = {}
        for uid in users:
            subs = await SubscriptionService.get_user_subscriptions(uid)
            user_subs[uid] = subs
            for s in subs:
                sp = str(s["pair"]).upper()
                coin = str(pair).upper()
                if sp == coin or sp == f"{coin}USDC":
                    try:
                        tfs.add(int(str(s["timeframe"]).replace("m", "")))
                    except Exception:
                        continue

        if not tfs:
            return

        closed_tfs = await self.pipeline.on_tick(pair, ts_ms, price, sorted(tfs))
        if not closed_tfs:
            return

        subs_by_tf: dict[int, list] = {}
        for uid, subs in user_subs.items():
            for s in subs:
                sp = str(s["pair"]).upper()
                coin = str(pair).upper()
                if not (sp == coin or sp == f"{coin}USDC"):
                    continue
                try:
                    tf = int(str(s["timeframe"]).replace("m", ""))
                except Exception:
                    continue
                if tf not in closed_tfs:
                    continue
                subs_by_tf.setdefault(tf, []).append((uid, s))

        for tf in sorted(subs_by_tf.keys()):
            df = await RedisCandleStore.to_df(pair, tf, n=220)
            if df is None or len(df) < 120:
                continue
            res = MLAdaptiveSupertrendEngine.evaluate(df, factor=3.0, atr_len=10, training_len=100, adx_confirm=20.0)
            if not res:
                continue
            await IndicatorCache.set_last(
                pair,
                tf,
                {
                    "t": int(df.iloc[-1]["timestamp"].value // 10**6),
                    "centroids": res["centroids"],
                    "cluster": res["cluster"],
                    "assigned": res["assigned"],
                    "st": res["st"],
                    "dir": res["dir"],
                    "adx": res["adx"],
                },
            )
            if not res["signal"]:
                continue
            for uid, s in subs_by_tf[tf]:
                ttl = tf * 60
                acquired = await SignalLock.acquire(uid, pair, tf, ttl_seconds=ttl)
                if not acquired:
                    logger.debug("Сигнал подавлен дедупом: user=%s pair=%s tf=%sm", uid, pair, tf)
                    continue
                logger.info("Сигнал %s user=%s pair=%s tf=%sm", res["signal"], uid, pair, tf)
                await Notifier.send_signal(user_id=uid, pair=pair, signal=res["signal"], risk=s["risk"])


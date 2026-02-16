import asyncio
import json
import time
import websockets
from app.config import HYPERLIQUID_WS
from app.core.logger import setup_logger
from app.services.signal_engine import SignalEngine
from app.services.subscription_service import SubscriptionService
from app.services.notifier import Notifier
from app.services.candles import CandlePipeline, RedisCandleStore
from app.services.signal_lock import SignalLock

logger = setup_logger()


class MarketWS:

    def __init__(self):
        self.ws = None
        self.pipeline = CandlePipeline()

    async def connect(self):
        while True:
            try:
                logger.info("Connecting to Hyperliquid WS...")
                async with websockets.connect(HYPERLIQUID_WS, ping_interval=20) as ws:
                    self.ws = ws
                    await self.subscribe_all_pairs()
                    await self.listen()

            except Exception as e:
                logger.error(f"WS reconnecting: {e}")
                await asyncio.sleep(5)

    async def subscribe_all_pairs(self):
        sub_msg = {
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }
        await self.ws.send(json.dumps(sub_msg))

    async def listen(self):
        async for message in self.ws:
            data = json.loads(message)
            await self.process_message(data)

    async def process_message(self, data):
        pair = data.get("coin") or (data.get("data") or {}).get("coin")
        price = data.get("mid") or (data.get("data") or {}).get("mid")
        if pair is None or price is None:
            return

        ts_ms = int(time.time() * 1000)

        users = await SubscriptionService.get_all_users()
        tfs = set()
        user_subs = {}
        for uid in users:
            subs = await SubscriptionService.get_user_subscriptions(uid)
            user_subs[uid] = subs
            for s in subs:
                if s["pair"] == pair:
                    try:
                        tfs.add(int(str(s["timeframe"]).replace("m", "")))
                    except Exception:
                        continue

        if not tfs:
            return

        await self.pipeline.on_tick(pair, ts_ms, float(price), sorted(tfs))

        for uid, subs in user_subs.items():
            for s in subs:
                if s["pair"] != pair:
                    continue
                try:
                    tf = int(str(s["timeframe"]).replace("m", ""))
                except Exception:
                    continue

                df = await RedisCandleStore.to_df(pair, tf, n=200)
                if df is None or len(df) < 30:
                    continue
                signal = SignalEngine.decide_from_candles(df, s["adx"], s["atr"])
                if not signal:
                    continue

                ttl = tf * 60
                acquired = await SignalLock.acquire(uid, pair, tf, ttl_seconds=ttl)
                if not acquired:
                    continue

                await Notifier.send_signal(
                    user_id=uid,
                    pair=pair,
                    signal=signal,
                    risk=s["risk"]
                )

import asyncio
import json
import websockets
from app.config import HYPERLIQUID_WS
from app.core.logger import setup_logger
from app.services.signal_engine import SignalEngine
from app.services.subscription_service import SubscriptionService
from app.services.notifier import Notifier

logger = setup_logger()


class MarketWS:

    def __init__(self):
        self.ws = None

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
        # подписываемся на все пары из Redis
        # для MVP можно подписаться на весь рынок
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
        if "data" not in data:
            return

        # здесь будет candle aggregation
        # пока — просто пример
        pair = data.get("coin")

        # получаем всех пользователей
        users = await SubscriptionService.get_all_users()

        for user_id in users:
            subs = await SubscriptionService.get_user_subscriptions(user_id)

            for sub in subs:
                if sub["pair"] == pair:

                    signal = SignalEngine.check_realtime(
                        pair=pair,
                        adx=sub["adx"],
                        atr=sub["atr"]
                    )

                    if signal:
                        await Notifier.send_signal(
                            user_id=user_id,
                            pair=pair,
                            signal=signal,
                            risk=sub["risk"]
                        )

import asyncio
import json
import logging
import aiohttp

logger = logging.getLogger(__name__)

class HyperliquidWebsocketClient:
    def __init__(self, wallet_address: str, base_url: str = "wss://api.hyperliquid.xyz/ws"):
        self.wallet = wallet_address
        self.base_url = base_url
        self.callbacks = []
        self._task = None

    def add_callback(self, cb):
        self.callbacks.append(cb)

    async def start(self):
        if not self.wallet:
            logger.warning("Hyperliquid WS: No wallet address provided, skipping.")
            return

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.base_url) as ws:
                        logger.info("Hyperliquid WS: Connected")
                        
                        # Подписываемся на события юзера
                        await ws.send_json({
                            "method": "subscribe",
                            "subscription": {"type": "userEvents", "user": self.wallet}
                        })
                        
                        # Подписываемся на данные аккаунта (позиции, маржа)
                        await ws.send_json({
                            "method": "subscribe",
                            "subscription": {"type": "webData2", "user": self.wallet}
                        })

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    for cb in self.callbacks:
                                        await cb(data)
                                except Exception as e:
                                    logger.error(f"Hyperliquid WS callback error: {e}")
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.error(f"Hyperliquid WS error: {e}")
            
            logger.info("Hyperliquid WS: Reconnecting in 5s...")
            await asyncio.sleep(5)

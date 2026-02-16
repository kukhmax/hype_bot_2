import aiohttp
from app.config import HYPERLIQUID_REST


class HyperliquidAPI:

    @staticmethod
    async def get_candles(pair: str, interval="5m"):
        url = f"{HYPERLIQUID_REST}/info"

        payload = {
            "type": "candleSnapshot",
            "coin": pair,
            "interval": interval
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json()

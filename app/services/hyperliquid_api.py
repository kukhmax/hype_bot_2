"""REST-обёртка над Hyperliquid: получение снимка свечей (пример)."""

import logging
import aiohttp
import json
from app.config import HYPERLIQUID_REST

logger = logging.getLogger(__name__)

class HyperliquidAPI:
    """Пример запроса к REST API Hyperliquid."""

    @staticmethod
    async def get_candles(pair: str, interval="5m"):
        """Возвращает JSON-снимок свечей для пары и интервала."""
        url = f"{HYPERLIQUID_REST}/info"

        payload = {
            "type": "candleSnapshot",
            "coin": pair,
            "interval": interval
        }

        logger.info("REST get_candles %s %s", pair, interval)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("REST error %s: %s", resp.status, text[:500])
                    return None
                text = await resp.text()
                data = json.loads(text)
                logger.debug("get_candles resp: %s", data)
                return data

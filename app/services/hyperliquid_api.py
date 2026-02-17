"""REST-обёртка над Hyperliquid: получение снимка свечей (пример)."""

import logging
import aiohttp
import json
import time
from app.config import HYPERLIQUID_REST

logger = logging.getLogger(__name__)

class HyperliquidAPI:
    """Пример запроса к REST API Hyperliquid."""

    @staticmethod
    async def get_candles(pair: str, interval="5m", n: int = 200):
        """Возвращает JSON-снимок свечей для пары и интервала."""
        url = f"{HYPERLIQUID_REST}/info"

        now_ms = int(time.time() * 1000)
        try:
            tf_minutes = int(str(interval).replace("m", ""))
        except Exception:
            tf_minutes = 1
        window_ms = n * tf_minutes * 60 * 1000
        start_ms = now_ms - window_ms

        payload = {
            "type": "candleSnapshot",
            "coin": pair,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms,
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

import os
import logging

import aiohttp

from app.config import HYPERLIQUID_REST

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        self.api_key = os.getenv("HL_API_KEY")
        self.api_secret = os.getenv("HL_API_SECRET")
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true" or not (self.api_key and self.api_secret)

    async def market_order(self, pair: str, side: str, qty: float):
        if self.dry_run:
            logger.info(f"[DRY RUN] place order {side} {qty} {pair}")
            return {"status": "ok", "dry_run": True}

        url = f"{HYPERLIQUID_REST}/order"
        payload = {
            "type": "market",
            "coin": pair,
            "side": side.lower(),
            "qty": qty,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Order failed: {resp.status} {txt}")
                    return {"status": "error", "code": resp.status, "body": txt}
                return await resp.json()


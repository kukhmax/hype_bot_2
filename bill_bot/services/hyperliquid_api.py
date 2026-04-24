from __future__ import annotations

import aiohttp


class HyperliquidInfoClient:
    def __init__(self, base_url: str = "https://api.hyperliquid.xyz"):
        self.base_url = base_url.rstrip("/")

    async def candle_snapshot(self, coin: str, interval: str, start_time_ms: int, end_time_ms: int) -> list[dict]:
        url = f"{self.base_url}/info"
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": int(start_time_ms),
                "endTime": int(end_time_ms),
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, list):
                    raise ValueError("Unexpected candleSnapshot response")
                return data

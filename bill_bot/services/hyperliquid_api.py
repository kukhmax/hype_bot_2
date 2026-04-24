from __future__ import annotations

import time
import aiohttp


class HyperliquidInfoClient:
    def __init__(self, base_url: str = "https://api.hyperliquid.xyz"):
        self.base_url = base_url.rstrip("/")
        self._meta_cache: dict | None = None
        self._meta_cache_ts: float = 0.0

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

    async def meta(self, cache_ttl_seconds: int = 3600) -> dict:
        now = time.time()
        if self._meta_cache is not None and (now - self._meta_cache_ts) < cache_ttl_seconds:
            return self._meta_cache

        url = f"{self.base_url}/info"
        payload = {"type": "meta"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected meta response")
                self._meta_cache = data
                self._meta_cache_ts = now
                return data

    async def get_sz_decimals(self, coin: str) -> int | None:
        m = await self.meta()
        uni = m.get("universe")
        if not isinstance(uni, list):
            return None
        coin = coin.upper()
        for it in uni:
            if not isinstance(it, dict):
                continue
            if str(it.get("name", "")).upper() != coin:
                continue
            if "szDecimals" not in it:
                return None
            try:
                return int(it["szDecimals"])
            except Exception:
                return None
        return None

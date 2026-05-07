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

    async def user_state(self, user_address: str) -> dict:
        url = f"{self.base_url}/info"
        payload = {
            "type": "clearinghouseState",
            "user": user_address
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected user_state response")
                return data

try:
    import eth_account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    import asyncio
except ImportError:
    pass

class HyperliquidExchangeClient:
    def __init__(self, wallet_address: str, private_key: str, base_url: str = None):
        self.wallet = wallet_address
        self.private_key = private_key
        try:
            from hyperliquid.utils import constants
            self.base_url = base_url or constants.MAINNET_API_URL
            self.account = eth_account.Account.from_key(private_key)
            self.exchange = Exchange(self.account, self.base_url, account_address=self.wallet)
        except Exception:
            self.exchange = None

    async def _run(self, func, *args, **kwargs):
        if self.exchange is None:
            raise RuntimeError("Hyperliquid SDK is not initialized")
        return await asyncio.to_thread(func, *args, **kwargs)

    async def place_order(self, coin: str, is_buy: bool, sz: float, limit_px: float, order_type: dict, reduce_only: bool = False) -> dict:
        return await self._run(self.exchange.order, coin, is_buy, sz, limit_px, order_type, reduce_only=reduce_only)

    async def cancel_order(self, coin: str, oid: int) -> dict:
        return await self._run(self.exchange.cancel, coin, oid)

    async def cancel_by_cloid(self, coin: str, cloid: str) -> dict:
        return await self._run(self.exchange.cancel, coin, None, cloid)

    async def update_leverage(self, coin: str, leverage: int, cross_margin: bool = True) -> dict:
        return await self._run(self.exchange.update_leverage, leverage, coin, cross_margin)

    async def market_close(self, coin: str, sz: float = None) -> dict:
        return await self._run(self.exchange.market_close, coin, sz=sz)

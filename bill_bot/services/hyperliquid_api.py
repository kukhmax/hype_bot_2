import asyncio
import time
import aiohttp
import logging

logger = logging.getLogger(__name__)


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

    async def spot_user_state(self, user_address: str) -> dict:
        url = f"{self.base_url}/info"
        payload = {
            "type": "spotClearinghouseState",
            "user": user_address
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Unexpected spot_user_state response")
                return data

    async def all_mids(self) -> dict:
        url = f"{self.base_url}/info"
        payload = {"type": "allMids"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, dict):
                    return {}
                return data

    async def open_orders(self, user_address: str) -> list[dict]:
        url = f"{self.base_url}/info"
        payload = {
            "type": "frontendOpenOrders",
            "user": user_address
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return data

    async def user_fills(self, user_address: str) -> list[dict]:
        url = f"{self.base_url}/info"
        payload = {
            "type": "userFills",
            "user": user_address
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return data

    async def get_real_trade_details(
        self,
        user_address: str,
        coin: str,
        opened_t: int,
        side: str,
        default_entry: float,
        default_exit: float,
        default_qty: float,
        taker_fee_rate: float = 0.000456,
        retries: int = 4,
        retry_delay_sec: float = 0.75,
    ) -> dict:
        """
        Получает реальные сделки (fills) пользователя и агрегирует их для расчета
        точных цен, объема, комиссий и PnL.
        """
        coin_upper = coin.upper()
        side_upper = side.upper()
        close_side_char = "S" if side_upper == "LONG" else "B"
        open_side_char = "B" if side_upper == "LONG" else "S"
        retries = max(0, int(retries))

        def _calc_gross(entry_px: float, exit_px: float, qty: float) -> float:
            if side_upper == "LONG":
                return (exit_px - entry_px) * qty
            return (entry_px - exit_px) * qty

        def _build_fallback(source: str) -> dict:
            est_qty = float(default_qty)
            est_entry = float(default_entry)
            est_exit = float(default_exit)
            est_fee = abs(est_entry * est_qty) * taker_fee_rate + abs(est_exit * est_qty) * taker_fee_rate
            est_gross = _calc_gross(est_entry, est_exit, est_qty)
            return {
                "entry": est_entry,
                "exit": est_exit,
                "qty": est_qty,
                "fee": est_fee,
                "pnl": est_gross - est_fee,
                "gross_pnl": est_gross,
                "success": False,
                "source": source,
            }

        for attempt in range(retries + 1):
            try:
                fills = await self.user_fills(user_address)
            except Exception as e:
                logger.error(f"Error fetching user fills for {coin_upper}: {e}")
                fills = []

            # Фильтруем сделки по монете и времени (с запасом 1 минута до opened_t)
            pair_fills = []
            for f in fills:
                f_coin = str(f.get("coin", "")).upper()
                f_time = int(f.get("time", 0))
                if f_coin == coin_upper and f_time >= (opened_t - 60000):
                    pair_fills.append(f)

            # Сортируем по времени (сначала новые)
            pair_fills.sort(key=lambda x: int(x.get("time", 0)), reverse=True)

            close_fills = []
            open_fills = []
            for f in pair_fills:
                f_side = str(f.get("side", "")).upper()
                if f_side == close_side_char:
                    close_fills.append(f)
                elif f_side == open_side_char:
                    open_fills.append(f)

            if close_fills:
                total_close_value = 0.0
                total_close_qty = 0.0
                real_close_fee = 0.0
                for f in close_fills:
                    px = float(f.get("px", 0.0))
                    sz = float(f.get("sz", 0.0))
                    fee = float(f.get("fee", 0.0))
                    total_close_value += px * sz
                    total_close_qty += sz
                    real_close_fee += fee

                real_exit = total_close_value / total_close_qty if total_close_qty > 0 else float(default_exit)
                real_qty = total_close_qty if total_close_qty > 0 else float(default_qty)

                real_open_fee = 0.0
                total_open_value = 0.0
                total_open_qty = 0.0
                if open_fills:
                    for f in open_fills:
                        px = float(f.get("px", 0.0))
                        sz = float(f.get("sz", 0.0))
                        fee = float(f.get("fee", 0.0))
                        total_open_value += px * sz
                        total_open_qty += sz
                        real_open_fee += fee
                    real_entry = total_open_value / total_open_qty if total_open_qty > 0 else float(default_entry)
                    logger.info(
                        "Matched %s open fills and %s close fills for %s.",
                        len(open_fills),
                        len(close_fills),
                        coin_upper,
                    )
                else:
                    real_entry = float(default_entry)
                    real_open_fee = abs(real_entry * real_qty) * taker_fee_rate
                    logger.info(
                        "Matched %s close fills for %s. Open fills not found, using fallback entry fee.",
                        len(close_fills),
                        coin_upper,
                    )

                total_fee = real_open_fee + real_close_fee
                gross_pnl = _calc_gross(real_entry, real_exit, real_qty)
                return {
                    "entry": real_entry,
                    "exit": real_exit,
                    "qty": real_qty,
                    "fee": total_fee,
                    "pnl": gross_pnl - total_fee,
                    "gross_pnl": gross_pnl,
                    "success": True,
                    "source": "exchange_fills",
                }

            if attempt < retries:
                logger.info(
                    "No close fills found for %s yet (attempt %s/%s). Retrying in %.2fs.",
                    coin_upper,
                    attempt + 1,
                    retries + 1,
                    retry_delay_sec,
                )
                await asyncio.sleep(float(retry_delay_sec))

        logger.warning(
            "No close fills found for %s since %s after %s attempts. Using estimated fallback.",
            coin_upper,
            opened_t,
            retries + 1,
        )
        return _build_fallback("estimated_fallback")


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
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"HyperliquidExchangeClient init failed: {e}")
            self.exchange = None

    async def _run(self, func_name: str, *args, **kwargs):
        if self.exchange is None:
            raise RuntimeError("Hyperliquid SDK is not initialized (exchange is None)")
        func = getattr(self.exchange, func_name, None)
        if func is None:
            raise AttributeError(f"Exchange object has no attribute '{func_name}'")
        
        res = await asyncio.to_thread(func, *args, **kwargs)
        if isinstance(res, dict):
            status = res.get("status")
            if status != "ok":
                logger.warning(f"HL API Warning ({func_name}): {res}")
            else:
                logger.info(f"HL API Response ({func_name}): {res}")
        return res

    async def place_order(self, coin: str, is_buy: bool, sz: float, limit_px: float, order_type: dict, reduce_only: bool = False) -> dict:
        # Принудительно приводим к нужным типам, чтобы избежать ошибок форматирования в SDK
        return await self._run("order", str(coin), bool(is_buy), float(sz), float(limit_px), order_type, reduce_only=bool(reduce_only))

    async def cancel_order(self, coin: str, oid: int) -> dict:
        return await self._run("cancel", coin, oid)

    async def cancel_by_cloid(self, coin: str, cloid: str) -> dict:
        return await self._run("cancel", coin, None, cloid)

    async def cancel_all_orders(self, coin: str, open_orders: list[dict]) -> None:
        if not open_orders: return
        # Отменяем ордера по одному для надежности и во избежание ошибок с аргументами
        for o in open_orders:
            o_coin = str(o.get("coin", "")).upper()
            if o_coin == coin or o_coin.startswith(f"{coin}-"):
                oid = int(o["oid"])
                try:
                    logger.info(f"HL: canceling order {oid} for {o_coin}")
                    await self._run("cancel", o_coin, oid)
                except Exception as e:
                    logger.error(f"HL: failed to cancel order {oid}: {e}")

    async def market_close(self, coin: str) -> dict:
        """Закрыть позицию по рынку (через SDK market_close)"""
        return await self._run("market_close", str(coin))

    async def update_leverage(self, coin: str, leverage: int, cross_margin: bool = True) -> dict:
        return await self._run("update_leverage", leverage, coin, cross_margin)

    async def market_close(self, coin: str, sz: float = None) -> dict:
        return await self._run("market_close", coin, sz=sz)

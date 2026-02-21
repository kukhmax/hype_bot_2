"""
WebSocket клиент для Hyperliquid.
Подписывается на candle-стримы и пушит свечи в Redis.
"""
import asyncio
import json
import logging
from typing import Callable, Coroutine, Any

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from config import config

logger = logging.getLogger(__name__)

# callback type: (user_id, token, tf, candle_dict) -> None
CandleCallback = Callable[[int, str, str, dict], Coroutine[Any, Any, None]]


class HyperliquidWSClient:
    """
    Один WS-клиент управляет ВСЕМИ подписками.
    Подписки хранятся как {(token, tf): set[user_id]}.
    """

    def __init__(self, on_candle: CandleCallback):
        self._on_candle = on_candle
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._subscriptions: dict[tuple[str, str], set[int]] = {}  # (token,tf) -> users
        self._running = False
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def subscribe(self, user_id: int, token: str, tf: str):
        async with self._lock:
            key = (token.upper(), tf)
            is_new = key not in self._subscriptions or not self._subscriptions[key]
            self._subscriptions.setdefault(key, set()).add(user_id)

            if is_new and self._ws and not self._ws.closed:
                await self._send_subscribe(token.upper(), tf)
                logger.info(f"Subscribed WS: {token}/{tf}")

    async def unsubscribe(self, user_id: int, token: str, tf: str):
        async with self._lock:
            key = (token.upper(), tf)
            if key in self._subscriptions:
                self._subscriptions[key].discard(user_id)
                if not self._subscriptions[key]:
                    del self._subscriptions[key]
                    if self._ws and not self._ws.closed:
                        await self._send_unsubscribe(token.upper(), tf)
                        logger.info(f"Unsubscribed WS: {token}/{tf}")

    async def run_forever(self):
        """Запустить клиент с авто-переподключением."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"WS error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _connect_and_listen(self):
        logger.info(f"Connecting to {config.HL_WS_URL}")
        async with websockets.connect(
            config.HL_WS_URL,
            ping_interval=None,
        ) as ws:
            self._ws = ws
            logger.info("WS connected")

            # Переподписываемся на все активные пары
            async with self._lock:
                for (token, tf) in list(self._subscriptions.keys()):
                    await self._send_subscribe(token, tf)

            async for raw in ws:
                await self._handle_message(raw)

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        channel = msg.get("channel", "")

        # Hyperliquid шлёт: {"channel": "candle", "data": {...}}
        if channel != "candle":
            return

        data = msg.get("data", {})
        # data example:
        # {"t": 1700000000000, "T": 1700000059999, "s": "ETH", "i": "5m",
        #  "o": "2000.0", "h": "2010.0", "l": "1995.0", "c": "2005.0",
        #  "v": "123.45", "n": 200}
        token = data.get("s", "").upper()
        tf = data.get("i", "")
        is_closed = data.get("x", False)  # true если свеча закрыта

        if not is_closed:
            return  # Ждём только закрытых свечей

        candle = {
            "t": data["t"],          # timestamp open ms
            "o": float(data["o"]),
            "h": float(data["h"]),
            "l": float(data["l"]),
            "c": float(data["c"]),
            "v": float(data["v"]),
        }

        # Раздаём всем подписанным пользователям
        key = (token, tf)
        async with self._lock:
            users = list(self._subscriptions.get(key, []))

        for user_id in users:
            try:
                await self._on_candle(user_id, token, tf, candle)
            except Exception as e:
                logger.error(f"on_candle error uid={user_id} {token}/{tf}: {e}")

    async def _send_subscribe(self, token: str, tf: str):
        payload = {
            "method": "subscribe",
            "subscription": {
                "type": "candle",
                "coin": token,
                "interval": tf,
            },
        }
        await self._ws.send(json.dumps(payload))

    async def _send_unsubscribe(self, token: str, tf: str):
        payload = {
            "method": "unsubscribe",
            "subscription": {
                "type": "candle",
                "coin": token,
                "interval": tf,
            },
        }
        await self._ws.send(json.dumps(payload))


async def fetch_historical_candles(token: str, tf: str, count: int = 200) -> list[dict]:
    """
    Загрузить исторические свечи через REST API Hyperliquid.
    Нужно для инициализации буфера при подписке.
    """
    import aiohttp
    import time

    end_ts = int(time.time() * 1000)

    # Определяем количество миллисекунд в одном периоде
    tf_ms = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "4h": 14_400_000,
    }
    period_ms = tf_ms.get(tf, 300_000)
    start_ts = end_ts - period_ms * count

    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": token.upper(),
            "interval": tf,
            "startTime": start_ts,
            "endTime": end_ts,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()

    candles = []
    for c in data:
        candles.append({
            "t": c["t"],
            "o": float(c["o"]),
            "h": float(c["h"]),
            "l": float(c["l"]),
            "c": float(c["c"]),
            "v": float(c["v"]),
        })

    return candles
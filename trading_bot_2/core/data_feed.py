"""
WebSocket data feed: MEXC / Binance / Bybit
Нормализует сообщения в объекты Candle и пишет в CandleBuffer.
"""
import asyncio
import json
import time
from typing import Callable, Awaitable, Optional
import aiohttp
import websockets

from core.candle_buffer import Candle, CandleBuffer
from config import config
from utils.logger import logger


# ─── Нормализаторы для каждой биржи ────────────────────────────────────────

def parse_mexc_futures(msg: dict) -> Optional[Candle]:
    """MEXC futures: channel 'push.kline'"""
    if msg.get("channel") != "push.kline":
        return None
    d = msg.get("data", {})
    k = d.get("kline", {})
    if not k:
        return None
    return Candle(
        timestamp=int(k.get("t", 0)) * 1000,
        open=float(k.get("o", 0)),
        high=float(k.get("h", 0)),
        low=float(k.get("l", 0)),
        close=float(k.get("c", 0)),
        volume=float(k.get("v", 0)),
        is_closed=bool(d.get("symbol")),  # MEXC не даёт явного флага — ставим True
    )


def parse_mexc_spot(msg: dict) -> Optional[Candle]:
    """MEXC spot: channel 'spot@public.kline.v3.api'"""
    if "d" not in msg:
        return None
    d = msg["d"]
    k = d.get("k", {})
    if not k:
        return None
    return Candle(
        timestamp=int(k.get("t", 0)),
        open=float(k.get("o", 0)),
        high=float(k.get("h", 0)),
        low=float(k.get("l", 0)),
        close=float(k.get("c", 0)),
        volume=float(k.get("v", 0)),
        is_closed=bool(k.get("x", False)),
    )


def parse_binance(msg: dict) -> Optional[Candle]:
    """Binance spot/futures: event 'kline'"""
    if msg.get("e") != "kline":
        return None
    k = msg["k"]
    return Candle(
        timestamp=int(k["t"]),
        open=float(k["o"]),
        high=float(k["h"]),
        low=float(k["l"]),
        close=float(k["c"]),
        volume=float(k["v"]),
        is_closed=bool(k["x"]),
    )


def parse_bybit(msg: dict) -> Optional[Candle]:
    """Bybit: topic 'kline.*'"""
    topic = msg.get("topic", "")
    if not topic.startswith("kline"):
        return None
    data = msg.get("data", [])
    if not data:
        return None
    k = data[0]
    return Candle(
        timestamp=int(k.get("start", 0)),
        open=float(k.get("open", 0)),
        high=float(k.get("high", 0)),
        low=float(k.get("low", 0)),
        close=float(k.get("close", 0)),
        volume=float(k.get("volume", 0)),
        is_closed=bool(k.get("confirm", False)),
    )


PARSERS = {
    ("mexc", "futures"): parse_mexc_futures,
    ("mexc", "spot"): parse_mexc_spot,
    ("binance", "spot"): parse_binance,
    ("binance", "futures"): parse_binance,
    ("bybit", "spot"): parse_bybit,
    ("bybit", "futures"): parse_bybit,
}


# ─── Subscription messages ───────────────────────────────────────────────────

def subscribe_msg(exchange: str, market: str, symbol: str, tf: str) -> dict | list:
    tf_map_mexc = {"1m": "Min1", "5m": "Min5", "15m": "Min15",
                   "1h": "Hour1", "4h": "Hour4", "1d": "Day1"}
    tf_map_bybit = {"1m": "1", "5m": "5", "15m": "15",
                    "1h": "60", "4h": "240", "1d": "D"}

    if exchange == "mexc" and market == "futures":
        return {"method": "sub.kline", "param": {"symbol": symbol, "interval": tf_map_mexc.get(tf, "Hour1")}}
    elif exchange == "mexc" and market == "spot":
        return {"method": "SUBSCRIPTION", "params": [f"spot@public.kline.v3.api@{symbol}@{tf}"]}
    elif exchange == "binance":
        stream = f"{symbol.lower()}@kline_{tf}"
        return {"method": "SUBSCRIBE", "params": [stream], "id": 1}
    elif exchange == "bybit":
        tf_b = tf_map_bybit.get(tf, "60")
        return {"op": "subscribe", "args": [f"kline.{tf_b}.{symbol}"]}
    return {}


# ─── Загрузка исторических свечей через REST ────────────────────────────────

async def fetch_historical(
    exchange: str,
    market: str,
    symbol: str,
    tf: str,
    limit: int = 200,
) -> list[Candle]:
    """Загружает исторические свечи для инициализации буфера."""
    url = config.REST_URLS[exchange][market]
    candles = []

    tf_map_bybit = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    tf_map_mexc_futures = {"1m": "Min1", "5m": "Min5", "15m": "Min15",
                           "1h": "Hour1", "4h": "Hour4", "1d": "Day1"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            if exchange == "binance":
                params = {"symbol": symbol, "interval": tf, "limit": limit}
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    for k in data:
                        candles.append(Candle(
                            timestamp=int(k[0]), open=float(k[1]),
                            high=float(k[2]), low=float(k[3]),
                            close=float(k[4]), volume=float(k[5]),
                        ))

            elif exchange == "mexc" and market == "spot":
                params = {"symbol": symbol, "interval": tf, "limit": limit}
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    for k in data:
                        candles.append(Candle(
                            timestamp=int(k[0]), open=float(k[1]),
                            high=float(k[2]), low=float(k[3]),
                            close=float(k[4]), volume=float(k[5]),
                        ))

            elif exchange == "mexc" and market == "futures":
                f_url = f"{url}/{symbol}"
                params = {"interval": tf_map_mexc_futures.get(tf, "Hour1"), "limit": limit}
                async with session.get(f_url, params=params) as resp:
                    data = await resp.json()
                    items = data.get("data", {})
                    if not items:
                        logger.error(f"[DataFeed] MEXC REST API пустой ответ: {data}")
                    ts_list = items.get("time", [])
                    opens = items.get("open", [])
                    highs = items.get("high", [])
                    lows = items.get("low", [])
                    closes = items.get("close", [])
                    vols = items.get("vol", [])
                    for i in range(len(ts_list)):
                        candles.append(Candle(
                            timestamp=int(ts_list[i]) * 1000,
                            open=float(opens[i]), high=float(highs[i]),
                            low=float(lows[i]), close=float(closes[i]),
                            volume=float(vols[i]),
                        ))

            elif exchange == "bybit":
                tf_b = tf_map_bybit.get(tf, "60")
                params = {"category": "linear" if market == "futures" else "spot",
                          "symbol": symbol, "interval": tf_b, "limit": limit}
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    for k in data.get("result", {}).get("list", [])[::-1]:
                        candles.append(Candle(
                            timestamp=int(k[0]), open=float(k[1]),
                            high=float(k[2]), low=float(k[3]),
                            close=float(k[4]), volume=float(k[5]),
                        ))

        except Exception as e:
            logger.error(f"[DataFeed] Ошибка загрузки истории: {e}")

    logger.info(f"[DataFeed] Загружено {len(candles)} исторических свечей")
    return candles


# ─── DataFeed ────────────────────────────────────────────────────────────────

class DataFeed:
    """
    Подключается по WebSocket, наполняет CandleBuffer,
    вызывает on_closed_candle при каждой закрытой свече.
    """

    def __init__(
        self,
        exchange: str,
        market: str,
        symbol: str,
        tf: str,
        buffer: CandleBuffer,
        on_closed_candle: Callable[[CandleBuffer], Awaitable[None]],
    ):
        self.exchange = exchange
        self.market = market
        self.symbol = symbol
        self.tf = tf
        self.buffer = buffer
        self.on_closed_candle = on_closed_candle
        self._running = False
        self._ws_url = config.WS_URLS[exchange][market]
        self._parser = PARSERS[(exchange, market)]

    async def start(self):
        self._running = True

        # Загружаем историю
        history = await fetch_historical(self.exchange, self.market, self.symbol, self.tf)
        for c in history:
            self.buffer.push(c)

        # WebSocket с авто-переподключением
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.warning(f"[DataFeed] WebSocket ошибка: {e}. Переподключение через 5с...")
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False

    async def _connect(self):
        logger.info(f"[DataFeed] Подключение к {self.exchange} {self.market} WS...")
        async with websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            # Подписываемся
            sub = subscribe_msg(self.exchange, self.market, self.symbol, self.tf)
            await ws.send(json.dumps(sub))
            logger.info(f"[DataFeed] Подписка: {self.symbol} {self.tf}")

            # Пинг-задача для MEXC (требует heartbeat)
            ping_task = asyncio.create_task(self._ping_loop(ws))

            try:
                async for raw in ws:
                    if not self._running:
                        break
                    msg = json.loads(raw)
                    candle = self._parser(msg)
                    if candle:
                        closed = self.buffer.push(candle)
                        if closed and self.buffer.ready(50):
                            await self.on_closed_candle(self.buffer)
            finally:
                ping_task.cancel()

    async def _ping_loop(self, ws):
        """Heartbeat для MEXC и Bybit"""
        while True:
            await asyncio.sleep(15)
            try:
                if self.exchange == "mexc":
                    await ws.send(json.dumps({"method": "ping"}))
                elif self.exchange == "bybit":
                    await ws.send(json.dumps({"op": "ping"}))
            except Exception:
                break

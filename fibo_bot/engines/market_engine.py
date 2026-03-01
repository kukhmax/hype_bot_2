"""
Fibo Bot — Market Engine.

WebSocket + REST подключение к MEXC (spot / futures).
Агрегация свечей, CandleBuffer, автоматическое переподключение.
"""

import asyncio
import json
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, List

import aiohttp
import numpy as np
import websockets

from config import config, TF_MAP_MEXC_FUTURES, TF_MAP_MEXC_SPOT, TF_SECONDS
from utils.logger import get_logger

logger = get_logger("market_engine")


# ─── Candle ──────────────────────────────────────────────────────────────────

@dataclass
class Candle:
    """Одна свеча OHLCV."""
    timestamp: int   # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def candle_range(self) -> float:
        return self.high - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open


# ─── CandleBuffer ────────────────────────────────────────────────────────────

class CandleBuffer:
    """
    Кольцевой буфер свечей.
    buffer[0] — текущая (последняя) свеча
    buffer[-N] — N свечей назад
    """

    def __init__(self, maxlen: int = 500):
        self._data: deque[Candle] = deque(maxlen=maxlen)
        self.maxlen = maxlen

    def push(self, candle: Candle) -> bool:
        """
        Добавить/обновить свечу.
        Возвращает True если свеча закрыта.
        """
        if self._data and self._data[-1].timestamp == candle.timestamp:
            self._data[-1] = candle  # обновляем незакрытую
            return candle.is_closed
        else:
            self._data.append(candle)
            return candle.is_closed

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, idx: int) -> Candle:
        """buffer[0] = последняя, buffer[-1] = предыдущая"""
        n = len(self._data)
        if idx == 0:
            return self._data[-1]
        elif idx < 0:
            real_idx = n + idx - 1
            if real_idx < 0:
                raise IndexError(f"Index {idx} out of range (buffer size={n})")
            return self._data[real_idx]
        else:
            raise IndexError("Используй buffer[0] для текущей, buffer[-N] для N назад")

    def ready(self, min_candles: int = 50) -> bool:
        return len(self._data) >= min_candles

    def to_list(self) -> List[Candle]:
        """Все свечи как список (от старых к новым)."""
        return list(self._data)

    # ─── Numpy arrays для индикаторов ────────────────────────────────────

    def _arr(self, field_name: str, n: Optional[int] = None) -> np.ndarray:
        data = list(self._data)
        if n:
            data = data[-n:]
        return np.array([getattr(c, field_name) for c in data], dtype=float)

    def opens(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("open", n)

    def highs(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("high", n)

    def lows(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("low", n)

    def closes(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("close", n)

    def volumes(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("volume", n)

    def timestamps(self, n: Optional[int] = None) -> np.ndarray:
        return self._arr("timestamp", n)


# ─── MEXC WebSocket парсеры ──────────────────────────────────────────────────

def parse_mexc_futures(msg: dict) -> Optional[Candle]:
    """Парсинг MEXC futures kline: channel 'push.kline'."""
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
        is_closed=True,  # MEXC futures не даёт явного флага
    )


def parse_mexc_spot(msg: dict) -> Optional[Candle]:
    """Парсинг MEXC spot kline: channel 'spot@public.kline.v3.api'."""
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


# ─── MEXC REST API ───────────────────────────────────────────────────────────

async def fetch_historical_candles(
    symbol: str,
    tf: str,
    market_type: str = "futures",
    limit: int = 300,
) -> List[Candle]:
    """Загрузка исторических свечей через MEXC REST API."""
    candles: List[Candle] = []
    start_time = time.time()

    headers = {"User-Agent": "Mozilla/5.0 (compatible; FiboBot/1.0)"}
    logger.info(f"[REST] Запрос исторических свечей: {symbol} {tf} limit={limit} market={market_type}")

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            if market_type == "futures":
                url = f"{config.exchange.rest_futures_url}/{symbol}"
                interval = TF_MAP_MEXC_FUTURES.get(tf, "Min5")
                params = {"interval": interval, "limit": limit}
                logger.debug(f"[REST] URL: {url} params: {params}")

                async with session.get(url, params=params) as resp:
                    elapsed = time.time() - start_time
                    logger.info(f"[REST] Ответ: status={resp.status} время={elapsed:.2f}с")

                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[REST] HTTP ошибка {resp.status}: {body[:500]}")
                        return candles

                    data = await resp.json()
                    items = data.get("data", {})
                    if not items:
                        logger.error(f"[REST] MEXC futures: пустой ответ data. Полный ответ: {str(data)[:500]}")
                        return candles

                    ts_list = items.get("time", [])
                    opens = items.get("open", [])
                    highs = items.get("high", [])
                    lows = items.get("low", [])
                    closes = items.get("close", [])
                    vols = items.get("vol", [])

                    for i in range(len(ts_list)):
                        candles.append(Candle(
                            timestamp=int(ts_list[i]) * 1000,
                            open=float(opens[i]),
                            high=float(highs[i]),
                            low=float(lows[i]),
                            close=float(closes[i]),
                            volume=float(vols[i]),
                        ))

            elif market_type == "spot":
                url = config.exchange.rest_spot_url
                params = {"symbol": symbol, "interval": tf, "limit": limit}
                logger.debug(f"[REST] URL: {url} params: {params}")

                async with session.get(url, params=params) as resp:
                    elapsed = time.time() - start_time
                    logger.info(f"[REST] Ответ: status={resp.status} время={elapsed:.2f}с")

                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[REST] HTTP ошибка {resp.status}: {body[:500]}")
                        return candles

                    data = await resp.json()
                    for k in data:
                        candles.append(Candle(
                            timestamp=int(k[0]),
                            open=float(k[1]),
                            high=float(k[2]),
                            low=float(k[3]),
                            close=float(k[4]),
                            volume=float(k[5]),
                        ))

        except aiohttp.ClientError as e:
            logger.error(f"[REST] Сетевая ошибка при загрузке свечей {symbol} {tf}: {e}")
        except Exception as e:
            logger.error(f"[REST] Непредвиденная ошибка загрузки свечей: {e}\n{traceback.format_exc()}")

    elapsed = time.time() - start_time
    if candles:
        first_c = candles[0]
        last_c = candles[-1]
        logger.info(
            f"[REST] ✅ Загружено {len(candles)} свечей ({symbol} {tf}) за {elapsed:.2f}с | "
            f"Диапазон: {first_c.close:.2f} → {last_c.close:.2f}"
        )
    else:
        logger.warning(f"[REST] ⚠ 0 свечей загружено для {symbol} {tf}")
    return candles


# ─── WebSocket подписка ──────────────────────────────────────────────────────

def build_subscribe_msg(symbol: str, tf: str, market_type: str = "futures") -> dict:
    """Формирует сообщение подписки для MEXC WebSocket."""
    if market_type == "futures":
        interval = TF_MAP_MEXC_FUTURES.get(tf, "Min5")
        return {
            "method": "sub.kline",
            "param": {"symbol": symbol, "interval": interval},
        }
    else:
        return {
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.kline.v3.api@{symbol}@{tf}"],
        }


# ─── CandleAggregator ───────────────────────────────────────────────────────

class CandleAggregator:
    """
    Агрегация 1m свечей в старшие таймфреймы (5m, 15m, 1h).
    """

    def __init__(self, target_tf: str):
        self.target_tf = target_tf
        self.interval_ms = TF_SECONDS[target_tf] * 1000
        self._current: Optional[Candle] = None

    def _slot(self, timestamp_ms: int) -> int:
        """Определяет начало интервала для данного timestamp."""
        return (timestamp_ms // self.interval_ms) * self.interval_ms

    def add(self, candle_1m: Candle) -> Optional[Candle]:
        """
        Добавить 1m свечу. Возвращает закрытую свечу старшего TF
        или None если интервал ещё не завершён.
        """
        slot = self._slot(candle_1m.timestamp)
        result = None

        if self._current is None:
            self._current = Candle(
                timestamp=slot,
                open=candle_1m.open,
                high=candle_1m.high,
                low=candle_1m.low,
                close=candle_1m.close,
                volume=candle_1m.volume,
                is_closed=False,
            )
        elif slot != self._current.timestamp:
            # Новый интервал — закрываем текущую
            self._current.is_closed = True
            result = self._current
            logger.debug(
                f"[Aggregator] Закрыта {self.target_tf} свеча: "
                f"O={result.open:.2f} H={result.high:.2f} L={result.low:.2f} "
                f"C={result.close:.2f} V={result.volume:.2f}"
            )
            self._current = Candle(
                timestamp=slot,
                open=candle_1m.open,
                high=candle_1m.high,
                low=candle_1m.low,
                close=candle_1m.close,
                volume=candle_1m.volume,
                is_closed=False,
            )
        else:
            # Обновляем текущую
            self._current.high = max(self._current.high, candle_1m.high)
            self._current.low = min(self._current.low, candle_1m.low)
            self._current.close = candle_1m.close
            self._current.volume += candle_1m.volume

        return result


# ─── MarketEngine ────────────────────────────────────────────────────────────

class MarketEngine:
    """
    Управляет WebSocket подключением к MEXC, буферами свечей
    и агрегацией таймфреймов.

    Использование:
        engine = MarketEngine(
            symbol="BTC_USDT",
            timeframes=["5m", "15m", "1h"],
            on_candle=my_callback,
        )
        await engine.start()
    """

    def __init__(
        self,
        symbol: str,
        timeframes: List[str],
        market_type: str = "futures",
        on_candle: Optional[Callable] = None,
        buffer_size: int = 500,
    ):
        self.symbol = symbol
        self.timeframes = timeframes
        self.market_type = market_type
        self.on_candle = on_candle
        self._running = False

        # WS URL
        if market_type == "futures":
            self._ws_url = config.exchange.ws_futures_url
            self._parser = parse_mexc_futures
        else:
            self._ws_url = config.exchange.ws_spot_url
            self._parser = parse_mexc_spot

        # Буферы для каждого таймфрейма
        self.buffers: dict[str, CandleBuffer] = {
            tf: CandleBuffer(maxlen=buffer_size) for tf in timeframes
        }

        # Агрегаторы (из 1m в старшие TF)
        self.aggregators: dict[str, CandleAggregator] = {}
        for tf in timeframes:
            if tf != "1m":
                self.aggregators[tf] = CandleAggregator(tf)

    async def start(self):
        """Запуск: загрузка истории + WebSocket стрим."""
        self._running = True
        self._reconnect_count = 0

        logger.info(f"[MarketEngine] 🚀 Запуск для {self.symbol} ({self.market_type})")
        logger.info(f"[MarketEngine] Таймфреймы: {self.timeframes}")
        logger.info(f"[MarketEngine] WS URL: {self._ws_url}")

        # Загружаем историю для каждого TF
        for tf in self.timeframes:
            logger.info(f"[MarketEngine] Загрузка истории {self.symbol} {tf}...")
            history = await fetch_historical_candles(
                symbol=self.symbol,
                tf=tf,
                market_type=self.market_type,
                limit=300,
            )
            for c in history:
                self.buffers[tf].push(c)
            buf_len = len(self.buffers[tf])
            ready = "✅ готов" if self.buffers[tf].ready(50) else "⚠ недостаточно данных"
            logger.info(f"[MarketEngine] Буфер {tf}: {buf_len} свечей — {ready}")

        logger.info(f"[MarketEngine] ✅ История загружена. Переход к WebSocket стриму.")

        # WebSocket с автоматическим переподключением
        while self._running:
            try:
                await self._ws_loop()
            except Exception as e:
                self._reconnect_count += 1
                logger.warning(
                    f"[MarketEngine] ⚠ WebSocket ошибка (попытка #{self._reconnect_count}): {e}. "
                    f"Переподключение через 5с..."
                )
                logger.debug(f"[MarketEngine] Traceback: {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def stop(self):
        """Остановка."""
        self._running = False
        logger.info(f"[MarketEngine] 🛑 Остановлен ({self.symbol}). Переподключений: {getattr(self, '_reconnect_count', 0)}")

    async def _ws_loop(self):
        """Основной WebSocket цикл."""
        connect_time = time.time()
        logger.info(f"[WS] 🔌 Подключение к MEXC {self.market_type} WS ({self.symbol})...")

        async with websockets.connect(
            self._ws_url,
            ping_interval=None,
        ) as ws:
            elapsed = time.time() - connect_time
            logger.info(f"[WS] ✅ Подключено к {self._ws_url} за {elapsed:.2f}с")

            # Подписываемся на 1m kline (базовый TF)
            sub = build_subscribe_msg(self.symbol, "1m", self.market_type)
            await ws.send(json.dumps(sub))
            logger.info(f"[WS] 📡 Подписка отправлена: {self.symbol} 1m kline")

            # Подписываемся на конкретные TF
            for tf in self.timeframes:
                if tf != "1m":
                    sub_tf = build_subscribe_msg(self.symbol, tf, self.market_type)
                    await ws.send(json.dumps(sub_tf))
                    logger.info(f"[WS] 📡 Подписка отправлена: {self.symbol} {tf} kline")

            # Пинг-задача
            ping_task = asyncio.create_task(self._ping_loop(ws))
            logger.debug(f"[WS] Пинг-задача запущена (интервал: 15с)")

            try:
                msg_count = 0
                candle_count = 0
                last_report = time.time()

                async for raw in ws:
                    if not self._running:
                        logger.info(f"[WS] Остановка WS потока ({self.symbol})")
                        break

                    msg_count += 1
                    msg = json.loads(raw)
                    candle = self._parser(msg)
                    if not candle:
                        continue

                    candle_count += 1

                    # Периодический отчёт каждые 60 секунд
                    now = time.time()
                    if now - last_report >= 60:
                        buf_info = {tf: len(b) for tf, b in self.buffers.items()}
                        logger.info(
                            f"[WS] 📊 {self.symbol} | Сообщений: {msg_count} | "
                            f"Свечей: {candle_count} | Буферы: {buf_info} | "
                            f"Текущая цена: {candle.close:.2f}"
                        )
                        last_report = now

                    # Пушим в буфер 1m
                    if "1m" in self.buffers:
                        self.buffers["1m"].push(candle)

                    # Агрегация в старшие TF
                    for tf, agg in self.aggregators.items():
                        closed = agg.add(candle)
                        if closed:
                            self.buffers[tf].push(closed)
                            logger.info(
                                f"[WS] 🕯 Свеча закрыта {self.symbol} {tf}: "
                                f"O={closed.open:.2f} H={closed.high:.2f} "
                                f"L={closed.low:.2f} C={closed.close:.2f} V={closed.volume:.0f}"
                            )
                            # Коллбэк при закрытии свечи
                            if self.on_candle and self.buffers[tf].ready(50):
                                logger.debug(f"[WS] Вызов on_candle для {self.symbol} {tf}")
                                await self.on_candle(
                                    symbol=self.symbol,
                                    timeframe=tf,
                                    buffer=self.buffers[tf],
                                )

            except websockets.exceptions.ConnectionClosed as e:
                duration = time.time() - connect_time
                logger.warning(
                    f"[WS] ⚠ Соединение закрыто после {duration:.0f}с: "
                    f"code={e.code if hasattr(e, 'code') else '?'} reason={e}"
                )
                raise
            finally:
                ping_task.cancel()
                logger.debug(f"[WS] Пинг-задача остановлена")

    async def _ping_loop(self, ws):
        """Heartbeat для MEXC WebSocket."""
        ping_count = 0
        while True:
            await asyncio.sleep(15)
            try:
                await ws.send(json.dumps({"method": "ping"}))
                ping_count += 1
                if ping_count % 20 == 0:  # Каждые 5 минут
                    logger.debug(f"[WS] 💓 Ping #{ping_count} отправлен")
            except Exception as e:
                logger.warning(f"[WS] Ошибка отправки ping: {e}")
                break

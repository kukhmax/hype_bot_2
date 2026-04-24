import asyncio
import logging
import time

from bill_bot.core.config import Config
from bill_bot.core.logger import setup_logger
from bill_bot.core.redis_client import get_redis
from bill_bot.services.candle_store import Candle, RedisCandleStore
from bill_bot.services.hyperliquid_api import HyperliquidInfoClient


async def main():
    cfg = Config.from_env()
    logger = setup_logger(cfg.log_level)

    r = get_redis(cfg)
    try:
        pong = await r.ping()
        logger.info("Redis ping=%s host=%s port=%s", pong, cfg.redis_host, cfg.redis_port)
    except Exception as e:
        logger.error("Redis connection failed: %s", e)
        raise

    store = RedisCandleStore(r)
    hl = HyperliquidInfoClient()

    if cfg.pairs:
        tf_ms = 15 * 60 * 1000 if cfg.timeframe == "15m" else None
        if tf_ms is None:
            raise ValueError("Only 15m timeframe supported in this step")

        now = int(time.time() * 1000)
        start = now - tf_ms * max(cfg.history_bars * 3, 300)
        for pair in cfg.pairs:
            raw = await hl.candle_snapshot(pair, cfg.timeframe, start, now)
            candles = [Candle.from_hl(x) for x in raw]
            candles.sort(key=lambda c: c.t)
            closed = [c for c in candles if c.T <= now]
            window = closed[-cfg.history_bars:]
            await store.set_window(pair, cfg.timeframe, window)
            logger.info(
                "History loaded: pair=%s tf=%s bars=%s range_t=%s..%s",
                pair,
                cfg.timeframe,
                len(window),
                window[0].t if window else None,
                window[-1].t if window else None,
            )

        logger.info("History bootstrap done. Starting polling for closed candles...")

        max_len = max(cfg.history_bars, 200)
        while True:
            now = int(time.time() * 1000)
            start = now - tf_ms * 10
            for pair in cfg.pairs:
                raw = await hl.candle_snapshot(pair, cfg.timeframe, start, now)
                candles = [Candle.from_hl(x) for x in raw]
                candles.sort(key=lambda c: c.t)
                closed = [c for c in candles if c.T <= now]
                if not closed:
                    continue
                new_candle = closed[-1]
                appended = await store.append_if_new(pair, cfg.timeframe, new_candle, max_len=max_len)
                if appended:
                    logger.info("New closed candle: pair=%s tf=%s t=%s o=%.4f c=%.4f", pair, cfg.timeframe, new_candle.t, new_candle.o, new_candle.c)
            await asyncio.sleep(cfg.poll_seconds)

    logger.info("Bill Bot bootstrap started (dry-run). No PAIRS configured. Waiting...")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    asyncio.run(main())

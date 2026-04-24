import asyncio
import logging
import json
import time

from bill_bot.core.config import Config
from bill_bot.core.logger import setup_logger
from bill_bot.core.redis_client import get_redis
from bill_bot.services.candle_store import Candle, RedisCandleStore
from bill_bot.services.fractals import RedisFractalStore, detect_confirmed_fractal
from bill_bot.services.hyperliquid_api import HyperliquidInfoClient
from bill_bot.services.indicators import alligator_ema, spread_lines, is_sleep


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
    fractals_store = RedisFractalStore(r)
    hl = HyperliquidInfoClient()

    async def update_fractals(pair: str):
        window = await store.get_window(pair, cfg.timeframe)
        if len(window) < 5:
            return
        closes = [c.c for c in window]
        alli = alligator_ema(closes)
        teeth = alli["teeth"]
        center_idx = len(window) - 3
        found = detect_confirmed_fractal(window, teeth_series=teeth, center_idx=center_idx)
        if not found:
            return
        added = await fractals_store.append_new(pair, cfg.timeframe, found, max_len=cfg.fractals_max)
        if added:
            for f in found:
                logger.info(
                    "Fractal confirmed: pair=%s tf=%s kind=%s t=%s price=%.4f close=%.4f teeth=%.4f",
                    pair,
                    cfg.timeframe,
                    f.kind,
                    f.t,
                    f.price,
                    f.close,
                    f.teeth,
                )

    async def update_indicators(pair: str):
        window = await store.get_window(pair, cfg.timeframe)
        if not window:
            return
        closes = [c.c for c in window]
        alli = alligator_ema(closes)
        spreads = spread_lines(alli["jaw"], alli["teeth"], alli["lips"])
        sleep, med_spread = is_sleep(spreads, last_close=closes[-1], window=cfg.sleep_window, k=cfg.sleep_k)

        payload = {
            "pair": pair,
            "tf": cfg.timeframe,
            "t": window[-1].t,
            "close": closes[-1],
            "jaw": alli["jaw"][-1],
            "teeth": alli["teeth"][-1],
            "lips": alli["lips"][-1],
            "spread": spreads[-1] if spreads else 0.0,
            "sleep": sleep,
            "sleep_med_spread": med_spread,
            "sleep_k": cfg.sleep_k,
            "sleep_window": cfg.sleep_window,
        }
        await r.set(f"ind:last:{pair}:{cfg.timeframe}", json.dumps(payload, separators=(",", ":")))
        logger.info(
            "Alligator: pair=%s tf=%s close=%.4f jaw=%.4f teeth=%.4f lips=%.4f sleep=%s med_spread=%.6f",
            pair,
            cfg.timeframe,
            closes[-1],
            payload["jaw"],
            payload["teeth"],
            payload["lips"],
            sleep,
            med_spread,
        )

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
            if window:
                await update_indicators(pair)
                await update_fractals(pair)

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
                    await update_indicators(pair)
                    await update_fractals(pair)
            await asyncio.sleep(cfg.poll_seconds)

    logger.info("Bill Bot bootstrap started (dry-run). No PAIRS configured. Waiting...")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    asyncio.run(main())

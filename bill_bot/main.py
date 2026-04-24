import asyncio
import logging
import json
import time

from bill_bot.core.config import Config
from bill_bot.core.logger import setup_logger
from bill_bot.core.redis_client import get_redis
from bill_bot.services.candle_store import Candle, RedisCandleStore
from bill_bot.services.execution import ExecutionDryRun, RedisTradeState
from bill_bot.services.fractals import RedisFractalStore, detect_confirmed_fractal
from bill_bot.services.hyperliquid_api import HyperliquidInfoClient
from bill_bot.services.indicators import alligator_ema, spread_lines, is_sleep
from bill_bot.services.strategy import RedisSignalStore, StrategyEngine
from bill_bot.services.subscriptions import SubscriptionStore
from bill_bot.services.telegram_bot import run_telegram


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
    signal_store = RedisSignalStore(r)
    trade_state = RedisTradeState(r)
    exec_engine = ExecutionDryRun(trade_state, tf=cfg.timeframe, virtual_equity=cfg.virtual_equity)
    hl = HyperliquidInfoClient()
    subs = SubscriptionStore(r, tf=cfg.timeframe)

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

    async def update_signal_candidate(pair: str):
        window = await store.get_window(pair, cfg.timeframe)
        fr = await fractals_store.get_all(pair, cfg.timeframe)
        sz_dec = await hl.get_sz_decimals(pair)
        engine = StrategyEngine(
            tf=cfg.timeframe,
            sleep_window=cfg.sleep_window,
            sleep_k=cfg.sleep_k,
            rr=1.5,
            sz_decimals=sz_dec,
            max_decimals=6,
            tick_size_fallback=cfg.tick_size_default,
        )
        cand, ctx = engine.evaluate(pair=pair, candles=window, fractals=fr)
        if not cand:
            return
        prev = await signal_store.get_last(pair, cfg.timeframe)
        if prev and prev.get("side") == cand.side and int(prev.get("cluster_t", 0)) == cand.cluster_t:
            return
        await signal_store.set_last(cand)
        tick = engine.tick_size_for_price(cand.cluster_price)
        logger.info(
            "Signal candidate: pair=%s tf=%s side=%s cluster_t=%s entry=%.4f sl=%.4f tp=%.4f tick=%.8f sleep_med=%.6f",
            pair,
            cfg.timeframe,
            cand.side,
            cand.cluster_t,
            cand.entry_trigger,
            cand.stop_loss,
            cand.take_profit,
            tick,
            float(ctx.get("sleep_med_spread", 0.0)),
        )
        users = await subs.get_pair_users(pair)
        for uid in users:
            if not await subs.is_active(uid):
                continue
            ucfg = await subs.get_user_cfg(uid)
            risk_pct = float(ucfg.get("risk_pct", cfg.default_risk_pct))
            order = await exec_engine.maybe_place_order(
                user_id=uid,
                pair=pair,
                candle=window[-1],
                cand=cand,
                risk_pct=risk_pct,
            )
            if order:
                logger.info(
                    "Dry-run order placed: user=%s pair=%s tf=%s side=%s trigger=%.4f sl=%.4f tp=%.4f qty=%.6f risk=%.2f%%",
                    uid,
                    pair,
                    cfg.timeframe,
                    order.side,
                    order.trigger,
                    order.stop_loss,
                    order.take_profit,
                    order.qty,
                    risk_pct,
                )

    async def market_loop():
        tf_ms = 15 * 60 * 1000 if cfg.timeframe == "15m" else None
        if tf_ms is None:
            raise ValueError("Only 15m timeframe supported in this step")

        max_len = max(cfg.history_bars, 200)
        while True:
            active_pairs = await subs.get_active_pairs()
            pairs_to_use = active_pairs or cfg.pairs
            if not pairs_to_use:
                await asyncio.sleep(2)
                continue

            now = int(time.time() * 1000)
            start = now - tf_ms * max(cfg.history_bars * 3, 300)
            for pair in pairs_to_use:
                existing = await store.get_window(pair, cfg.timeframe)
                if len(existing) < cfg.history_bars:
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
                        await update_signal_candidate(pair)
                    continue

                start_small = now - tf_ms * 10
                raw = await hl.candle_snapshot(pair, cfg.timeframe, start_small, now)
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
                    await update_signal_candidate(pair)
                    users = await subs.get_pair_users(pair)
                    for uid in users:
                        if not await subs.is_active(uid):
                            continue
                        evt = await exec_engine.on_candle(user_id=uid, pair=pair, candle=new_candle)
                        if evt.get("changed"):
                            logger.info("Dry-run event: user=%s pair=%s tf=%s %s", uid, pair, cfg.timeframe, evt)
            await asyncio.sleep(cfg.poll_seconds)

    tasks = [asyncio.create_task(market_loop())]
    if cfg.telegram_token:
        tasks.append(asyncio.create_task(run_telegram(cfg, subs, trade_state)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    asyncio.run(main())

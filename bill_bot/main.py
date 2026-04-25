import asyncio
from datetime import datetime, timezone
import logging
import json
import time

from aiogram import Bot
from aiogram.types.input_file import BufferedInputFile

from bill_bot.core.config import Config
from bill_bot.core.logger import setup_logger
from bill_bot.core.redis_client import get_redis
from bill_bot.services.candle_store import Candle, RedisCandleStore
from bill_bot.services.charting import FractalPoint, PriceLevel, build_chart_png
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

    tg_bot = Bot(token=cfg.telegram_token) if cfg.telegram_token else None

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
    hl = HyperliquidInfoClient()
    subs = SubscriptionStore(r, tf=cfg.timeframe)

    def fmt_ts_ms(ts_ms: int) -> str:
        try:
            ts_ms = int(ts_ms)
        except Exception:
            return str(ts_ms)
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        except Exception:
            return str(ts_ms)
        return dt.strftime("%H:%M %d/%m/%y")

    def display_pair(coin: str) -> str:
        return f"{str(coin).upper().strip()}-USDC"

    async def tg_send(user_id: int, text: str) -> None:
        if tg_bot is None:
            return
        try:
            await tg_bot.send_message(chat_id=int(user_id), text=text)
        except Exception as e:
            logger.info("Telegram send failed: user=%s err=%s", user_id, e)

    async def tg_send_photo(user_id: int, png: bytes, caption: str) -> None:
        if tg_bot is None:
            return
        try:
            await tg_bot.send_photo(
                chat_id=int(user_id),
                photo=BufferedInputFile(png, filename="signal.png"),
                caption=caption,
            )
        except Exception as e:
            logger.info("Telegram send_photo failed: user=%s err=%s", user_id, e)

    def calc_fractal_points(candles: list[Candle], teeth_series: list[float]) -> list[FractalPoint]:
        out: list[FractalPoint] = []
        for i in range(2, max(2, len(candles) - 2)):
            c = candles[i]
            l2 = candles[i - 2]
            l1 = candles[i - 1]
            r1 = candles[i + 1]
            r2 = candles[i + 2]

            if c.h > l2.h and c.h > l1.h and c.h > r1.h and c.h > r2.h:
                out.append(FractalPoint(kind="HIGH", t=c.t, price=c.h, idx=i))
            if c.l < l2.l and c.l < l1.l and c.l < r1.l and c.l < r2.l:
                out.append(FractalPoint(kind="LOW", t=c.t, price=c.l, idx=i))
        out.sort(key=lambda x: x.t)
        return out

    async def build_signal_chart(pair: str, tf: str, levels: list[PriceLevel]) -> bytes | None:
        candles = await store.get_window(pair, tf)
        if len(candles) < 5:
            return None
        closes = [c.c for c in candles]
        alli = alligator_ema(closes)
        fpts = calc_fractal_points(candles, teeth_series=alli["teeth"])
        try:
            return build_chart_png(pair, tf, candles, alli["jaw"], alli["teeth"], alli["lips"], fpts, levels=levels)
        except Exception as e:
            logger.info("Chart build failed: pair=%s tf=%s err=%s", pair, tf, e)
            return None

    async def user_balance(subs_tf: SubscriptionStore, user_id: int, tf: str) -> tuple[float, float, float, float]:
        pairs = await subs_tf.get_user_pairs(user_id)
        total_u = 0.0
        total_r = 0.0
        for p in pairs:
            pnl = await trade_state.get_pnl(user_id, p, tf)
            try:
                total_u += float(pnl.get("unrealized", 0.0))
            except Exception:
                pass
            try:
                total_r += float(pnl.get("realized", 0.0))
            except Exception:
                pass
        balance = float(cfg.virtual_equity) + total_r + total_u
        return balance, total_r, total_u, float(cfg.virtual_equity)

    def timeframe_ms(tf: str) -> int:
        tf = str(tf).strip()
        if tf.endswith("m"):
            n = tf[:-1]
            try:
                minutes = int(n)
            except Exception:
                raise ValueError(f"Unsupported timeframe: {tf}")
            if minutes <= 0:
                raise ValueError(f"Unsupported timeframe: {tf}")
            return minutes * 60_000
        if tf.endswith("h"):
            n = tf[:-1]
            try:
                hours = int(n)
            except Exception:
                raise ValueError(f"Unsupported timeframe: {tf}")
            if hours <= 0:
                raise ValueError(f"Unsupported timeframe: {tf}")
            return hours * 60 * 60_000
        raise ValueError(f"Unsupported timeframe: {tf}")

    async def update_fractals(pair: str, tf: str):
        window = await store.get_window(pair, tf)
        if len(window) < 5:
            return
        closes = [c.c for c in window]
        alli = alligator_ema(closes)
        teeth = alli["teeth"]
        center_idx = len(window) - 3
        found = detect_confirmed_fractal(window, teeth_series=teeth, center_idx=center_idx)
        if not found:
            return
        added = await fractals_store.append_new(pair, tf, found, max_len=cfg.fractals_max)
        if added:
            for f in found:
                logger.info(
                    "Fractal confirmed: pair=%s tf=%s kind=%s t=%s price=%.4f close=%.4f teeth=%.4f",
                    pair,
                    tf,
                    f.kind,
                    f.t,
                    f.price,
                    f.close,
                    f.teeth,
                )

    async def update_indicators(pair: str, tf: str):
        window = await store.get_window(pair, tf)
        if not window:
            return
        closes = [c.c for c in window]
        alli = alligator_ema(closes)
        spreads = spread_lines(alli["jaw"], alli["teeth"], alli["lips"])
        sleep, med_spread = is_sleep(spreads, last_close=closes[-1], window=cfg.sleep_window, k=cfg.sleep_k)

        payload = {
            "pair": pair,
            "tf": tf,
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
        await r.set(f"ind:last:{pair}:{tf}", json.dumps(payload, separators=(",", ":")))
        logger.info(
            "Alligator: pair=%s tf=%s close=%.4f jaw=%.4f teeth=%.4f lips=%.4f sleep=%s med_spread=%.6f",
            pair,
            tf,
            closes[-1],
            payload["jaw"],
            payload["teeth"],
            payload["lips"],
            sleep,
            med_spread,
        )

    async def update_signal_candidate(pair: str, tf: str, subs_tf: SubscriptionStore, exec_engine: ExecutionDryRun):
        window = await store.get_window(pair, tf)
        fr = await fractals_store.get_all(pair, tf)
        sz_dec = await hl.get_sz_decimals(pair)
        engine = StrategyEngine(
            tf=tf,
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
        prev = await signal_store.get_last(pair, tf)
        if prev and prev.get("side") == cand.side and int(prev.get("cluster_t", 0)) == cand.cluster_t:
            return
        await signal_store.set_last(cand)
        tick = engine.tick_size_for_price(cand.cluster_price)
        logger.info(
            "Signal candidate: pair=%s tf=%s side=%s cluster_t=%s entry=%.4f sl=%.4f tp=%.4f tick=%.8f sleep_med=%.6f",
            pair,
            tf,
            cand.side,
            cand.cluster_t,
            cand.entry_trigger,
            cand.stop_loss,
            cand.take_profit,
            tick,
            float(ctx.get("sleep_med_spread", 0.0)),
        )
        users = await subs_tf.get_pair_users(pair)
        levels_setup = [
            PriceLevel(price=cand.cluster_price, label="Cluster", color="#7c3aed", linestyle=":", linewidth=1.0),
            PriceLevel(price=cand.entry_trigger, label="Trigger", color="#0ea5e9", linestyle="--", linewidth=1.2),
            PriceLevel(price=cand.stop_loss, label="SL", color="#ef4444", linestyle="-", linewidth=1.0),
            PriceLevel(price=cand.take_profit, label="TP", color="#22c55e", linestyle="-", linewidth=1.0),
        ]
        setup_png = await build_signal_chart(pair, tf, levels=levels_setup)
        for uid in users:
            if not await subs_tf.is_active(uid):
                continue
            ucfg = await subs_tf.get_user_cfg(uid)
            risk_pct = float(ucfg.get("risk_pct", cfg.default_risk_pct))

            state = await trade_state.get(uid, pair, tf)
            if state.get("pos") or state.get("ord"):
                note = "ℹ️ Уже есть позиция/ордер, новый ордер не выставлен"
            else:
                note = ""

            order = await exec_engine.maybe_place_order(
                user_id=uid,
                pair=pair,
                candle=window[-1],
                cand=cand,
                risk_pct=risk_pct,
            )
            qty = float(order.qty) if order else 0.0
            setup_caption = (
                f"� Сетап {cand.side} {display_pair(pair)} ({tf})\n"
                f"🕒 {fmt_ts_ms(cand.cluster_t)}\n"
                f"🎯 Trigger {cand.entry_trigger:.4f} | 🛑 SL {cand.stop_loss:.4f} | ✅ TP {cand.take_profit:.4f}\n"
                f"� Qty {qty:.6f} | ⚙️ Risk {risk_pct:.2f}%"
            )
            if note:
                setup_caption = f"{setup_caption}\n{note}"
            if setup_png:
                await tg_send_photo(uid, setup_png, setup_caption)
            else:
                await tg_send(uid, setup_caption)
            if order:
                logger.info(
                    "Dry-run order placed: user=%s pair=%s tf=%s side=%s trigger=%.4f sl=%.4f tp=%.4f qty=%.6f risk=%.2f%%",
                    uid,
                    pair,
                    tf,
                    order.side,
                    order.trigger,
                    order.stop_loss,
                    order.take_profit,
                    order.qty,
                    risk_pct,
                )

    async def market_loop(tf: str):
        tf_ms = timeframe_ms(tf)
        subs_tf = SubscriptionStore(r, tf=tf)
        exec_engine = ExecutionDryRun(trade_state, tf=tf, virtual_equity=cfg.virtual_equity)

        max_len = max(cfg.history_bars, 200)
        while True:
            active_pairs = await subs_tf.get_active_pairs()
            if active_pairs:
                pairs_to_use = active_pairs
            elif tf == cfg.timeframe:
                pairs_to_use = cfg.pairs
            else:
                pairs_to_use = []
            if not pairs_to_use:
                await asyncio.sleep(2)
                continue

            now = int(time.time() * 1000)
            start = now - tf_ms * max(cfg.history_bars * 3, 300)
            for pair in pairs_to_use:
                try:
                    existing = await store.get_window(pair, tf)
                    if len(existing) < cfg.history_bars:
                        raw = await hl.candle_snapshot(pair, tf, start, now)
                        candles = [Candle.from_hl(x) for x in raw]
                        candles.sort(key=lambda c: c.t)
                        closed = [c for c in candles if c.T <= now]
                        window = closed[-cfg.history_bars:]
                        await store.set_window(pair, tf, window)
                        logger.info(
                            "History loaded: pair=%s tf=%s bars=%s range_t=%s..%s",
                            pair,
                            tf,
                            len(window),
                            window[0].t if window else None,
                            window[-1].t if window else None,
                        )
                        if window:
                            await update_indicators(pair, tf)
                            await update_fractals(pair, tf)
                            await update_signal_candidate(pair, tf, subs_tf, exec_engine)
                        continue

                    start_small = now - tf_ms * 10
                    raw = await hl.candle_snapshot(pair, tf, start_small, now)
                    candles = [Candle.from_hl(x) for x in raw]
                    candles.sort(key=lambda c: c.t)
                    closed = [c for c in candles if c.T <= now]
                    if not closed:
                        continue
                    new_candle = closed[-1]
                    appended = await store.append_if_new(pair, tf, new_candle, max_len=max_len)
                    if appended:
                        logger.info("New closed candle: pair=%s tf=%s t=%s o=%.4f c=%.4f", pair, tf, new_candle.t, new_candle.o, new_candle.c)
                        await update_indicators(pair, tf)
                        await update_fractals(pair, tf)
                        await update_signal_candidate(pair, tf, subs_tf, exec_engine)
                        users = await subs_tf.get_pair_users(pair)
                        for uid in users:
                            if not await subs_tf.is_active(uid):
                                continue
                            evt = await exec_engine.on_candle(user_id=uid, pair=pair, candle=new_candle)
                            if evt.get("changed"):
                                logger.info("Dry-run event: user=%s pair=%s tf=%s %s", uid, pair, tf, evt)
                                if evt.get("event") == "position_opened":
                                    entry = float(evt.get("entry", 0.0))
                                    sl = float(evt.get("stop_loss", 0.0))
                                    tp = float(evt.get("take_profit", 0.0))
                                    qty = float(evt.get("qty", 0.0))
                                    opened_t = int(evt.get("opened_t", new_candle.t))
                                    levels_open = [
                                        PriceLevel(price=entry, label="Entry", color="#0ea5e9", linestyle="-", linewidth=1.2),
                                        PriceLevel(price=sl, label="SL", color="#ef4444", linestyle="-", linewidth=1.0),
                                        PriceLevel(price=tp, label="TP", color="#22c55e", linestyle="-", linewidth=1.0),
                                    ]
                                    opened_png = await build_signal_chart(pair, tf, levels=levels_open)
                                    opened_caption = (
                                        f"🚀 Открыта {str(evt.get('side', '')).upper()} {display_pair(pair)} ({tf})\n"
                                        f"🕒 {fmt_ts_ms(opened_t)}\n"
                                        f"🎯 Entry {entry:.4f} | 🛑 SL {sl:.4f} | ✅ TP {tp:.4f}\n"
                                        f"� Qty {qty:.6f}"
                                    )
                                    if opened_png:
                                        await tg_send_photo(uid, opened_png, opened_caption)
                                    else:
                                        await tg_send(uid, opened_caption)
                                elif evt.get("event") == "position_closed":
                                    balance, total_r, total_u, base = await user_balance(subs_tf, uid, tf)
                                    pnl = float(evt.get("pnl", 0.0))
                                    pnl_emoji = "🟩" if pnl >= 0 else "🟥"
                                    reason_u = str(evt.get("reason", "")).upper()
                                    if reason_u == "TP":
                                        reason_txt = "✅ TP"
                                    elif reason_u == "SL":
                                        reason_txt = "🛑 SL"
                                    elif "SL_AND_TP" in reason_u:
                                        reason_txt = "⚠️ SL/TP (в одной свече)"
                                    else:
                                        reason_txt = str(evt.get("reason", ""))
                                    entry = float(evt.get("entry", 0.0))
                                    exit_px = float(evt.get("exit", 0.0))
                                    sl = float(evt.get("stop_loss", 0.0))
                                    tp = float(evt.get("take_profit", 0.0))
                                    qty = float(evt.get("qty", 0.0))
                                    closed_t = int(evt.get("closed_t", new_candle.t))
                                    levels_close = [
                                        PriceLevel(price=entry, label="Entry", color="#0ea5e9", linestyle="-", linewidth=1.2),
                                        PriceLevel(price=exit_px, label="Exit", color="#f59e0b", linestyle="--", linewidth=1.2),
                                        PriceLevel(price=sl, label="SL", color="#ef4444", linestyle="-", linewidth=1.0),
                                        PriceLevel(price=tp, label="TP", color="#22c55e", linestyle="-", linewidth=1.0),
                                    ]
                                    closed_png = await build_signal_chart(pair, tf, levels=levels_close)
                                    closed_caption = (
                                        f"🏁 Закрыта {str(evt.get('side', '')).upper()} {display_pair(pair)} ({tf})\n"
                                        f"� {fmt_ts_ms(closed_t)} | {reason_txt}\n"
                                        f"🎯 Entry {entry:.4f} → Exit {exit_px:.4f} | {pnl_emoji} PnL {pnl:+.2f}\n"
                                        f"� Баланс {balance:.2f} | 💰 R {total_r:.2f} | 📈 U {total_u:.2f}"
                                    )
                                    if closed_png:
                                        await tg_send_photo(uid, closed_png, closed_caption)
                                    else:
                                        await tg_send(uid, closed_caption)
                except Exception as e:
                    logger.error("Market loop error: pair=%s tf=%s err=%s", pair, tf, e)
            await asyncio.sleep(cfg.poll_seconds)

    timeframes = list(dict.fromkeys([*cfg.timeframes_available, cfg.timeframe]))
    tasks = [asyncio.create_task(market_loop(tf)) for tf in timeframes]
    if cfg.telegram_token:
        tasks.append(asyncio.create_task(run_telegram(cfg, subs, trade_state, store, fractals_store)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    asyncio.run(main())

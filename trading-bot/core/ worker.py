"""
Воркер: получает новую свечу → считает индикаторы → проверяет сетап → 
запрашивает DeepSeek → отправляет сигнал пользователю.
"""
import asyncio
import logging

from config import config
from core.redis_client import redis_client
from core.strategy import strategy
from core.deepseek_client import analyze_setup
from core.hyperliquid_ws import HyperliquidWSClient, fetch_historical_candles

logger = logging.getLogger(__name__)

# Будет установлен из main.py
_bot = None
_ws_client: HyperliquidWSClient | None = None


def set_bot(bot):
    global _bot
    _bot = bot


def get_ws_client() -> HyperliquidWSClient:
    return _ws_client


async def on_candle(user_id: int, token: str, tf: str, candle: dict):
    """Callback: вызывается при каждой закрытой свече."""
    # Сохраняем свечу
    await redis_client.push_candle(user_id, token, tf, candle)

    # Читаем все свечи
    candles = await redis_client.get_candles(user_id, token, tf)

    ctx = strategy.evaluate(token, tf, candles)
    if ctx is None or not ctx.has_signal:
        return

    setup = ctx.setup

    # Проверяем кулдаун
    if await redis_client.is_on_cooldown(user_id, token, tf):
        return

    logger.info(f"Setup found! uid={user_id} {token}/{tf} {setup.direction}")

    # Запрашиваем DeepSeek
    analysis = await analyze_setup(token, tf, setup)
    if analysis is None:
        logger.warning("DeepSeek returned None, sending raw signal")
        await send_raw_signal(user_id, token, tf, setup)
        return

    verdict = analysis.get("verdict", "SKIP")
    if verdict == "SKIP":
        reason = analysis.get("skip_reason", "неизвестно")
        logger.info(f"Signal skipped by AI: {reason}")
        return

    # Устанавливаем кулдаун перед отправкой
    await redis_client.set_cooldown(user_id, token, tf)

    # Формируем и отправляем сообщение
    msg = format_signal_message(token, tf, setup, analysis)
    if _bot:
        await _bot.send_message(user_id, msg, parse_mode="HTML")


def format_signal_message(token: str, tf: str, setup, analysis: dict) -> str:
    direction = analysis.get("direction", setup.direction)
    verdict = analysis.get("verdict", "")
    confidence = analysis.get("confidence", 0)
    entry_low = analysis.get("entry_low", setup.close_last)
    entry_high = analysis.get("entry_high", setup.close_last)
    sl = analysis.get("stop_loss", 0)
    tp1 = analysis.get("take_profit_1", 0)
    tp2 = analysis.get("take_profit_2", 0)
    rr1 = analysis.get("rr_ratio_tp1", 0)
    rr2 = analysis.get("rr_ratio_tp2", 0)
    desc = analysis.get("analysis", "")

    emoji = "🟢" if direction == "LONG" else "🔴"
    verdict_str = "⚡️ Агрессивный" if verdict == "AGGRESSIVE_ENTRY" else "⚠️ Осторожный"
    tf_display = config.TF_DISPLAY.get(tf, tf)

    return (
        f"{emoji} <b>СИГНАЛ: {direction}</b> | {token} | {tf_display}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Вход:</b> {verdict_str}\n"
        f"📊 <b>Уверенность AI:</b> {confidence}%\n"
        f"\n"
        f"📍 <b>Вход:</b> {entry_low:.4f} – {entry_high:.4f}\n"
        f"🛑 <b>Стоп-лосс:</b> {sl:.4f}\n"
        f"🎯 <b>TP1:</b> {tp1:.4f}  (RR {rr1:.1f}:1)\n"
        f"🎯 <b>TP2:</b> {tp2:.4f}  (RR {rr2:.1f}:1)\n"
        f"\n"
        f"📈 <b>ADX:</b> {setup.adx_value:.1f}  "
        f"+DI {setup.plus_di:.1f} / -DI {setup.minus_di:.1f}\n"
        f"📦 <b>Объём:</b> {setup.volume_ratio:.2f}x от среднего\n"
        f"\n"
        f"💬 <i>{desc}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Таймфрейм: {tf_display} | 🔗 Hyperliquid"
    )


async def send_raw_signal(user_id: int, token: str, tf: str, setup):
    """Отправить сигнал без AI анализа (fallback)."""
    if not _bot:
        return
    emoji = "🟢" if setup.direction == "LONG" else "🔴"
    tf_display = config.TF_DISPLAY.get(tf, tf)
    msg = (
        f"{emoji} <b>{setup.direction}</b> | {token} | {tf_display}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>AI анализ недоступен — базовый сигнал</i>\n"
        f"\n"
        f"📍 <b>Цена закрытия:</b> {setup.close_last:.4f}\n"
        f"📊 <b>ADX:</b> {setup.adx_value:.1f} | "
        f"+DI {setup.plus_di:.1f} / -DI {setup.minus_di:.1f}\n"
        f"📦 <b>Объём:</b> {setup.volume_ratio:.2f}x\n"
    )
    await redis_client.set_cooldown(user_id, token, tf)
    await _bot.send_message(user_id, msg, parse_mode="HTML")


async def init_worker(bot):
    """Инициализировать воркер: восстановить подписки из Redis."""
    global _ws_client
    set_bot(bot)

    _ws_client = HyperliquidWSClient(on_candle=on_candle)

    # Восстанавливаем все активные подписки
    all_subs = await redis_client.get_all_subscriptions()
    for user_id, subs in all_subs.items():
        for token, tf in subs:
            # Инициализируем исторические свечи если нет
            candles = await redis_client.get_candles(user_id, token, tf)
            if len(candles) < config.MIN_CANDLES:
                logger.info(f"Loading history for {token}/{tf} uid={user_id}")
                hist = await fetch_historical_candles(token, tf, count=200)
                if hist:
                    await redis_client.set_candles_bulk(user_id, token, tf, hist)

            await _ws_client.subscribe(user_id, token, tf)

    logger.info(f"Worker initialized. Total subscriptions: {sum(len(v) for v in all_subs.values())}")

    # Запускаем WS в фоне
    asyncio.create_task(_ws_client.run_forever())
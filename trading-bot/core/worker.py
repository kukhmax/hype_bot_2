"""
Воркер: получает новую свечу → считает индикаторы → проверяет сетап → 
запрашивает DeepSeek → отправляет сигнал пользователю.
"""
import asyncio
import logging
import html

from config import config
from core.redis_client import redis_client
from core.strategy import strategy
from core.deepseek_client import analyze_setup
from core.gemini_client import analyze_setup_with_gemini
from core.hyperliquid_ws import HyperliquidWSClient, fetch_historical_candles

logger = logging.getLogger(__name__)

# Будет установлен из main.py
_bot = None
_ws_client: HyperliquidWSClient | None = None
# Трекинг сигналов для периодического отчета: {(user_id, token, tf): bool}
_signals_status: dict[tuple[int, str, str], bool] = {}


def set_bot(bot):
    """Устанавливает глобальный экземпляр бота для использования в воркере."""
    global _bot
    _bot = bot


def get_ws_client() -> HyperliquidWSClient:
    """Возвращает текущий инстанс WS-клиента Hyperliquid."""
    return _ws_client


async def on_candle(user_id: int, token: str, tf: str, candle: dict):
    """
    Callback: вызывается при закрытии каждой свечи.
    Сохраняет свечу, вызывает стратегию, получает подтверждение AI и отправляет сигнал.
    """
    # 1. Сохраняем/обновляем свечу в Redis (держим 1000 для запаса под старшие ТФ)
    await redis_client.update_or_append_candle(user_id, token, tf, candle, max_len=1000)

    # Читаем все свечи
    candles = await redis_client.get_candles(user_id, token, tf)

    ctx = strategy.evaluate(token, tf, candles)
    if ctx is None or not ctx.has_signal:
        return

    setup = ctx.setup

    # Проверяем кулдаун
    if await redis_client.is_on_cooldown(user_id, token, tf):
        return

    logger.info(f"Signal detected! {token}/{tf} direction: {setup.direction} | price: {setup.close_last} | uid={user_id}")
    
    # Отмечаем для отчета
    _signals_status[(user_id, token, tf)] = True

    # Запрашиваем Gemini
    logger.info(f"Requesting Gemini analysis for {token}/{tf}")
    gemini_report = await analyze_setup_with_gemini(token, tf, setup, setup.close_last)

    # Запрашиваем DeepSeek
    logger.info(f"Requesting DeepSeek analysis for {token}/{tf}")
    analysis = await analyze_setup(token, tf, setup, gemini_report)
    if analysis is None:
        logger.warning("DeepSeek returned None, sending raw signal")
        await send_raw_signal(user_id, token, tf, setup)
        return

    verdict = analysis.get("verdict", "SKIP")
    logger.info(f"DeepSeek verdict: {verdict}")

    # Устанавливаем кулдаун перед отправкой
    await redis_client.set_cooldown(user_id, token, tf)

    # Формируем и отправляем сообщение (теперь отправляем всегда, даже при SKIP)
    msg = format_signal_message(token, tf, setup, analysis, gemini_report)
    if _bot:
        try:
            await _bot.send_message(user_id, msg, parse_mode="HTML")
            logger.info(f"Signal message sent to user {user_id} for {token}/{tf}")
        except Exception as e:
            logger.error(f"Failed to send message to user {user_id}: {e}")


def format_signal_message(token: str, tf: str, setup, analysis: dict, gemini_report: str | None) -> str:
    """Оформляет текстовое сообщение сигнала для отправки пользователю."""
    logger.debug(f"Formatting signal message for {token}/{tf}")
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
    skip_reason = analysis.get("skip_reason", "")

    emoji = "🟢" if direction == "LONG" else "🔴"
    
    if verdict == "SKIP":
        st_color = "❌"
        verdict_str = "ОТКЛОНЕН AI (Ложный пробой/Ловушка)"
    elif verdict == "AGGRESSIVE_ENTRY":
        st_color = "⚡️"
        verdict_str = "Агрессивный вход"
    else:
        st_color = "⚠️"
        verdict_str = "Осторожный вход"
        
    tf_display = config.TF_DISPLAY.get(tf, tf)
    
    # Форматируем текст отчета Gemini
    gemini_text = f"\n\n🤖 <b>Отчет Gemini:</b>\n<i>{html.escape(gemini_report)}</i>" if gemini_report else ""

    return (
        f"{emoji} <b>СИГНАЛ: {direction}</b> | {html.escape(token)} | {html.escape(tf_display)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{st_color} <b>Вердикт:</b> {verdict_str}\n"
        f"📊 <b>Уверенность DeepSeek:</b> {confidence}%\n"
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
        f"💬 <b>Анализ DeepSeek:</b> <i>{html.escape(skip_reason if verdict == 'SKIP' else desc)}</i>"
        f"{gemini_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Таймфрейм: {html.escape(tf_display)} | 🔗 Hyperliquid"
    )


async def send_raw_signal(user_id: int, token: str, tf: str, setup):
    """Отправить сигнал без AI анализа (fallback)."""
    if not _bot:
        return
    emoji = "🟢" if setup.direction == "LONG" else "🔴"
    tf_display = config.TF_DISPLAY.get(tf, tf)
    msg = (
        f"{emoji} <b>{setup.direction}</b> | {html.escape(token)} | {html.escape(tf_display)}\n"
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
    """
    Инициализировать воркер: восстановить подписки из Redis, 
    загрузить исторические данные и запустить WS-клиент в фоне.
    """
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
    
    # Запускаем периодический отчет
    asyncio.create_task(status_logger_loop())


async def status_logger_loop():
    """Периодически выводит в лог состояние всех подписок."""
    while True:
        await asyncio.sleep(config.STATUS_LOG_INTERVAL)
        try:
            all_subs = await redis_client.get_all_subscriptions()
            if not all_subs:
                logger.info("[STATUS] Нет активных подписок")
                continue

            lines = [" [PERIODIC STATUS REPORT]"]
            for user_id, subs in all_subs.items():
                for token, tf in subs:
                    # Считаем количество свечей
                    candles = await redis_client.get_candles(user_id, token, tf)
                    count = len(candles)
                    
                    # Был ли сигнал
                    has_signal = _signals_status.get((user_id, token, tf), False)
                    signal_str = "✅ БЫЛ" if has_signal else "❌ нет"
                    
                    lines.append(
                        f"  • User {user_id} | {token}/{tf} | Свечей: {count}/{config.MIN_CANDLES} | Сигнал: {signal_str}"
                    )
                    
                    # Сбрасываем статус сигнала после отчета
                    _signals_status[(user_id, token, tf)] = False
            
            logger.info("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in status_logger_loop: {e}")
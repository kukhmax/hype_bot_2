"""
Воркер: получает новую свечу → считает индикаторы → проверяет сетап → 
запрашивает DeepSeek → отправляет сигнал пользователю.
"""
import asyncio
import logging
import html

from aiogram.types import BufferedInputFile

from config import config
from core.redis_client import redis_client
from core.strategy import strategy
from core.deepseek_client import analyze_setup
from core.gemini_client import analyze_setup_with_gemini
from core.hyperliquid_ws import HyperliquidWSClient, fetch_historical_candles
from core.charting import generate_setup_chart
from bot.keyboards import signal_trade_kb

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

    # Сохраняем сетап во временном кэше для ручной оценки AI
    setup_data = {
        "direction": setup.direction,
        "close_last": setup.close_last,
        "ema_high_last": setup.ema_high_last,
        "ema_low_last": setup.ema_low_last,
        "plus_di": setup.plus_di,
        "minus_di": setup.minus_di,
        "adx_value": setup.adx_value,
        "prev_high": setup.prev_high,
        "prev_low": setup.prev_low,
        "volume_ratio": setup.volume_ratio,
    }
    await redis_client.save_latest_setup(user_id, token, tf, setup_data)

    # Устанавливаем кулдаун перед отправкой
    await redis_client.set_cooldown(user_id, token, tf)

    # Формируем базовое сообщение без AI
    msg = format_base_signal_message(token, tf, setup)
    
    # Примерно прикидываем SL/TP для кнопки (будут уточнены AI, но базово даем текущие экстремумы)
    sl = setup.ema_low_last if setup.direction == "LONG" else setup.ema_high_last
    tp1 = setup.close_last * 1.02 if setup.direction == "LONG" else setup.close_last * 0.98

    if _bot:
        try:
            # Сначала отправляем фотографию графика
            try:
                tf_display = config.TF_DISPLAY.get(tf, tf)
                chart_bytes = await asyncio.to_thread(
                    generate_setup_chart,
                    token=token,
                    tf_display=tf_display,
                    candles=candles,
                    ema_high=ctx.indicators.ema_high,
                    ema_low=ctx.indicators.ema_low
                )
                photo = BufferedInputFile(chart_bytes.getvalue(), filename=f"{token}_{tf}.png")
                await _bot.send_photo(chat_id=user_id, photo=photo)
            except Exception as chart_err:
                logger.error(f"Failed to generate/send chart for {token}: {chart_err}")

            # Главное сообщение (с 3-мя кнопками)
            kbd = signal_trade_kb(token=token, direction=setup.direction, sl=sl, tp=tp1, tf=tf)
            await _bot.send_message(user_id, msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kbd)
            
            logger.info(f"Signal message sent to user {user_id} for {token}/{tf}")
        except Exception as e:
            logger.error(f"Failed to send message to user {user_id}: {e}")


def format_base_signal_message(token: str, tf: str, setup) -> str:
    """Оформляет текстовое сообщение базового сигнала (до ручной проверки AI)."""
    logger.debug(f"Formatting base signal message for {token}/{tf}")
    direction = setup.direction
    emoji = "🟢" if direction == "LONG" else "🔴"
    tf_display = config.TF_DISPLAY.get(tf, tf)
    
    msg = (
        f"{emoji} <b>СИГНАЛ: {direction}</b> | {html.escape(token)} | {html.escape(tf_display)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Цена сейчас:</b> {setup.close_last:.4f}\n"
        f"\n"
        f"📈 <b>ADX:</b> {setup.adx_value:.1f}  "
        f"+DI {setup.plus_di:.1f} / -DI {setup.minus_di:.1f}\n"
        f"📦 <b>Объём:</b> {setup.volume_ratio:.2f}x от среднего\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <i>Нажмите кнопки ниже для глубокого анализа AI</i>\n"
        f"⏱ <a href=\"https://app.hyperliquid.xyz/trade/{html.escape(token)}\">🔗 Открыть на Hyperliquid</a>"
    )
    return msg


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
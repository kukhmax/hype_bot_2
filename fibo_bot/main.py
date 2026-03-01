"""
Fibo Bot — Точка входа.

Elliott + Fibonacci + VWAP + Order Flow + ML
Генерация торговых сигналов с графиками → Telegram.
Биржа: MEXC (spot / futures).
"""

import asyncio
import signal
import sys

from config import config
from utils.logger import setup_logger, get_logger
from utils.redis_manager import redis_manager
from utils.db_manager import db_manager
from engines.market_engine import MarketEngine, CandleBuffer
from engines.strategy_engine import StrategyEngine
from engines.risk_engine import RiskEngine
from ml.ml_engine import ml_engine
from services.chart_service import chart_service
from services.telegram_bot import telegram_service

# Инициализация логгера
setup_logger(
    log_level=config.log_level,
    log_file=config.log_file,
)
logger = get_logger("main")


async def on_candle(symbol: str, timeframe: str, buffer: CandleBuffer):
    """
    Коллбэк при закрытии свечи.
    Здесь будет вызываться весь pipeline:
    Features → Strategy → Risk → ML → Chart → Telegram.
    """
    candle = buffer[0]
    logger.debug(
        f"📊 Свеча закрыта: {symbol} {timeframe} | "
        f"O={candle.open} H={candle.high} L={candle.low} C={candle.close} "
        f"V={candle.volume}"
    )

    # Шаг 3 & 4 — Feature Engine & Strategy Engine
    # StrategyEngine внутри себя вызывает FeatureEngine.compute()
    try:
        signal = await strategy_engine.analyze(symbol, timeframe, buffer)
    except Exception as e:
        logger.error(f"Ошибка в Strategy Engine: {e}", exc_info=True)
        return

    if not signal:
        return  # Нет сигнала

    # Шаг 5 — Risk Engine
    # Для RiskEngine нам нужны фичи, достанем их заново (быстро, т.к. уже закэшировано в буфере)
    # или можно передавать фичи из StrategyEngine, но пока для простоты вызовем:
    features = strategy_engine.feature_engine.compute(buffer)
    if not features:
        return

    try:
        risk_result = await risk_engine.validate(signal, features)
    except Exception as e:
        logger.error(f"Ошибка в Risk Engine: {e}", exc_info=True)
        return

    if not risk_result:
        return  # Отклонено риск-менеджментом

    # Обогащаем сигнал размером позиции
    signal.explanation += f"\nРекомендуемая маржа: {risk_result['margin_pct']}%"

    logger.info(f"🚀 СИГНАЛ ПРОШЁЛ РИСК-ФИЛЬТРЫ: {signal.direction} {signal.symbol}")

    # Шаг 8 — ML Engine
    try:
        # Предсказываем вероятность отработки (TP)
        prob_pct = await ml_engine.predict_probability(features, signal)
        signal.probability = prob_pct
        if prob_pct > 0:
            signal.explanation += f"\nML Вероятность: {prob_pct}%"
    except Exception as e:
        logger.error(f"Ошибка в ML Engine: {e}", exc_info=True)

    # Шаг 6 — Chart Service
    chart_path = chart_service.generate_chart(signal, buffer)
    if chart_path:
        signal.explanation += "\nГрафическая разметка: ✅"
        # Для Telegram бота нам понадобится путь к файлу, сохраним его в объекте:
        signal.chart_path = chart_path

    # Шаг 7 — Signal Formatter → Telegram
    # Отправляем сигнал асинхронно
    asyncio.create_task(telegram_service.broadcast_signal(signal))


async def main():
    """Главный цикл приложения."""
    logger.info("=" * 60)
    logger.info("🚀 Fibo Bot запускается...")
    logger.info(f"   Биржа: MEXC ({config.exchange.market_type})")
    logger.info(f"   Символ: {config.trading.default_symbol}")
    logger.info(f"   Таймфрейм: {config.trading.default_timeframe}")
    logger.info(f"   Режим: {config.trading.default_mode}")
    logger.info(f"   Риск на сделку: {config.trading.risk_per_trade * 100}%")
    logger.info("=" * 60)

    # --- Инициализация инфраструктуры ---
    try:
        await redis_manager.connect()
    except Exception as e:
        logger.error(f"Не удалось подключиться к Redis: {e}")
        logger.warning("Продолжаем без Redis (in-memory fallback)")

    try:
        await db_manager.connect()
        await db_manager.init_tables()
    except Exception as e:
        logger.error(f"Не удалось подключиться к PostgreSQL: {e}")
        logger.warning("Продолжаем без PostgreSQL")

    # --- Инициализация Market Engine ---
    global strategy_engine, risk_engine
    strategy_engine = StrategyEngine()
    risk_engine = RiskEngine()

    market_engine = MarketEngine(
        symbol=config.trading.default_symbol,
        timeframes=config.trading.timeframes,
        market_type=config.exchange.market_type,
        on_candle=on_candle,
    )

    # Шаг 7 — инициализация Telegram Bot (запуск поллинга в фоне)
    asyncio.create_task(telegram_service.start())

    logger.info("✅ Все компоненты инициализированы. Запуск...")

    try:
        await market_engine.start()
    except asyncio.CancelledError:
        logger.info("🛑 Fibo Bot остановлен.")
    finally:
        await telegram_service.stop()
        await market_engine.stop()
        await redis_manager.disconnect()
        await db_manager.disconnect()


def shutdown(sig, frame):
    """Обработчик сигнала завершения."""
    logger.info(f"Получен сигнал {sig}, завершение...")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Fibo Bot остановлен (Ctrl+C).")

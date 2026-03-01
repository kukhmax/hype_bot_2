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

    # TODO: Шаг 3 — Feature Engine
    # TODO: Шаг 4 — Strategy Engine
    # TODO: Шаг 5 — Risk Engine
    # TODO: Шаг 8 — ML Engine
    # TODO: Шаг 6 — Chart Service
    # TODO: Шаг 7 — Signal Formatter → Telegram


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
    market_engine = MarketEngine(
        symbol=config.trading.default_symbol,
        timeframes=config.trading.timeframes,
        market_type=config.exchange.market_type,
        on_candle=on_candle,
    )

    # TODO: Шаг 7 — инициализация Telegram Bot

    logger.info("✅ Все компоненты инициализированы. Запуск...")

    try:
        await market_engine.start()
    except asyncio.CancelledError:
        logger.info("🛑 Fibo Bot остановлен.")
    finally:
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

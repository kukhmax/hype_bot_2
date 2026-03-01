"""
Fibo Bot — Точка входа.

Elliott + Fibonacci + VWAP + Order Flow + ML
Генерация торговых сигналов с графиками → Telegram.
"""

import asyncio
import signal
import sys

from config import config
from utils.logger import setup_logger, get_logger

# Инициализация логгера
setup_logger(
    log_level=config.log_level,
    log_file=config.log_file,
)
logger = get_logger("main")


async def main():
    """Главный цикл приложения."""
    logger.info("=" * 60)
    logger.info("🚀 Fibo Bot запускается...")
    logger.info(f"   Символ: {config.trading.default_symbol}")
    logger.info(f"   Таймфрейм: {config.trading.default_timeframe}")
    logger.info(f"   Режим: {config.trading.default_mode}")
    logger.info(f"   Риск на сделку: {config.trading.risk_per_trade * 100}%")
    logger.info("=" * 60)

    # TODO: Шаг 2 — инициализация Market Engine
    # TODO: Шаг 3 — инициализация Feature Engine
    # TODO: Шаг 4 — инициализация Strategy Engine
    # TODO: Шаг 5 — инициализация Risk Engine
    # TODO: Шаг 6 — инициализация Chart Service
    # TODO: Шаг 7 — инициализация Telegram Bot
    # TODO: Шаг 8 — инициализация ML Engine

    logger.info("✅ Fibo Bot — структура проекта создана. Компоненты будут добавлены.")

    # Ожидание завершения
    try:
        while True:
            await asyncio.sleep(60)
            logger.debug("💓 Heartbeat")
    except asyncio.CancelledError:
        logger.info("🛑 Fibo Bot остановлен.")


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

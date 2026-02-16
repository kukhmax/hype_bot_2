"""Конфигурация логирования приложения."""

import logging
import sys

def setup_logger():
    """Инициализирует базовую конфигурацию логирования и возвращает именованный логгер 'bot'."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log")
        ]
    )
    return logging.getLogger("bot")

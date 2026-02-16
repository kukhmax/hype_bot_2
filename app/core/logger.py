"""Конфигурация логирования приложения."""

import logging
import os
import sys

def setup_logger():
    """Инициализирует базовую конфигурацию логирования и возвращает именованный логгер 'bot'."""
    level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log")
        ]
    )
    return logging.getLogger("bot")

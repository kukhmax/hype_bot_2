"""Конфигурация логирования приложения."""

import logging
import os
import sys


def setup_logger():
    """Инициализирует базовую конфигурацию логирования и возвращает именованный логгер 'bot'."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = os.getenv("LOG_DIR")
    if not log_dir:
        # По умолчанию: если есть каталоги /opt/app/logs (контейнер) или ./logs (локально)
        default_container = "/opt/app/logs"
        default_local = os.path.join(os.getcwd(), "logs")
        log_dir = default_container if os.path.isdir(default_container) else default_local
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        # запасной вариант — текущая директория
        log_dir = os.getcwd()

    log_file = os.path.join(log_dir, os.getenv("LOG_FILE", "bot.log"))

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8")
    ]

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )
    # Подавляем подробный дебаг сторонних логгеров
    try:
        logging.getLogger("websockets").setLevel(logging.INFO)
        logging.getLogger("websockets.client").setLevel(logging.INFO)
        logging.getLogger("websockets.server").setLevel(logging.INFO)
    except Exception:
        pass
    return logging.getLogger("bot")

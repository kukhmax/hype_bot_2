"""
Fibo Bot — Логирование.

Настройка логгера с выводом в файл и консоль.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logger(
    name: str = "fibo_bot",
    log_level: str = "INFO",
    log_file: str = "logs/fibo_bot.log",
) -> logging.Logger:
    """
    Создание и настройка логгера.

    Args:
        name: имя логгера
        log_level: уровень логирования (DEBUG, INFO, WARNING, ERROR)
        log_file: путь к файлу лога
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Формат сообщений
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Консольный handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # Файловый handler (ротация: 10 MB, 5 файлов)
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Получить дочерний логгер."""
    return logging.getLogger(f"fibo_bot.{name}")

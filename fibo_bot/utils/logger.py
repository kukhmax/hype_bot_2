"""
Fibo Bot — Логирование.

Система логирования с раздельными файлами:
  - logs/fibo_bot.log       — общий лог (все компоненты)
  - logs/market_engine.log  — данные с биржи
  - logs/strategy.log       — сигналы стратегии
  - logs/signals.log        — отправленные сигналы
  - logs/errors.log         — только ошибки

Все важные операции логируются с подробностями.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

# Директория для логов
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Формат сообщений
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Маппинг компонентов → файлы логов
COMPONENT_LOG_FILES = {
    "market_engine": "market_engine.log",
    "feature_engine": "feature_engine.log",
    "strategy_engine": "strategy.log",
    "risk_engine": "strategy.log",
    "ml_engine": "strategy.log",
    "chart_service": "signals.log",
    "signal_formatter": "signals.log",
    "telegram_bot": "signals.log",
    "redis_manager": "infrastructure.log",
    "db_manager": "infrastructure.log",
}


def _create_file_handler(
    filename: str,
    level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> RotatingFileHandler:
    """Создать файловый handler с ротацией."""
    filepath = os.path.join(LOG_DIR, filename)
    handler = RotatingFileHandler(
        filepath,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def setup_logger(
    name: str = "fibo_bot",
    log_level: str = "INFO",
    log_file: str = "logs/fibo_bot.log",
) -> logging.Logger:
    """
    Инициализация корневого логгера.

    Создаёт:
    - Консольный вывод (stdout)
    - Общий файл лога (fibo_bot.log)
    - Файл ошибок (errors.log)
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Ловим всё, фильтруем на handlers

    # Не дублируем handlers при повторном вызове
    if logger.handlers:
        return logger

    fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # --- Консольный handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # --- Общий файл лога ---
    main_handler = _create_file_handler("fibo_bot.log", level=logging.DEBUG)
    logger.addHandler(main_handler)

    # --- Файл ошибок (только ERROR и CRITICAL) ---
    error_handler = _create_file_handler("errors.log", level=logging.ERROR)
    logger.addHandler(error_handler)

    logger.info("=" * 60)
    logger.info("Система логирования инициализирована")
    logger.info(f"  Уровень консоли: {log_level}")
    logger.info(f"  Лог-файлы: {LOG_DIR}/")
    logger.info("=" * 60)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер для компонента.

    Автоматически добавляет файловый handler для компонента
    (market_engine → market_engine.log, и т.д.).
    """
    logger = logging.getLogger(f"fibo_bot.{name}")

    # Добавляем компонентный файл, если есть маппинг
    if name in COMPONENT_LOG_FILES and not logger.handlers:
        component_file = COMPONENT_LOG_FILES[name]
        handler = _create_file_handler(component_file, level=logging.DEBUG)
        logger.addHandler(handler)

    return logger

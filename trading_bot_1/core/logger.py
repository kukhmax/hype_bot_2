import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from core.config import settings

# Директория для логов
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

def setup_logger(name: str) -> logging.Logger:
    """Provides a configured logger instance."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        logger.setLevel(level)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File handler (ротация: 10 MB, 5 файлов)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

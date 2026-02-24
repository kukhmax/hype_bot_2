import logging
import sys
from core.config import settings

def setup_logger(name: str) -> logging.Logger:
    """Provides a configured logger instance."""
    logger = logging.getLogger(name)
    
    # Avoid adding multiple handlers if logger is already configured
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

    return logger

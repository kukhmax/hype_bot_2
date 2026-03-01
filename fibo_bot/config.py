"""
Fibo Bot — Конфигурация.

Все настройки загружаются из переменных окружения (.env файл).
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    """Настройки подключения к бирже."""
    ws_url: str = "wss://fstream.binance.com/ws"
    rest_url: str = "https://fapi.binance.com"
    api_key: str = ""
    api_secret: str = ""


@dataclass
class TelegramConfig:
    """Настройки Telegram бота."""
    token: str = ""
    chat_id: str = ""
    admin_ids: List[int] = field(default_factory=list)


@dataclass
class RedisConfig:
    """Настройки Redis."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""


@dataclass
class TradingConfig:
    """Параметры торговой стратегии."""
    # Торговая пара по умолчанию
    default_symbol: str = "BTCUSDT"
    # Основной таймфрейм
    default_timeframe: str = "5m"
    # Таймфреймы для анализа
    timeframes: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h"])

    # --- Wave Detection ---
    min_impulse_atr: float = 2.5       # минимум ATR для Wave 1
    fib_zone_low: float = 0.5          # нижняя граница зоны Fibonacci
    fib_zone_high: float = 0.618       # верхняя граница зоны Fibonacci
    fib_extension_target: float = 1.618  # цель Wave 3

    # --- Risk ---
    risk_per_trade: float = 0.01       # 1% депозита на сделку
    max_daily_loss: float = 0.03       # макс. дневной убыток 3%
    max_open_trades: int = 3           # макс. одновременных позиций
    min_adx: float = 20.0             # мин. ADX для фильтра

    # --- ML ---
    ml_threshold_conservative: float = 0.70
    ml_threshold_balanced: float = 0.65
    ml_threshold_aggressive: float = 0.58
    default_mode: str = "balanced"     # conservative / balanced / aggressive

    # --- Chart ---
    chart_candles: int = 100           # количество свечей на графике
    chart_dpi: int = 150               # DPI графика


@dataclass
class Config:
    """Главная конфигурация приложения."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)

    # Общие настройки
    log_level: str = "INFO"
    log_file: str = "logs/fibo_bot.log"
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        """Загрузка конфигурации из переменных окружения."""
        config = cls()

        # Exchange
        config.exchange.api_key = os.getenv("BINANCE_API_KEY", "")
        config.exchange.api_secret = os.getenv("BINANCE_API_SECRET", "")

        # Telegram
        config.telegram.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
        if admin_ids_str:
            config.telegram.admin_ids = [int(x.strip()) for x in admin_ids_str.split(",")]

        # Redis
        config.redis.host = os.getenv("REDIS_HOST", "localhost")
        config.redis.port = int(os.getenv("REDIS_PORT", "6379"))
        config.redis.password = os.getenv("REDIS_PASSWORD", "")

        # Trading
        config.trading.default_symbol = os.getenv("DEFAULT_SYMBOL", "BTCUSDT")
        config.trading.default_timeframe = os.getenv("DEFAULT_TIMEFRAME", "5m")
        config.trading.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        config.trading.default_mode = os.getenv("TRADING_MODE", "balanced")

        # General
        config.log_level = os.getenv("LOG_LEVEL", "INFO")
        config.debug = os.getenv("DEBUG", "false").lower() == "true"

        return config


# Глобальный экземпляр конфигурации
config = Config.from_env()

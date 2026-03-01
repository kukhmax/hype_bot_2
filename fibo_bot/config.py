"""
Fibo Bot — Конфигурация.

Все настройки загружаются из переменных окружения (.env файл).
Биржа: MEXC (spot + futures).
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    """Настройки подключения к MEXC."""
    # WebSocket
    ws_spot_url: str = "wss://wbs.mexc.com/ws"
    ws_futures_url: str = "wss://contract.mexc.com/edge"
    # REST API
    rest_spot_url: str = "https://api.mexc.com/api/v3/klines"
    rest_futures_url: str = "https://contract.mexc.com/api/v1/contract/kline"
    # API ключи
    api_key: str = ""
    api_secret: str = ""
    # Тип рынка: spot / futures
    market_type: str = "futures"


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
    url: str = ""  # redis://host:port/db

    def get_url(self) -> str:
        if self.url:
            return self.url
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


@dataclass
class PostgresConfig:
    """Настройки PostgreSQL."""
    host: str = "localhost"
    port: int = 5432
    database: str = "fibo_bot"
    user: str = "fibo_bot"
    password: str = ""
    url: str = ""  # postgresql://user:pass@host:port/db

    def get_url(self) -> str:
        if self.url:
            return self.url
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class TradingConfig:
    """Параметры торговой стратегии."""
    # Торговые пары (список, считываемый из SYMBOLS)
    symbols: List[str] = field(default_factory=lambda: ["SOL_USDT"])
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


# Маппинг таймфреймов для MEXC
TF_MAP_MEXC_FUTURES = {
    "1m": "Min1", "5m": "Min5", "15m": "Min15",
    "30m": "Min30", "1h": "Min60", "4h": "Hour4", "1d": "Day1",
}

TF_MAP_MEXC_SPOT = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "60m", "4h": "4h", "1d": "1d",
}

# Таймфреймы → секунды
TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class Config:
    """Главная конфигурация приложения."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)

    # Общие настройки
    log_level: str = "INFO"
    log_file: str = "logs/fibo_bot.log"
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        """Загрузка конфигурации из переменных окружения."""
        config = cls()

        # Exchange (MEXC)
        config.exchange.api_key = os.getenv("MEXC_API_KEY", "")
        config.exchange.api_secret = os.getenv("MEXC_API_SECRET", "")
        config.exchange.market_type = os.getenv("MARKET_TYPE", "futures")

        # Telegram
        config.telegram.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
        if admin_ids_str:
            config.telegram.admin_ids = [
                int(x.strip()) for x in admin_ids_str.split(",") if x.strip()
            ]

        # Redis
        config.redis.host = os.getenv("REDIS_HOST", "localhost")
        config.redis.port = int(os.getenv("REDIS_PORT", "6379"))
        config.redis.password = os.getenv("REDIS_PASSWORD", "")
        config.redis.url = os.getenv("REDIS_URL", "")

        # PostgreSQL
        config.postgres.host = os.getenv("POSTGRES_HOST", "localhost")
        config.postgres.port = int(os.getenv("POSTGRES_PORT", "5432"))
        config.postgres.database = os.getenv("POSTGRES_DB", "fibo_bot")
        config.postgres.user = os.getenv("POSTGRES_USER", "fibo_bot")
        config.postgres.password = os.getenv("POSTGRES_PASSWORD", "")
        config.postgres.url = os.getenv("DATABASE_URL", "")

        # Trading
        symbols_str = os.getenv("SYMBOLS", "SOL_USDT")
        config.trading.symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
        config.trading.default_timeframe = os.getenv("DEFAULT_TIMEFRAME", "5m")
        config.trading.risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        config.trading.default_mode = os.getenv("TRADING_MODE", "balanced")

        # General
        config.log_level = os.getenv("LOG_LEVEL", "INFO")
        config.debug = os.getenv("DEBUG", "false").lower() == "true"

        return config


# Глобальный экземпляр конфигурации
config = Config.from_env()

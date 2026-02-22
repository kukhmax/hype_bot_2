import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: int | None = int(os.getenv("TELEGRAM_CHAT_ID", 0)) or None

    # AI
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Exchange — зафиксировано: MEXC Futures
    DEFAULT_EXCHANGE: str = "mexc"
    DEFAULT_MARKET_TYPE: str = "futures"
    DEFAULT_SYMBOL: str = os.getenv("DEFAULT_SYMBOL", "BTC_USDT")

    # Multi-Timeframe: 15m = вход, 1h = фильтр тренда
    ENTRY_TIMEFRAME: str = "15m"         # основной TF для паттернов и входа
    TREND_TIMEFRAME: str = "1h"          # старший TF для подтверждения тренда
    # Легаси (для совместимости с data_feed)
    DEFAULT_TIMEFRAME: str = "15m"

    # Strategy
    MIN_PATTERNS_TO_SIGNAL: int = int(os.getenv("MIN_PATTERNS_TO_SIGNAL", 3))
    MIN_CONFIDENCE: int = int(os.getenv("MIN_CONFIDENCE", 60))
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", 14))
    ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", 14))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", 14))
    CCI_PERIOD: int = int(os.getenv("CCI_PERIOD", 20))

    # Кулдаун между сигналами (секунды)
    SIGNAL_COOLDOWN_SEC: int = int(os.getenv("SIGNAL_COOLDOWN_SEC", 900))  # 15 мин

    # Таймаут ожидания подтверждения от пользователя (секунды)
    CONFIRM_TIMEOUT_SEC: int = int(os.getenv("CONFIRM_TIMEOUT_SEC", 300))  # 5 мин

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # WebSocket URLs
    WS_URLS = {
        "mexc": {
            "spot": "wss://wbs.mexc.com/ws",
            "futures": "wss://contract.mexc.com/ws",
        },
        "binance": {
            "spot": "wss://stream.binance.com:9443/ws",
            "futures": "wss://fstream.binance.com/ws",
        },
        "bybit": {
            "spot": "wss://stream.bybit.com/v5/public/spot",
            "futures": "wss://stream.bybit.com/v5/public/linear",
        },
    }

    # REST API для получения исторических свечей
    REST_URLS = {
        "mexc": {
            "spot": "https://api.mexc.com/api/v3/klines",
            "futures": "https://contract.mexc.com/api/v1/contract/kline",
        },
        "binance": {
            "spot": "https://api.binance.com/api/v3/klines",
            "futures": "https://fapi.binance.com/fapi/v1/klines",
        },
        "bybit": {
            "spot": "https://api.bybit.com/v5/market/kline",
            "futures": "https://api.bybit.com/v5/market/kline",
        },
    }

    # Таймфреймы → секунды
    TF_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }

    VALID_TIMEFRAMES = list(TF_SECONDS.keys())
    VALID_EXCHANGES = ["mexc", "binance", "bybit"]

config = Config()

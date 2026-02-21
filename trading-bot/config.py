import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # DeepSeek
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1/chat/completions"

    # Hyperliquid
    HL_WS_URL: str = os.getenv("HL_WS_URL", "wss://api.hyperliquid.xyz/ws")

    # Strategy
    MIN_CANDLES: int = int(os.getenv("MIN_CANDLES", "50"))
    EMA_PERIOD: int = int(os.getenv("EMA_PERIOD", "20"))
    ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", "14"))
    ADX_THRESHOLD: float = float(os.getenv("ADX_THRESHOLD", "20"))

    # Allowed timeframes (Hyperliquid format)
    ALLOWED_TIMEFRAMES: list[str] = ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]
    TF_DISPLAY: dict[str, str] = {
        "1m": "M1", "3m": "M3", "5m": "M5",
        "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4"
    }

    # Redis key patterns
    SUBS_KEY = "subscriptions:{user_id}"          # SET of "TOKEN:TF"
    CANDLES_KEY = "candles:{user_id}:{token}:{tf}" # LIST of OHLCV json
    SIGNAL_COOLDOWN_KEY = "cooldown:{user_id}:{token}:{tf}"  # Cooldown чтобы не спамить

    SIGNAL_COOLDOWN_SEC: int = 300  # 5 минут между сигналами по одной паре
    STATUS_LOG_INTERVAL: int = 900  # 15 минут между отчетами в логах


config = Config()
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LOG_LEVEL: str = "INFO"
    EXCHANGE: str = "mexc"
    REDIS_URL: str = "redis://redis:6379/0"
    
    # API Keys for Execution (Required for Phase 3)
    API_KEY: str = ""
    API_SECRET: str = ""
    HL_WALLET_ADDRESS: str = ""
    HL_PRIVATE_KEY: str = ""
    
    # Telegram Bot (Required for Phase 4)
    TELEGRAM_BOT_TOKEN: str = ""
    ADMIN_CHAT_ID: str = ""
    
    # AI / Gemini (Required for Phase 5)
    GEMINI_API_KEY: str = ""
    
    # Ensemble ML Filter (Phase 6 — ML Signal Filtering)
    ENABLE_ML_FILTER: bool = False

    # AI Gemini Verification
    # Минимальный таймфрейм для AI верификации (в минутах).
    # На таймфреймах ниже этого порога AI пропускается,
    # т.к. EMA 200 и ADX не информативны на 1m данных.
    AI_VERIFY_MIN_TIMEFRAME: int = 5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LOG_LEVEL: str = "INFO"
    EXCHANGE: str = "binance"
    REDIS_URL: str = "redis://redis:6379/0"
    
    # API Keys for Execution (Required for Phase 3)
    API_KEY: str = ""
    API_SECRET: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

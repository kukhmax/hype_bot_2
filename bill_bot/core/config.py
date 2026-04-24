import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    redis_host: str
    redis_port: int
    redis_password: str
    log_level: str
    pairs: list[str]
    timeframe: str
    history_bars: int
    poll_seconds: int

    @staticmethod
    def from_env() -> "Config":
        host = os.getenv("REDIS_HOST", "redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", "")
        level = os.getenv("LOG_LEVEL", "INFO")
        pairs_raw = os.getenv("PAIRS", "")
        pairs = [p.strip().upper() for p in pairs_raw.split(",") if p.strip()]
        timeframe = os.getenv("TIMEFRAME", "15m")
        history_bars = int(os.getenv("HISTORY_BARS", "100"))
        poll_seconds = int(os.getenv("POLL_SECONDS", "5"))
        return Config(
            redis_host=host,
            redis_port=port,
            redis_password=password,
            log_level=level,
            pairs=pairs,
            timeframe=timeframe,
            history_bars=history_bars,
            poll_seconds=poll_seconds,
        )

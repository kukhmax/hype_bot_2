import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    redis_host: str
    redis_port: int
    redis_password: str
    log_level: str

    @staticmethod
    def from_env() -> "Config":
        host = os.getenv("REDIS_HOST", "redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", "")
        level = os.getenv("LOG_LEVEL", "INFO")
        return Config(
            redis_host=host,
            redis_port=port,
            redis_password=password,
            log_level=level,
        )

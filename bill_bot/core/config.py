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
    timeframes_available: list[str]
    history_bars: int
    poll_seconds: int
    sleep_window: int
    sleep_k: float
    fractals_max: int
    tick_size_default: float
    tick_sizes: dict[str, float]
    virtual_equity: float
    risk_pct: float
    telegram_token: str
    pairs_available: list[str]
    default_virtual_equity: float
    default_risk_pct: float

    @staticmethod
    def from_env() -> "Config":
        host = os.getenv("REDIS_HOST", "redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", "")
        level = os.getenv("LOG_LEVEL", "INFO")
        pairs_raw = os.getenv("PAIRS", "")
        pairs = [p.strip().upper() for p in pairs_raw.split(",") if p.strip()]
        timeframe = os.getenv("TIMEFRAME", "15m")
        timeframes_available_raw = os.getenv("TIMEFRAMES_AVAILABLE", timeframe)
        timeframes_available = [t.strip() for t in timeframes_available_raw.split(",") if t.strip()]
        if not timeframes_available:
            timeframes_available = [timeframe]
        history_bars = int(os.getenv("HISTORY_BARS", "100"))
        poll_seconds = int(os.getenv("POLL_SECONDS", "5"))
        sleep_window = int(os.getenv("SLEEP_WINDOW", "20"))
        sleep_k = float(os.getenv("SLEEP_K", "0.001"))
        fractals_max = int(os.getenv("FRACTALS_MAX", "200"))
        tick_size_default = float(os.getenv("TICK_SIZE", "0.01"))
        tick_sizes_raw = os.getenv("TICK_SIZES", "")
        tick_sizes: dict[str, float] = {}
        for part in tick_sizes_raw.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                continue
            sym, val = part.split(":", 1)
            sym = sym.strip().upper()
            try:
                tick_sizes[sym] = float(val.strip())
            except ValueError:
                continue
        default_virtual_equity = float(os.getenv("VIRTUAL_EQUITY", "10000"))
        default_risk_pct = float(os.getenv("RISK_PCT", "1.0"))
        virtual_equity = default_virtual_equity
        risk_pct = default_risk_pct
        telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        pairs_available_raw = os.getenv("PAIRS_AVAILABLE", pairs_raw)
        pairs_available = [p.strip().upper() for p in pairs_available_raw.split(",") if p.strip()]
        return Config(
            redis_host=host,
            redis_port=port,
            redis_password=password,
            log_level=level,
            pairs=pairs,
            timeframe=timeframe,
            timeframes_available=timeframes_available,
            history_bars=history_bars,
            poll_seconds=poll_seconds,
            sleep_window=sleep_window,
            sleep_k=sleep_k,
            fractals_max=fractals_max,
            tick_size_default=tick_size_default,
            tick_sizes=tick_sizes,
            virtual_equity=virtual_equity,
            risk_pct=risk_pct,
            telegram_token=telegram_token,
            pairs_available=pairs_available,
            default_virtual_equity=default_virtual_equity,
            default_risk_pct=default_risk_pct,
        )

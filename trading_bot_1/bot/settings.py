from typing import Dict, Any, List

class BotSettings:
    """Глобальные настройки бота, которые можно менять из ТГ."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotSettings, cls).__new__(cls)
            cls._instance.config: Dict[str, Any] = {
                "mode": "paper",  # paper / live / signals
                "risk_percent": 2.5,
                # Пары с индивидуальными настройками: {symbol: {tf, leverage}}
                "pairs": {
                    "SOL_USDT": {"tf": 5, "leverage": 20}
                },
                # Параметры стратегий (можно менять из Telegram)
                "strategy_params": {
                    "rsi_threshold": 45,        # RSI для отката (TrendPullback). Дефолт 40 → 45
                    "bb_width_threshold": 0.025, # BB Width сжатие (Breakout). Дефолт 0.015 → 0.025
                    "adx_threshold": 20,         # ADX мин. сила тренда (Breakout)
                    "sl_atr_mult": 1.5,          # SL = N * ATR
                    "rr_ratio": 2.0,             # Risk/Reward ratio
                }
            }
        return cls._instance

    @classmethod
    def get(cls) -> Dict[str, Any]:
        return cls().config
        
    @classmethod
    def update(cls, key: str, value: Any):
        cls().config[key] = value

bot_settings = BotSettings.get()

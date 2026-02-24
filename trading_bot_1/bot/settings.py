from typing import Dict, Any

class BotSettings:
    """Глобальные настройки бота, которые можно менять из ТГ."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotSettings, cls).__new__(cls)
            cls._instance.config: Dict[str, Any] = {
                "mode": "paper", # paper / live / signals
                "symbol": "SOL_USDT",
                "timeframe": 15,
                "risk_percent": 2.0
            }
        return cls._instance

    @classmethod
    def get(cls) -> Dict[str, Any]:
        return cls().config
        
    @classmethod
    def update(cls, key: str, value: Any):
        cls().config[key] = value

bot_settings = BotSettings.get()

from typing import Dict, Any, List

class BotSettings:
    """Глобальные настройки бота, которые можно менять из ТГ."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotSettings, cls).__new__(cls)
            cls._instance.config: Dict[str, Any] = {
                "mode": "paper", # paper / live / signals
                "symbols": ["SOL_USDT"],  # Список активных пар
                "timeframe": 15,
                "risk_percent": 2.0,
                "leverage": 5  # Кредитное плечо (1x, 3x, 5x, 10x, 20x)
            }
        return cls._instance

    @classmethod
    def get(cls) -> Dict[str, Any]:
        return cls().config
        
    @classmethod
    def update(cls, key: str, value: Any):
        cls().config[key] = value

bot_settings = BotSettings.get()

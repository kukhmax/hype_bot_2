from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class Signal(BaseModel):
    token: str
    timeframe: str
    side: str  # LONG or SHORT
    confidence: float  # % уверенности от DeepSeek
    entry_min: float
    entry_max: float
    stop_loss: float
    take_profit_1: float  # TP1 (например, 1:1)
    take_profit_2: float  # TP2 (например, 1:2)
    description: str
    timestamp: datetime
    price_at_signal: float

class Subscription(BaseModel):
    user_id: int
    token: str
    timeframe: str
    created_at: datetime
    active: bool = True
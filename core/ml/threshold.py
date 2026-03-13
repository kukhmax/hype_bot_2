"""
Dynamic Threshold — адаптивный порог для ансамбля.

Порог принятия сигнала рассчитывается по winrate последних N сделок:
- Высокий WinRate → снижаем порог (больше сделок)
- Низкий WinRate  → повышаем порог (строже фильтруем)
"""

from typing import List, Dict
from core.logger import setup_logger

logger = setup_logger("ml_threshold")


class DynamicThreshold:
    """
    Адаптирует threshold на основе результатов последних N сделок.
    
    Формула:
        threshold = base_threshold - (winrate - 0.5) * sensitivity
    
    Например при base=0.55, sensitivity=0.2:
        WR=60% → threshold=0.53 (чуть ниже, больше сделок)
        WR=40% → threshold=0.57 (чуть выше, меньше сделок)
        WR=50% → threshold=0.55 (базовый)
    """

    def __init__(
        self,
        window: int = 100,
        base_threshold: float = 0.55,
        sensitivity: float = 0.2,
        min_threshold: float = 0.30,
        max_threshold: float = 0.80,
    ):
        self.window = window
        self.base_threshold = base_threshold
        self.sensitivity = sensitivity
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.trade_history: List[Dict] = []

    def get_threshold(self) -> float:
        """Получить текущий динамический порог."""
        if len(self.trade_history) < 20:
            return self.base_threshold  # Мало данных — базовый порог

        recent = self.trade_history[-self.window:]
        wins = [t for t in recent if t["pnl"] > 0]
        winrate = len(wins) / len(recent)

        adjustment = (winrate - 0.5) * self.sensitivity
        threshold = self.base_threshold - adjustment

        return max(self.min_threshold, min(self.max_threshold, threshold))

    def report_trade(self, score: float, pnl: float):
        """
        Зарегистрировать результат сделки.
        
        Args:
            score: ensemble_score на момент входа
            pnl: итоговый PnL сделки
        """
        self.trade_history.append({"score": score, "pnl": pnl})

        # Ограничиваем размер истории
        if len(self.trade_history) > self.window * 2:
            self.trade_history = self.trade_history[-self.window:]

        if len(self.trade_history) % 10 == 0:
            logger.debug(
                f"DynamicThreshold: {len(self.trade_history)} сделок. "
                f"Текущий порог: {self.get_threshold():.3f}"
            )

    def get_stats(self) -> Dict:
        """Получить текущую статистику."""
        if not self.trade_history:
            return {
                "total_trades": 0,
                "winrate": 0.0,
                "threshold": self.base_threshold,
            }

        recent = self.trade_history[-self.window:]
        wins = [t for t in recent if t["pnl"] > 0]
        return {
            "total_trades": len(self.trade_history),
            "recent_trades": len(recent),
            "winrate": round(len(wins) / len(recent) * 100, 1),
            "threshold": round(self.get_threshold(), 3),
            "avg_score": round(
                sum(t["score"] for t in recent) / len(recent), 3
            ),
        }

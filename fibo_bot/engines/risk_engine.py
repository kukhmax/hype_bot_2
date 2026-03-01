"""
Fibo Bot — Risk Engine.

Проверка сигналов перед отправкой:
1. VolatilityFilter — отмена при аномально высокой волатильности
2. DailyLossFilter — лимит убыточных сделок на день
3. ExposureManager — лимит одновременно открытых сделок
4. PositionSizer — расчёт размера позиции (%)

Взаимодействует с Redis для хранения стейта
и PostgreSQL для статистики.
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone

from config import config
from engines.strategy_engine import TradeSignal
from engines.feature_engine import FeatureResult
from utils.logger import get_logger
from utils.redis_manager import redis_manager
from utils.db_manager import db_manager


logger = get_logger("risk_engine")


class VolatilityFilter:
    """
    Фильтр аномальной волатильности.
    Отклоняет сигналы, если текущий ATR сильно выше среднего.
    """

    def __init__(self, max_atr_ratio: float = 3.0):
        self.max_atr_ratio = max_atr_ratio

    def check(self, features: FeatureResult) -> bool:
        """
        Возвращает True если волатильность в норме, False если аномальная.
        """
        if features.atr_ratio > self.max_atr_ratio:
            logger.warning(
                f"[Risk: Volatility] Отклонено: ATR Ratio = {features.atr_ratio:.2f} "
                f"(макс. {self.max_atr_ratio})"
            )
            return False
        return True


class DailyLossFilter:
    """
    Лимит убыточных сделок или просадки на день.
    Читает статистику из PostgreSQL.
    """

    def __init__(self, max_losses: int = 3, max_drawdown_percent: float = 5.0):
        self.max_losses = max_losses
        self.max_drawdown = max_drawdown_percent

    async def check(self) -> bool:
        """
        Возвращает True если можно торговать, False если лимит исчерпан.
        """
        try:
            # Получаем статистику за сегодня (1 день)
            stats = await db_manager.get_performance(days=1)

            losses = stats.get("losses", 0)
            total_pnl = stats.get("total_pnl", 0.0)

            if losses >= self.max_losses:
                logger.warning(
                    f"[Risk: DailyLoss] Отклонено: Достигнут лимит убытков: "
                    f"{losses}/{self.max_losses}"
                )
                return False

            if total_pnl < -self.max_drawdown:
                logger.warning(
                    f"[Risk: DailyLoss] Отклонено: Просадка за день: "
                    f"{total_pnl:.2f}% (макс. -{self.max_drawdown}%)"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"[Risk: DailyLoss] Ошибка проверки статистики: {e}")
            # В случае ошибки разрешаем, чтобы не блокировать полностью
            return True


class ExposureManager:
    """
    Контроль общей экспозиции (одновременных сделок).
    Для демо используем Redis стейт (как счетчик) или просто заглушку.
    В реальной системе опрашивает API биржи на предмет открытых ордеров/позиций.
    """

    def __init__(self, max_open_trades: int = 3):
        self.max_open_trades = max_open_trades

    async def check(self, symbol: str) -> bool:
        """
        Возвращает True если можно открыть новую сделку.
        """
        try:
            # Временно читаем из Redis (для симуляции)
            val = await redis_manager.get_bot_state("active_trades")
            active_trades = int(val) if val else 0

            if active_trades >= self.max_open_trades:
                logger.warning(
                    f"[Risk: Exposure] Отклонено: Максимум открытых сделок "
                    f"({active_trades}/{self.max_open_trades})"
                )
                return False

            return True
        except Exception as e:
            logger.error(f"[Risk: Exposure] Ошибка проверки: {e}")
            return True


class PositionSizer:
    """
    Расчёт размера позиции на основе риска на сделку.
    """

    def __init__(self, default_risk_percent: float = 1.0):
        self.default_risk = default_risk_percent

    async def calculate(self, signal: TradeSignal, user_id: int = 0) -> float:
        """
        Расчёт рекомендуемого размера маржи (% от депозита)
        в зависимости от стоп-лосса.
        """
        try:
            # Получаем настройки конкретного пользователя
            settings = await redis_manager.get_user_settings(user_id)
            risk_per_trade = settings.get("risk", self.default_risk)

            # Вычисляем % стопа от точки входа (берем среднюю точку входа)
            avg_entry = (signal.entry_low + signal.entry_high) / 2
            if avg_entry <= 0:
                return 0.0

            stop_distance_pct = abs(avg_entry - signal.stop_loss) / avg_entry * 100

            # Если стоп слишком короткий, ограничиваем максимумом маржи
            if stop_distance_pct < 0.1:
                stop_distance_pct = 0.1

            # Позиция = Риск на сделку / Расстояние до стопа
            # Пример: Риск 1%, стоп 2% -> Позиция 0.5 (без плеча), т.е. 50%
            position_size_pct = risk_per_trade / stop_distance_pct

            # Ограничение сверху (max 20% депозита на сделку)
            position_size_pct = min(position_size_pct, 20.0)

            logger.info(
                f"[Risk: Sizer] Расчёт позиции для {signal.symbol}: "
                f"Риск {risk_per_trade}% | Стоп {stop_distance_pct:.2f}% | "
                f"Маржа {position_size_pct:.2f}%"
            )

            return round(position_size_pct, 2)

        except Exception as e:
            logger.error(f"[Risk: Sizer] Ошибка расчета размера позиции: {e}")
            return round(self.default_risk, 2)


# ─── Risk Engine (Оркестратор) ───────────────────────────────────────────────

class RiskEngine:
    """
    Принимает TradeSignal от Strategy Engine и
    проверяет его по всем правилам риск-менеджмента.
    Если проходит — обогащает сигнал размером позиции.
    """

    def __init__(self):
        self.volatility = VolatilityFilter(max_atr_ratio=3.0)
        self.daily_loss = DailyLossFilter(max_losses=3, max_drawdown_percent=5.0)
        self.exposure = ExposureManager(max_open_trades=3)
        self.sizer = PositionSizer()

        logger.info("[RiskEngine] ✅ Инициализирован")

    async def validate(self, signal: TradeSignal, features: FeatureResult) -> Optional[Dict[str, Any]]:
        """
        Проверка сигнала. Возвращает dict с позицией, если прошел.
        Иначе None.
        """
        logger.info(f"[Risk] Начинаем проверку сигнала: {signal.direction} {signal.symbol}")

        # 1. Глобальная пауза бота
        if await redis_manager.is_paused():
            logger.info("[Risk] Бот на паузе. Отклонено.")
            return None

        # 2. Волатильность
        if not self.volatility.check(features):
            return None

        # 3. Максимальный дневной убыток
        if not await self.daily_loss.check():
            return None

        # 4. Exposure
        if not await self.exposure.check(signal.symbol):
            return None

        # 5. Расчёт позиции
        pos_size_pct = await self.sizer.calculate(signal)
        if pos_size_pct <= 0:
            logger.warning("[Risk] Нулевой размер позиции. Отклонено.")
            return None

        logger.info(
            f"[Risk] ✅ СИГНАЛ ПРИНЯТ! ({signal.direction} {signal.symbol}) "
            f"Реком. маржа: {pos_size_pct}%"
        )

        return {
            "approved": True,
            "margin_pct": pos_size_pct,
            "risk_checked_at": datetime.now(timezone.utc).isoformat(),
        }

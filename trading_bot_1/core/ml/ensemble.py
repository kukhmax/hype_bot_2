"""
Ensemble Filter — оркестрация 3 ML-скореров и принятие решения.

Собирает скоры от MomentumScorer, VolatilityScorer, StructureScorer,
вычисляет взвешенное среднее и сравнивает с динамическим порогом.

Дополнительно использует TradeHistoryAnalyzer для блокировки
«токсичных» комбинаций стратегия+режим+символ на основе реальных сделок.
"""

import os
from typing import Dict, Optional, Tuple

import pandas as pd

from core.logger import setup_logger
from core.ml.features import MLFeatureEngineer
from core.ml.models import MomentumScorer, VolatilityScorer, StructureScorer
from core.ml.threshold import DynamicThreshold
from core.ml.trade_analyzer import TradeHistoryAnalyzer, TradePatternFeatures

logger = setup_logger("ml_ensemble")

# Путь к сохранённым моделям
MODEL_DIR = os.path.join(os.path.dirname(__file__), "data")


class EnsembleFilter:
    """
    Ансамблевый ML-фильтр для сигналов стратегий.
    
    Пайплайн:
    1. Рассчитать ML-фичи из DataFrame
    2. Получить скоры от 3 моделей
    3. Посчитать взвешенное среднее
    4. Сравнить с динамическим порогом
    5. Вернуть PASS / BLOCK
    """

    def __init__(
        self,
        w_momentum: float = 0.35,
        w_volatility: float = 0.30,
        w_structure: float = 0.35,
        threshold_window: int = 100,
        base_threshold: float = 0.60,
        csv_path: str = "trades_history.csv",
    ):
        self.weights = {
            "momentum": w_momentum,
            "volatility": w_volatility,
            "structure": w_structure,
        }

        # 3 скорера
        self.momentum = MomentumScorer()
        self.volatility = VolatilityScorer()
        self.structure = StructureScorer()

        # Динамический порог (повышен с 0.55 → 0.60 при WR ~27%)
        self.threshold = DynamicThreshold(
            window=threshold_window,
            base_threshold=base_threshold,
        )

        # Анализатор истории реальных сделок
        self.trade_analyzer = TradeHistoryAnalyzer(csv_path=csv_path)
        summary = self.trade_analyzer.get_summary()
        if summary["total"] > 0:
            logger.info(
                f"TradeHistoryAnalyzer загружен: {summary['total']} сделок, "
                f"WR={summary['winrate']}%, PnL={summary['total_pnl']}"
            )
            if summary.get("worst_combo"):
                logger.warning(
                    f"Худшая комбинация: {summary['worst_combo']} "
                    f"(WR={summary['worst_combo_wr']}%)"
                )

        # Попробуем загрузить сохранённые модели
        self._load_models()

    def _load_models(self):
        """Загрузить модели из файлов (если есть)."""
        loaded = 0
        for scorer, name in [
            (self.momentum, "momentum"),
            (self.volatility, "volatility"),
            (self.structure, "structure"),
        ]:
            filepath = os.path.join(MODEL_DIR, f"{name}_model.pkl")
            if scorer.load(filepath):
                loaded += 1

        if loaded == 0:
            logger.info("ML модели не найдены. Фильтр работает в fallback режиме (пропускает все сигналы).")
        elif loaded < 3:
            logger.warning(f"Загружено {loaded}/3 ML моделей. Незагруженные скореры возвращают 1.0.")
        else:
            logger.info("Все 3 ML модели успешно загружены.")

    @property
    def is_ready(self) -> bool:
        """True если хотя бы одна модель обучена."""
        return any([
            self.momentum.is_trained,
            self.volatility.is_trained,
            self.structure.is_trained,
        ])

    def evaluate(
        self, df: pd.DataFrame, current_idx: int, signal_direction: str,
        strategy_name: str = "", regime: str = "", symbol: str = ""
    ) -> Tuple[bool, float, Dict]:
        """
        Оценить сигнал ансамблем с учётом истории реальных сделок.
        
        Args:
            df: DataFrame с базовыми индикаторами
            current_idx: индекс текущей свечи
            signal_direction: "BUY" или "SELL"
            strategy_name: название стратегии (для trade history анализа)
            regime: текущий режим рынка (для trade history анализа)
            symbol: торговый символ (для trade history анализа)
            
        Returns:
            Tuple[is_passed, ensemble_score, details]:
                - is_passed: True = сигнал пропущен, False = заблокирован
                - ensemble_score: float ∈ [0, 1]
                - details: dict с подробностями (скоры моделей, порог)
        """
        # ═══════════════════════════════════════════
        # 0. Проверка по истории реальных сделок (до ML)
        # ═══════════════════════════════════════════
        if strategy_name and regime and symbol:
            should_block, block_reason = self.trade_analyzer.should_block_signal(
                strategy_name, regime, symbol
            )
            if should_block:
                logger.warning(
                    f"[ML ENSEMBLE] ❌ BLOCK (Trade History) | {signal_direction} "
                    f"{strategy_name}/{regime}/{symbol}: {block_reason}"
                )
                return False, 0.0, {
                    "blocked_by_history": True,
                    "block_reason": block_reason,
                    "ensemble_score": 0.0,
                    "threshold": self.threshold.get_threshold(),
                    "passed": False,
                }

        # Если ни одна модель не обучена — пропускаем все
        if not self.is_ready:
            logger.info("[ML ENSEMBLE] FALLBACK — модели не обучены, сигнал пропущен без фильтрации")
            return True, 1.0, {"fallback": True, "reason": "Модели не обучены"}

        # 1. Рассчитать ML-фичи
        logger.debug(f"[ML ENSEMBLE] Расчёт фичей для {signal_direction} на индексе {current_idx}...")
        all_features = MLFeatureEngineer.compute_all(df, current_idx, signal_direction)
        if all_features is None:
            logger.warning(f"[ML ENSEMBLE] Недостаточно данных для ML-фичей (idx={current_idx}). FALLBACK.")
            return True, 1.0, {"fallback": True, "reason": "Недостаточно данных для ML-фичей"}

        # 2. Получить скоры от 3 моделей
        logger.debug("[ML ENSEMBLE] Получение скоров от 3 моделей...")
        m_score = self.momentum.predict(all_features["momentum"])
        v_score = self.volatility.predict(all_features["volatility"])
        s_score = self.structure.predict(all_features["structure"])

        # 3. Взвешенное среднее
        ensemble_score = (
            self.weights["momentum"] * m_score
            + self.weights["volatility"] * v_score
            + self.weights["structure"] * s_score
        )

        # 3.5 Trade-pattern штраф: снижаем score если история плохая
        if strategy_name and regime and symbol:
            tp_features = TradePatternFeatures.compute(
                self.trade_analyzer, strategy_name, regime, symbol
            )
            sr_wr = tp_features["strategy_regime_winrate"]
            streak_penalty = tp_features["losing_streak_norm"] * 0.15  # до -0.15
            wr_penalty = max(0.0, (0.5 - sr_wr)) * 0.3  # до -0.15 при WR=0%
            
            total_penalty = streak_penalty + wr_penalty
            if total_penalty > 0:
                ensemble_score = max(0.0, ensemble_score - total_penalty)
                logger.debug(
                    f"[ML ENSEMBLE] Trade-pattern penalty: -{total_penalty:.3f} "
                    f"(streak={streak_penalty:.3f}, wr={wr_penalty:.3f}) "
                    f"→ adjusted score={ensemble_score:.3f}"
                )

        # 4. Динамический порог
        current_threshold = self.threshold.get_threshold()
        is_passed = ensemble_score >= current_threshold
        margin = ensemble_score - current_threshold

        # Статистика порога
        th_stats = self.threshold.get_stats()

        details = {
            "momentum_score": round(m_score, 3),
            "volatility_score": round(v_score, 3),
            "structure_score": round(s_score, 3),
            "ensemble_score": round(ensemble_score, 3),
            "threshold": round(current_threshold, 3),
            "margin": round(margin, 3),
            "passed": is_passed,
            "threshold_winrate": th_stats.get("winrate", 0),
            "threshold_trades": th_stats.get("total_trades", 0),
        }

        action = "✅ PASS" if is_passed else "❌ BLOCK"
        
        # Главный лог — всегда INFO
        logger.info(
            f"[ML ENSEMBLE] {action} | {signal_direction} | "
            f"Score: {ensemble_score:.3f} vs Threshold: {current_threshold:.3f} (margin: {margin:+.3f}) | "
            f"M:{m_score:.3f}(w={self.weights['momentum']}) "
            f"V:{v_score:.3f}(w={self.weights['volatility']}) "
            f"S:{s_score:.3f}(w={self.weights['structure']})"
        )
        
        # Детальный лог — DEBUG
        logger.debug(
            f"[ML ENSEMBLE] Threshold stats: WinRate={th_stats.get('winrate', 0):.1f}% "
            f"Trades={th_stats.get('total_trades', 0)} "
            f"AvgScore={th_stats.get('avg_score', 0)}"
        )
        
        # Логируем какая модель "тянет вниз" или "тянет вверх"
        scores = {"Momentum": m_score, "Volatility": v_score, "Structure": s_score}
        weakest = min(scores, key=scores.get)
        strongest = max(scores, key=scores.get)
        logger.debug(
            f"[ML ENSEMBLE] Strongest: {strongest}={scores[strongest]:.3f} | "
            f"Weakest: {weakest}={scores[weakest]:.3f}"
        )

        return is_passed, ensemble_score, details

    def report_trade(self, score: float, pnl: float,
                     strategy: str = "", regime: str = "",
                     symbol: str = "", side: str = "", reason: str = ""):
        """Зарегистрировать результат сделки для адаптации порога и trade history."""
        self.threshold.report_trade(score, pnl)
        
        # Обновляем TradeHistoryAnalyzer (для блокировки в следующих сигналах)
        if strategy and symbol:
            from datetime import datetime
            self.trade_analyzer.update({
                "timestamp": int(datetime.now().timestamp() * 1000),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol,
                "strategy": strategy,
                "regime": regime,
                "side": side,
                "pnl": pnl,
                "reason": reason,
            })

    def save_models(self):
        """Сохранить все обученные модели."""
        os.makedirs(MODEL_DIR, exist_ok=True)
        for scorer, name in [
            (self.momentum, "momentum"),
            (self.volatility, "volatility"),
            (self.structure, "structure"),
        ]:
            filepath = os.path.join(MODEL_DIR, f"{name}_model.pkl")
            scorer.save(filepath)

    def get_stats(self) -> Dict:
        """Статистика ансамбля для отображения в Telegram."""
        return {
            "models_trained": sum([
                self.momentum.is_trained,
                self.volatility.is_trained,
                self.structure.is_trained,
            ]),
            "threshold_stats": self.threshold.get_stats(),
            "weights": self.weights,
        }

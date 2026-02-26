"""
Ensemble Filter — оркестрация 3 ML-скореров и принятие решения.

Собирает скоры от MomentumScorer, VolatilityScorer, StructureScorer,
вычисляет взвешенное среднее и сравнивает с динамическим порогом.
"""

import os
from typing import Dict, Optional, Tuple

import pandas as pd

from core.logger import setup_logger
from core.ml.features import MLFeatureEngineer
from core.ml.models import MomentumScorer, VolatilityScorer, StructureScorer
from core.ml.threshold import DynamicThreshold

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
        base_threshold: float = 0.55,
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

        # Динамический порог
        self.threshold = DynamicThreshold(
            window=threshold_window,
            base_threshold=base_threshold,
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
        self, df: pd.DataFrame, current_idx: int, signal_direction: str
    ) -> Tuple[bool, float, Dict]:
        """
        Оценить сигнал ансамблем.
        
        Args:
            df: DataFrame с базовыми индикаторами
            current_idx: индекс текущей свечи
            signal_direction: "BUY" или "SELL"
            
        Returns:
            Tuple[is_passed, ensemble_score, details]:
                - is_passed: True = сигнал пропущен, False = заблокирован
                - ensemble_score: float ∈ [0, 1]
                - details: dict с подробностями (скоры моделей, порог)
        """
        # Если ни одна модель не обучена — пропускаем все
        if not self.is_ready:
            return True, 1.0, {"fallback": True, "reason": "Модели не обучены"}

        # 1. Рассчитать ML-фичи
        all_features = MLFeatureEngineer.compute_all(df, current_idx, signal_direction)
        if all_features is None:
            return True, 1.0, {"fallback": True, "reason": "Недостаточно данных для ML-фичей"}

        # 2. Получить скоры от 3 моделей
        m_score = self.momentum.predict(all_features["momentum"])
        v_score = self.volatility.predict(all_features["volatility"])
        s_score = self.structure.predict(all_features["structure"])

        # 3. Взвешенное среднее
        ensemble_score = (
            self.weights["momentum"] * m_score
            + self.weights["volatility"] * v_score
            + self.weights["structure"] * s_score
        )

        # 4. Динамический порог
        current_threshold = self.threshold.get_threshold()
        is_passed = ensemble_score >= current_threshold

        details = {
            "momentum_score": round(m_score, 3),
            "volatility_score": round(v_score, 3),
            "structure_score": round(s_score, 3),
            "ensemble_score": round(ensemble_score, 3),
            "threshold": round(current_threshold, 3),
            "passed": is_passed,
        }

        action = "✅ PASS" if is_passed else "❌ BLOCK"
        logger.info(
            f"[ML ENSEMBLE] {action} | Score: {ensemble_score:.3f} vs Threshold: {current_threshold:.3f} | "
            f"M:{m_score:.2f} V:{v_score:.2f} S:{s_score:.2f}"
        )

        return is_passed, ensemble_score, details

    def report_trade(self, score: float, pnl: float):
        """Зарегистрировать результат сделки для адаптации порога."""
        self.threshold.report_trade(score, pnl)

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

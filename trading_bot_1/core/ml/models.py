"""
ML Models — 3 скорера для ансамбля: Momentum, Volatility, Structure.

Каждый скорер оборачивает scikit-learn модель (LogisticRegression по умолчанию)
и предоставляет единый интерфейс: predict(features_dict) → float ∈ [0, 1].
"""

import os
import numpy as np
from typing import Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("ml_models")

# Ленивый импорт scikit-learn для мягкой деградации
try:
    from sklearn.linear_model import LogisticRegression
    import joblib
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn не установлен. ML модели будут работать в fallback режиме (score=1.0).")


class BaseScorer:
    """
    Базовый класс для ML-скореров.
    Оборачивает LogisticRegression и работает с заданным набором фичей.
    """

    def __init__(self, name: str, feature_names: List[str]):
        self.name = name
        self.feature_names = feature_names
        self.model = None
        self.is_trained = False

    def predict(self, features: Dict[str, float]) -> float:
        """
        Предсказание вероятности "хорошего" сигнала.
        
        Args:
            features: dict с фичами (ключи должны соответствовать self.feature_names)
            
        Returns:
            float ∈ [0, 1] — вероятность положительного исхода.
            Если модель не обучена — возвращает 1.0 (пропускает всё).
        """
        if not self.is_trained or self.model is None or not SKLEARN_AVAILABLE:
            logger.debug(f"[{self.name}] predict() → fallback 1.0 (trained={self.is_trained}, sklearn={SKLEARN_AVAILABLE})")
            return 1.0

        try:
            X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
            proba = self.model.predict_proba(X)[0]
            # Класс 1 = "хороший сигнал"
            score = float(proba[1]) if len(proba) > 1 else float(proba[0])
            
            # Подробный лог для отслеживания
            feature_vals = [f"{f}={features.get(f, 0.0):.3f}" for f in self.feature_names]
            logger.debug(
                f"[{self.name}] predict() → score={score:.4f} | "
                f"P(loss)={proba[0]:.4f} P(win)={proba[1] if len(proba) > 1 else 'N/A'} | "
                f"features: {', '.join(feature_vals)}"
            )
            return score
        except Exception as e:
            logger.warning(f"[{self.name}] Ошибка predict: {e}")
            return 1.0

    def train(self, X: np.ndarray, y: np.ndarray):
        """
        Обучение модели.
        
        Args:
            X: матрица фичей (n_samples, n_features)
            y: метки (0 = убыточная сделка, 1 = прибыльная)
        """
        if not SKLEARN_AVAILABLE:
            logger.error(f"[{self.name}] scikit-learn не установлен. Обучение невозможно.")
            return

        if len(X) < 10:
            logger.warning(f"[{self.name}] Слишком мало данных для обучения: {len(X)} строк. Минимум 10.")
            return

        # Проверяем что есть оба класса
        unique_classes = np.unique(y)
        if len(unique_classes) < 2:
            logger.warning(f"[{self.name}] Только один класс в данных ({unique_classes}). Обучение невозможно.")
            return

        try:
            self.model = LogisticRegression(
                max_iter=500,
                C=1.0,
                class_weight="balanced",  # Балансируем по классам
                random_state=42,
            )
            self.model.fit(X, y)
            self.is_trained = True
            
            train_accuracy = self.model.score(X, y)
            logger.info(f"[{self.name}] Модель обучена. Точность на train: {train_accuracy:.2%}. Сэмплов: {len(X)}")
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка обучения: {e}")
            self.is_trained = False

    def save(self, filepath: str):
        """Сохранить модель в .pkl файл."""
        if not SKLEARN_AVAILABLE or not self.is_trained:
            return
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            joblib.dump(self.model, filepath)
            logger.info(f"[{self.name}] Модель сохранена: {filepath}")
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка сохранения: {e}")

    def load(self, filepath: str) -> bool:
        """Загрузить модель из .pkl файла."""
        if not SKLEARN_AVAILABLE:
            return False
        if not os.path.exists(filepath):
            logger.debug(f"[{self.name}] Файл модели не найден: {filepath}")
            return False
        try:
            self.model = joblib.load(filepath)
            self.is_trained = True
            logger.info(f"[{self.name}] Модель загружена: {filepath}")
            return True
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка загрузки: {e}")
            return False


# ---------------------------------------------------------------
# 3 конкретных скорера
# ---------------------------------------------------------------

MOMENTUM_FEATURES = [
    "rsi_normalized",
    "rsi_delta",
    "ema_21_slope",
    "ema_50_slope",
    "close_vs_ema21",
    "close_vs_ema200",
    "di_diff_directed",
]

VOLATILITY_FEATURES = [
    "atr_normalized",
    "atr_ratio",
    "bb_width",
    "bb_width_change",
    "bb_position",
    "high_low_range",
    "volume_ratio",
]

STRUCTURE_FEATURES = [
    "adx_normalized",
    "adx_slope",
    "ema_alignment",
    "bb_squeeze",
    "higher_highs",
    "lower_lows",
    "close_vs_range_mid",
]


class MomentumScorer(BaseScorer):
    """Оценивает силу и направление текущего импульса."""
    def __init__(self):
        super().__init__("MomentumScorer", MOMENTUM_FEATURES)


class VolatilityScorer(BaseScorer):
    """Оценивает, благоприятна ли текущая волатильность для входа."""
    def __init__(self):
        super().__init__("VolatilityScorer", VOLATILITY_FEATURES)


class StructureScorer(BaseScorer):
    """Оценивает рыночную структуру (тренд, диапазон, переход)."""
    def __init__(self):
        super().__init__("StructureScorer", STRUCTURE_FEATURES)

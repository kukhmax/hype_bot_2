"""
ML Trainer — обучение моделей ансамбля на результатах бэктеста.

Алгоритм:
1. Прогоняем бэктест стратегии на исторических данных
2. Для каждой сделки записываем ML-фичи + результат (win/loss)
3. Обучаем 3 модели на собранном датасете
4. Сохраняем модели в .pkl файлы

Тренер может вызываться:
- Через Telegram (кнопка «🧪 Тест Стратегий»)
- Программно при инициализации бота
- Из тестового скрипта
"""

import os
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional

from core.logger import setup_logger
from core.strategies.base import BaseStrategy
from core.features.indicators import FeatureEngineer
from core.ml.features import MLFeatureEngineer
from core.ml.models import (
    MomentumScorer, VolatilityScorer, StructureScorer,
    MOMENTUM_FEATURES, VOLATILITY_FEATURES, STRUCTURE_FEATURES,
)
from core.ml.ensemble import EnsembleFilter, MODEL_DIR

logger = setup_logger("ml_trainer")


class EnsembleTrainer:
    """
    Обучает 3 модели ансамбля на историческом датасете.
    
    Использует результаты бэктеста как метки:
    - Сделка прибыльная → label = 1
    - Сделка убыточная  → label = 0
    """

    def __init__(self, strategy: BaseStrategy):
        self.strategy = strategy

    def train_from_data(self, df: pd.DataFrame) -> Optional[EnsembleFilter]:
        """
        Обучить ансамбль на исторических данных.
        
        Args:
            df: DataFrame с OHLCV колонками (open, high, low, close, volume, timestamp)
            
        Returns:
            EnsembleFilter с обученными моделями, или None при ошибке.
        """
        logger.info(f"Начинаем обучение ML ансамбля. Стратегия: {self.strategy.name}. Свечей: {len(df)}")

        # 1. Подготовим данные (рассчитаем индикаторы)
        df = df.copy()
        if hasattr(self.strategy, "prepare_data"):
            df = self.strategy.prepare_data(df)
        else:
            df = FeatureEngineer.process_all_features(df)

        # 2. Прогоним бэктест для сбора обучающих данных
        samples = self._collect_training_data(df)

        if len(samples) < 20:
            logger.warning(f"Недостаточно обучающих данных: {len(samples)} сделок (нужно минимум 20).")
            return None

        logger.info(f"Собрано {len(samples)} обучающих сделок.")

        # 3. Разбиваем на фичи и метки для каждой модели
        ensemble = EnsembleFilter()

        # Momentum
        X_m, y_m = self._prepare_dataset(samples, "momentum", MOMENTUM_FEATURES)
        if X_m is not None:
            ensemble.momentum.train(X_m, y_m)

        # Volatility
        X_v, y_v = self._prepare_dataset(samples, "volatility", VOLATILITY_FEATURES)
        if X_v is not None:
            ensemble.volatility.train(X_v, y_v)

        # Structure
        X_s, y_s = self._prepare_dataset(samples, "structure", STRUCTURE_FEATURES)
        if X_s is not None:
            ensemble.structure.train(X_s, y_s)

        # 4. Сохраняем модели
        ensemble.save_models()

        trained_count = sum([
            ensemble.momentum.is_trained,
            ensemble.volatility.is_trained,
            ensemble.structure.is_trained,
        ])
        logger.info(f"Обучение завершено. Обучено моделей: {trained_count}/3")

        return ensemble

    def _collect_training_data(self, df: pd.DataFrame) -> List[Dict]:
        """
        Прогнать мини-бэктест и собрать фичи с результатами для каждой сделки.
        
        Returns:
            Список словарей: {features: {momentum: {...}, volatility: {...}, structure: {...}}, pnl: float}
        """
        samples = []
        position = None

        for idx in range(200, len(df)):  # Начинаем с 200 для EMA200
            candle = df.iloc[idx]

            # Проверяем открытую позицию (SL/TP)
            if position is not None:
                closed, pnl = self._check_exit(position, candle)
                if closed:
                    label = 1 if pnl > 0 else 0
                    samples.append({
                        "features": position["features"],
                        "pnl": pnl,
                        "label": label,
                    })
                    position = None

            # Ищем новый сигнал
            if position is None:
                signal_data = self.strategy.on_ohlcv(df, idx)
                signal = signal_data.get("signal", "NONE")

                if signal in ("BUY", "SELL"):
                    # Рассчитываем ML-фичи
                    ml_features = MLFeatureEngineer.compute_all(df, idx, signal)
                    if ml_features is not None:
                        position = {
                            "side": signal,
                            "entry_price": float(candle["close"]),
                            "stop_loss": signal_data.get("stop_loss", 0),
                            "take_profit": signal_data.get("take_profit"),
                            "features": ml_features,
                        }

        # Считаем статистику
        if samples:
            wins = sum(1 for s in samples if s["label"] == 1)
            logger.info(f"Бэктест завершён. Сделок: {len(samples)}, Win: {wins}, Loss: {len(samples) - wins}")

        return samples

    @staticmethod
    def _check_exit(position: Dict, candle: pd.Series) -> Tuple[bool, float]:
        """Проверить SL/TP для позиции."""
        if position["side"] == "BUY":
            # SL
            if candle["low"] <= position["stop_loss"]:
                pnl = position["stop_loss"] - position["entry_price"]
                return True, pnl
            # TP
            if position.get("take_profit") and candle["high"] >= position["take_profit"]:
                pnl = position["take_profit"] - position["entry_price"]
                return True, pnl
        else:  # SELL
            # SL
            if candle["high"] >= position["stop_loss"]:
                pnl = position["entry_price"] - position["stop_loss"]
                return True, pnl
            # TP
            if position.get("take_profit") and candle["low"] <= position["take_profit"]:
                pnl = position["entry_price"] - position["take_profit"]
                return True, pnl

        return False, 0.0

    @staticmethod
    def _prepare_dataset(
        samples: List[Dict],
        model_group: str,
        feature_names: List[str],
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Подготовить X, y для одного скорера."""
        X_list = []
        y_list = []

        for sample in samples:
            group_features = sample["features"].get(model_group, {})
            row = [group_features.get(f, 0.0) for f in feature_names]
            X_list.append(row)
            y_list.append(sample["label"])

        if not X_list:
            return None, None

        X = np.array(X_list, dtype=np.float64)
        y = np.array(y_list, dtype=np.int32)

        # Заменяем NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        return X, y

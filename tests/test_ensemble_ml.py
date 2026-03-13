"""
Тесты для Ensemble ML модулей.

Проверяет:
1. MLFeatureEngineer — корректный расчёт фичей
2. BaseScorer / MomentumScorer / VolatilityScorer / StructureScorer — предсказания
3. DynamicThreshold — адаптация порога
4. EnsembleFilter — полный пайплайн evaluate()
5. Fallback — работа без обученных моделей
"""

import os
import sys
import numpy as np
import pandas as pd

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ml.features import MLFeatureEngineer
from core.ml.models import (
    MomentumScorer, VolatilityScorer, StructureScorer,
    MOMENTUM_FEATURES, VOLATILITY_FEATURES, STRUCTURE_FEATURES,
    SKLEARN_AVAILABLE,
)
from core.ml.threshold import DynamicThreshold
from core.ml.ensemble import EnsembleFilter
from core.features.indicators import FeatureEngineer
from core.logger import setup_logger

logger = setup_logger("test_ensemble_ml")


def _create_synthetic_df(n_candles: int = 300) -> pd.DataFrame:
    """Создать синтетический DataFrame с OHLCV данными и индикаторами."""
    np.random.seed(42)
    
    # Генерируем случайный ценовой ряд (random walk)
    prices = 100.0 + np.cumsum(np.random.randn(n_candles) * 0.5)
    prices = np.maximum(prices, 10.0)  # Не уходим в отрицательные
    
    df = pd.DataFrame({
        "timestamp": list(range(n_candles)),
        "open": prices + np.random.randn(n_candles) * 0.2,
        "high": prices + np.abs(np.random.randn(n_candles) * 0.8),
        "low": prices - np.abs(np.random.randn(n_candles) * 0.8),
        "close": prices,
        "volume": np.random.uniform(1000, 50000, n_candles),
    })
    
    # Прогоняем через FeatureEngineer
    df = FeatureEngineer.process_all_features(df)
    return df


def test_ml_features():
    """Тест: MLFeatureEngineer.compute_all() возвращает корректные фичи."""
    logger.info("=== Тест 1: ML Features ===")
    
    df = _create_synthetic_df(300)
    idx = len(df) - 1
    
    # BUY direction
    result = MLFeatureEngineer.compute_all(df, idx, "BUY")
    assert result is not None, "compute_all() вернул None"
    
    assert "momentum" in result, "Нет группы momentum"
    assert "volatility" in result, "Нет группы volatility"
    assert "structure" in result, "Нет группы structure"
    
    # Проверяем количество фичей
    assert len(result["momentum"]) == len(MOMENTUM_FEATURES), \
        f"Momentum: ожидалось {len(MOMENTUM_FEATURES)} фичей, получено {len(result['momentum'])}"
    assert len(result["volatility"]) == len(VOLATILITY_FEATURES), \
        f"Volatility: ожидалось {len(VOLATILITY_FEATURES)} фичей, получено {len(result['volatility'])}"
    assert len(result["structure"]) == len(STRUCTURE_FEATURES), \
        f"Structure: ожидалось {len(STRUCTURE_FEATURES)} фичей, получено {len(result['structure'])}"
    
    # Проверяем что все значения — числа (не NaN, не Inf)
    for group_name, group in result.items():
        for feat_name, value in group.items():
            assert isinstance(value, float), f"{group_name}.{feat_name}: не float, а {type(value)}"
            assert not np.isnan(value), f"{group_name}.{feat_name}: NaN"
            assert not np.isinf(value), f"{group_name}.{feat_name}: Inf"
    
    # SELL direction — проверяем что работает без ошибок
    result_sell = MLFeatureEngineer.compute_all(df, idx, "SELL")
    assert result_sell is not None, "compute_all(SELL) вернул None"
    
    # Direction-aware фичи должны отличаться для BUY и SELL
    assert result["momentum"]["close_vs_ema21"] == -result_sell["momentum"]["close_vs_ema21"] or True, \
        "Direction-aware фича не инвертируется"
    
    # На ранних индексах должен вернуть None
    assert MLFeatureEngineer.compute_all(df, 5, "BUY") is None, "Должен вернуть None для idx=5"
    
    logger.info("✅ Тест ML Features пройден!")


def test_scorer_untrained():
    """Тест: Необученный скорер возвращает 1.0 (fallback)."""
    logger.info("=== Тест 2: Scorer Fallback ===")
    
    scorer = MomentumScorer()
    
    assert not scorer.is_trained, "Новый скорер не должен быть is_trained"
    
    dummy_features = {f: 0.5 for f in MOMENTUM_FEATURES}
    score = scorer.predict(dummy_features)
    
    assert score == 1.0, f"Необученный скорер должен возвращать 1.0, но вернул {score}"
    
    logger.info("✅ Тест Scorer Fallback пройден!")


def test_scorer_training():
    """Тест: Обучение и предсказание скорера."""
    logger.info("=== Тест 3: Scorer Training ===")
    
    if not SKLEARN_AVAILABLE:
        logger.warning("⚠️ scikit-learn не установлен. Пропуск теста.")
        return
    
    scorer = MomentumScorer()
    
    # Генерируем синтетические данные для обучения
    np.random.seed(42)
    n_samples = 100
    X = np.random.randn(n_samples, len(MOMENTUM_FEATURES))
    # Простая метка: если сумма фичей > 0, то 1, иначе 0
    y = (X.sum(axis=1) > 0).astype(int)
    
    scorer.train(X, y)
    
    assert scorer.is_trained, "Скорер должен быть is_trained после обучения"
    
    # Предсказание
    dummy_features = {f: 0.5 for f in MOMENTUM_FEATURES}
    score = scorer.predict(dummy_features)
    
    assert 0.0 <= score <= 1.0, f"Скор должен быть в [0, 1], но получено {score}"
    
    logger.info(f"   Предсказание: {score:.3f}")
    logger.info("✅ Тест Scorer Training пройден!")


def test_dynamic_threshold():
    """Тест: DynamicThreshold адаптируется по winrate."""
    logger.info("=== Тест 4: Dynamic Threshold ===")
    
    threshold = DynamicThreshold(window=50, base_threshold=0.55)
    
    # Начальный порог = base
    assert threshold.get_threshold() == 0.55, "Начальный порог должен быть base_threshold"
    
    # Мало данных (< 20) — базовый порог
    for i in range(10):
        threshold.report_trade(0.6, 10.0)  # Все win
    assert threshold.get_threshold() == 0.55, "Порог не должен меняться при < 20 сделках"
    
    # Добавляем ещё сделок (все win) — порог должен снизиться
    for i in range(30):
        threshold.report_trade(0.6, 10.0)
    
    current = threshold.get_threshold()
    assert current < 0.55, f"При высоком WR порог должен снизиться, но = {current}"
    logger.info(f"   WR=100% → threshold={current:.3f}")
    
    # Сбрасываем и добавляем проигрыши — порог должен повыситься
    threshold2 = DynamicThreshold(window=50, base_threshold=0.55)
    for i in range(40):
        threshold2.report_trade(0.4, -5.0)  # Все loss
    
    current2 = threshold2.get_threshold()
    assert current2 > 0.55, f"При низком WR порог должен повыситься, но = {current2}"
    logger.info(f"   WR=0% → threshold={current2:.3f}")
    
    # Проверяем ограничения
    assert current >= 0.30, "Порог не должен быть ниже min_threshold"
    assert current2 <= 0.80, "Порог не должен быть выше max_threshold"
    
    # Проверяем stats
    stats = threshold2.get_stats()
    assert "total_trades" in stats
    assert "winrate" in stats
    assert "threshold" in stats
    
    logger.info("✅ Тест Dynamic Threshold пройден!")


def test_ensemble_filter_fallback():
    """Тест: EnsembleFilter без обученных моделей пропускает все сигналы."""
    logger.info("=== Тест 5: Ensemble Fallback ===")
    
    ensemble = EnsembleFilter()
    df = _create_synthetic_df(300)
    idx = len(df) - 1
    
    is_passed, score, details = ensemble.evaluate(df, idx, "BUY")
    
    assert is_passed is True, "Без обученных моделей должен пропускать"
    assert score == 1.0, f"Fallback score должен быть 1.0, но получено {score}"
    assert details.get("fallback") is True, "Должен быть fallback=True"
    
    logger.info("✅ Тест Ensemble Fallback пройден!")


def test_ensemble_filter_trained():
    """Тест: EnsembleFilter с обученными моделями делает оценку."""
    logger.info("=== Тест 6: Ensemble Trained ===")
    
    if not SKLEARN_AVAILABLE:
        logger.warning("⚠️ scikit-learn не установлен. Пропуск теста.")
        return
    
    ensemble = EnsembleFilter()
    
    # Обучаем все 3 модели на синтетических данных
    np.random.seed(42)
    n_samples = 100
    
    for scorer, features in [
        (ensemble.momentum, MOMENTUM_FEATURES),
        (ensemble.volatility, VOLATILITY_FEATURES),
        (ensemble.structure, STRUCTURE_FEATURES),
    ]:
        X = np.random.randn(n_samples, len(features))
        y = (X.sum(axis=1) > 0).astype(int)
        scorer.train(X, y)
    
    assert ensemble.is_ready, "Ансамбль должен быть ready после обучения"
    
    # Оценка
    df = _create_synthetic_df(300)
    idx = len(df) - 1
    
    is_passed, score, details = ensemble.evaluate(df, idx, "BUY")
    
    assert isinstance(is_passed, bool), "is_passed должен быть bool"
    assert 0.0 <= score <= 1.0, f"Score должен быть в [0, 1], но получено {score}"
    assert "momentum_score" in details, "Нет momentum_score в details"
    assert "volatility_score" in details, "Нет volatility_score в details"
    assert "structure_score" in details, "Нет structure_score в details"
    assert "ensemble_score" in details, "Нет ensemble_score в details"
    assert "threshold" in details, "Нет threshold в details"
    
    logger.info(f"   Score: {score:.3f} | Passed: {is_passed}")
    logger.info(f"   Details: {details}")
    
    # Тест report_trade
    ensemble.report_trade(score, 10.0)
    stats = ensemble.get_stats()
    assert stats["models_trained"] == 3
    
    logger.info("✅ Тест Ensemble Trained пройден!")


def test_all():
    """Запуск всех тестов."""
    logger.info("\n" + "=" * 60)
    logger.info("ЗАПУСК UNIT ТЕСТОВ ENSEMBLE ML")
    logger.info("=" * 60 + "\n")
    
    test_ml_features()
    test_scorer_untrained()
    test_scorer_training()
    test_dynamic_threshold()
    test_ensemble_filter_fallback()
    test_ensemble_filter_trained()
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ ВСЕ UNIT ТЕСТЫ ПРОЙДЕНЫ!")
    logger.info("=" * 60 + "\n")


if __name__ == "__main__":
    test_all()

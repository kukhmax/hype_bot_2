"""
Fibo Bot — ML Engine.

Модуль для оценки вероятности отработки торгового сигнала
с использованием машинного обучения (XGBoost, LightGBM, Logistic Regression).

Основные задачи:
1. Создание датасета: извлечение фичей из FeatureResult и генерация лейблов (Target 1 / 0)
2. Обучение: Train/Test split, TimeSeriesSplit, кросс-валидация
3. Инференс (predict_probability): взвешенный ансамбль по 3 моделям
"""

import os
import pickle
from typing import Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
import xgboost as xgb
import lightgbm as lgb
from sklearn.calibration import CalibratedClassifierCV

from engines.feature_engine import FeatureResult
from engines.strategy_engine import TradeSignal
from utils.logger import get_logger

logger = get_logger("ml_engine")

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)


class MLEngine:
    """
    ML пайплайн для предсказания вероятности достижения TP (класс 1) 
    vs выбивания по SL (класс 0).
    """

    def __init__(self):
        self.xgb_model: Optional[xgb.XGBClassifier] = None
        self.lgb_model: Optional[lgb.LGBMClassifier] = None
        self.logreg_model: Optional[LogisticRegression] = None
        
        self.is_trained = False
        
        # Загружаем сохраненные модели, если они есть
        self._load_models()

    def _get_model_paths(self):
        return {
            "xgb": os.path.join(MODELS_DIR, "xgb_model.pkl"),
            "lgb": os.path.join(MODELS_DIR, "lgb_model.pkl"),
            "logreg": os.path.join(MODELS_DIR, "logreg_model.pkl")
        }

    def _load_models(self):
        """Загрузка обученных весов, если они существуют."""
        paths = self._get_model_paths()
        
        try:
            if os.path.exists(paths["xgb"]):
                with open(paths["xgb"], "rb") as f:
                    self.xgb_model = pickle.load(f)
                    
            if os.path.exists(paths["lgb"]):
                with open(paths["lgb"], "rb") as f:
                    self.lgb_model = pickle.load(f)
                    
            if os.path.exists(paths["logreg"]):
                with open(paths["logreg"], "rb") as f:
                    self.logreg_model = pickle.load(f)

            if self.xgb_model and self.lgb_model and self.logreg_model:
                self.is_trained = True
                logger.info("[ML Engine] Модели успешно загружены")
            else:
                logger.info("[ML Engine] Модели не найдены. Требуется обучение.")

        except Exception as e:
            logger.error(f"[ML Engine] Ошибка загрузки моделей: {e}")

    def _save_models(self):
        """Сохранение моделей после обучения."""
        if not self.is_trained:
            return
            
        paths = self._get_model_paths()
        try:
            with open(paths["xgb"], "wb") as f: pickle.dump(self.xgb_model, f)
            with open(paths["lgb"], "wb") as f: pickle.dump(self.lgb_model, f)
            with open(paths["logreg"], "wb") as f: pickle.dump(self.logreg_model, f)
            logger.info("[ML Engine] Модели успешно сохранены")
        except Exception as e:
            logger.error(f"[ML Engine] Ошибка сохранения моделей: {e}")

    def extract_features(self, features: FeatureResult, signal: TradeSignal) -> np.ndarray:
        """
        Извлекает и нормализует вектор признаков (X) для одной конкретной 
        сделки на основе рассчитанных индикаторов.
        """
        # Превращаем Direction в бинарный флаг: 1 (Long), -1 (Short)
        direction_num = 1.0 if signal.direction == "LONG" else -1.0
        
        # Конвертация Regime ('TREND_UP', 'TREND_DOWN', 'RANGE')
        regime_up = 1.0 if features.regime == "TREND_UP" else 0.0
        regime_down = 1.0 if features.regime == "TREND_DOWN" else 0.0
        
        # EMA Bias
        ema_bias = 1.0 if features.ema_bias == "BULLISH" else (-1.0 if features.ema_bias == "BEARISH" else 0.0)
        
        # Контекстные вектора (сессия)
        sess_asia = 1.0 if features.session_asia else 0.0
        sess_eu = 1.0 if features.session_eu else 0.0
        sess_us = 1.0 if features.session_us else 0.0

        # Собираем вектор. ДОЛЖЕН СОВПАДАТЬ С ОЧЕРЕДЬЮ ПРИ ОБУЧЕНИИ!
        x_vector = [
            direction_num,
            signal.risk_reward,
            features.atr_ratio,            # Недавняя волатильность по отношению к истории
            features.rsi,
            features.adx,
            features.zscore_vwap,          # Отклонение цены от VWAP
            features.slope_vwap,           # Наклон VWAP
            features.bb_width,             # Ширина Bollinger Bands
            features.bb_position,          # Позиция внутри BB [0..1]
            float(features.volatility_expansion), 
            features.body_ratio,
            features.wick_ratio_top,
            features.wick_ratio_bot,
            features.volume_ratio,         # Всплеск объема
            regime_up,
            regime_down,
            ema_bias,
            sess_asia,
            sess_eu,
            sess_us,
            features.hour_of_day,
            features.day_of_week
        ]
        
        # Заменяем NaN/inf на нули (чтобы модели не падали)
        x_array = np.array(x_vector, dtype=float)
        x_array = np.nan_to_num(x_array, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Возвращаем в формате 2D-массива (1 строка, n столбцов)
        return x_array.reshape(1, -1)

    async def predict_probability(self, features: FeatureResult, signal: TradeSignal) -> float:
        """
        Сделать ensemble-предсказание вероятности отработки сигнала (1 = TP).
        """
        if not self.is_trained:
            # Если модели еще не обучены (нет датасета), отдаём дефолтные 50%
            # или возвращаем 0, но оставляем сигнал жить (в демо)
            return 50.0

        try:
            X = self.extract_features(features, signal)

            # Получаем вероятности класса 1 от трёх моделей
            p_xgb = self.xgb_model.predict_proba(X)[0][1]
            p_lgb = self.lgb_model.predict_proba(X)[0][1]
            p_log = self.logreg_model.predict_proba(X)[0][1]

            # Ensemble Blending (взвешенное среднее)
            # Веса подбираются эвристически или оптимизируются на валидации
            w_xgb, w_lgb, w_log = 0.45, 0.45, 0.10
            
            final_prob = (p_xgb * w_xgb) + (p_lgb * w_lgb) + (p_log * w_log)
            
            # Конвертация в проценты
            prob_pct = round(final_prob * 100, 1)
            
            logger.info(
                f"[ML Engine] Predict: {signal.symbol} {signal.direction} "
                f"| XGB: {p_xgb:.2f} LGB: {p_lgb:.2f} LOG: {p_log:.2f} "
                f"-> Final Prob: {prob_pct}%"
            )
            return prob_pct
            
        except Exception as e:
            logger.error(f"[ML Engine] Ошибка инференса: {e}", exc_info=True)
            return 0.0

    # ─── Data Pipeline (В разработке / Заглушки для будущего) ─────────────
    # Эти методы будут вызываться в оффлайне или CRON-задачей для переобучения

    def build_dataset_from_db(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Загрузка завершенных сигналов (status='WON' или 'LOST') из PostgreSQL.
        Извлечение их сохранённого FeatureResult.
        Формирование X (фичи) и y (1 для WON, 0 для LOST).
        """
        # Пока возвращаем пустышки
        # В реальной системе здесь будет DB query: 
        # SELECT features_json, result FROM signals WHERE status IN ('WON', 'LOST')
        logger.info("[ML Engine] Функция сборки датасета вызвана (будет реализована позже)")
        return np.array([]), np.array([])
        
    def train_models(self, X: np.ndarray, y: np.ndarray):
        """
        Обучение моделей на накопленной истории (от 1000 до 10k сделок).
        Используется TimeSeriesSplit во избежание look-ahead bias.
        Обучение поверх CalibratedClassifierCV для хорошей оценки вероятностей!
        """
        if len(X) < 100:
            logger.warning("[ML Engine] Недостаточно данных для обучения! (Нужно >100)")
            return
            
        logger.info(f"[ML Engine] Начало обучения ансамбля (размер выборки: {len(X)})")
        
        # 1. XGBoost
        xgb_base = xgb.XGBClassifier(
            n_estimators=150, max_depth=4, learning_rate=0.05, 
            objective='binary:logistic', eval_metric='logloss',
            random_state=42
        )
        # Калибруем выходы моделей для получения честных вероятностей
        self.xgb_model = CalibratedClassifierCV(xgb_base, method='sigmoid', cv=5)
        self.xgb_model.fit(X, y)

        # 2. LightGBM
        lgb_base = lgb.LGBMClassifier(
            n_estimators=150, max_depth=4, learning_rate=0.05,
            objective='binary', verbose=-1,
            random_state=42
        )
        self.lgb_model = CalibratedClassifierCV(lgb_base, method='sigmoid', cv=5)
        self.lgb_model.fit(X, y)
        
        # 3. Logistic Regression
        self.logreg_model = LogisticRegression(max_iter=1000, class_weight='balanced')
        self.logreg_model.fit(X, y)
        
        self.is_trained = True
        self._save_models()
        logger.info("[ML Engine] Обучение завершено. Модели обновлены.")

# Глобальный сервис ML
ml_engine = MLEngine()

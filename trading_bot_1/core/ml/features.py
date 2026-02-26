"""
ML Feature Engineering — расчёт фичей для 3 моделей ансамбля.

Все фичи рассчитываются поверх базовых индикаторов (EMA, RSI, ATR, BB, ADX),
которые уже есть в DataFrame после FeatureEngineer.process_all_features().
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional

from core.logger import setup_logger

logger = setup_logger("ml_features")


class MLFeatureEngineer:
    """
    Расчёт ML-специфичных фичей поверх базовых индикаторов.
    Вызывается на DataFrame, в котором уже есть: ema_21, ema_50, ema_200,
    rsi, atr, bb_upper, bb_lower, bb_mid, bb_width, adx, di_plus, di_minus.
    """

    @staticmethod
    def compute_all(df: pd.DataFrame, current_idx: int, signal_direction: str) -> Optional[Dict[str, float]]:
        """
        Рассчитать все ML-фичи для заданного индекса в DataFrame.
        
        Args:
            df: DataFrame с базовыми индикаторами
            current_idx: индекс текущей свечи
            signal_direction: "BUY" или "SELL" — для direction-aware фичей
            
        Returns:
            Dict с фичами для каждой из 3 моделей, или None если данных недостаточно.
        """
        if current_idx < 20 or current_idx >= len(df):
            return None

        try:
            momentum = MLFeatureEngineer._momentum_features(df, current_idx, signal_direction)
            volatility = MLFeatureEngineer._volatility_features(df, current_idx)
            structure = MLFeatureEngineer._structure_features(df, current_idx, signal_direction)
        except (KeyError, IndexError, ZeroDivisionError) as e:
            logger.warning(f"Ошибка расчёта ML-фичей на индексе {current_idx}: {e}")
            return None

        # Подробный лог всех фичей для отладки
        logger.debug(
            f"[ML FEATURES] idx={current_idx} dir={signal_direction} | "
            f"MOMENTUM: RSI={momentum['rsi_normalized']:.3f} RSI_d={momentum['rsi_delta']:.3f} "
            f"EMA21s={momentum['ema_21_slope']:.3f} EMA50s={momentum['ema_50_slope']:.3f} "
            f"vs21={momentum['close_vs_ema21']:.3f} vs200={momentum['close_vs_ema200']:.3f} "
            f"DI={momentum['di_diff_directed']:.3f}"
        )
        logger.debug(
            f"[ML FEATURES] idx={current_idx} | "
            f"VOLATILITY: ATRn={volatility['atr_normalized']:.3f} ATRr={volatility['atr_ratio']:.3f} "
            f"BBw={volatility['bb_width']:.4f} BBwΔ={volatility['bb_width_change']:.4f} "
            f"BBpos={volatility['bb_position']:.3f} HLr={volatility['high_low_range']:.3f} "
            f"Vol={volatility['volume_ratio']:.3f}"
        )
        logger.debug(
            f"[ML FEATURES] idx={current_idx} | "
            f"STRUCTURE: ADX={structure['adx_normalized']:.3f} ADXs={structure['adx_slope']:.3f} "
            f"EMAalign={structure['ema_alignment']:.1f} BBsq={structure['bb_squeeze']:.0f} "
            f"HH={structure['higher_highs']:.2f} LL={structure['lower_lows']:.2f} "
            f"vsMid={structure['close_vs_range_mid']:.3f}"
        )

        return {
            "momentum": momentum,
            "volatility": volatility,
            "structure": structure,
        }

    # ------------------------------------------------------------------
    # Momentum Features (для MomentumScorer)
    # ------------------------------------------------------------------
    @staticmethod
    def _momentum_features(df: pd.DataFrame, idx: int, direction: str) -> Dict[str, float]:
        """Фичи силы и направления текущего импульса."""
        row = df.iloc[idx]
        atr = row["atr"] if pd.notna(row["atr"]) and row["atr"] > 0 else 1e-8

        # RSI и его дельта за 3 свечи
        rsi = float(row["rsi"]) if pd.notna(row["rsi"]) else 50.0
        rsi_3ago = float(df.iloc[idx - 3]["rsi"]) if pd.notna(df.iloc[idx - 3]["rsi"]) else 50.0
        rsi_delta = rsi - rsi_3ago

        # Наклоны EMA за последние 5 свечей (в %)
        ema_21_now = row["ema_21"]
        ema_21_5ago = df.iloc[idx - 5]["ema_21"]
        ema_21_slope = ((ema_21_now - ema_21_5ago) / ema_21_5ago * 100) if pd.notna(ema_21_5ago) and ema_21_5ago != 0 else 0.0

        ema_50_now = row["ema_50"]
        ema_50_5ago = df.iloc[idx - 5]["ema_50"]
        ema_50_slope = ((ema_50_now - ema_50_5ago) / ema_50_5ago * 100) if pd.notna(ema_50_5ago) and ema_50_5ago != 0 else 0.0

        # Отклонение цены от скользящих (нормализовано ATR)
        close = float(row["close"])
        close_vs_ema21 = (close - float(row["ema_21"])) / atr if pd.notna(row["ema_21"]) else 0.0
        close_vs_ema200 = (close - float(row["ema_200"])) / atr if pd.notna(row["ema_200"]) else 0.0

        # Направленность тренда
        di_plus = float(row["di_plus"]) if pd.notna(row["di_plus"]) else 0.0
        di_minus = float(row["di_minus"]) if pd.notna(row["di_minus"]) else 0.0
        di_diff = di_plus - di_minus

        # Direction-aware: для SELL инвертируем знак direction-sensitive фичей
        dir_mult = 1.0 if direction == "BUY" else -1.0

        return {
            "rsi_normalized": rsi / 100.0,
            "rsi_delta": float(np.clip(rsi_delta / 30.0, -1.0, 1.0)),  # нормализуем
            "ema_21_slope": float(np.clip(ema_21_slope, -2.0, 2.0)),
            "ema_50_slope": float(np.clip(ema_50_slope, -2.0, 2.0)),
            "close_vs_ema21": float(np.clip(close_vs_ema21 * dir_mult, -3.0, 3.0)),
            "close_vs_ema200": float(np.clip(close_vs_ema200 * dir_mult, -5.0, 5.0)),
            "di_diff_directed": float(np.clip(di_diff * dir_mult / 20.0, -1.0, 1.0)),
        }

    # ------------------------------------------------------------------
    # Volatility Features (для VolatilityScorer)
    # ------------------------------------------------------------------
    @staticmethod
    def _volatility_features(df: pd.DataFrame, idx: int) -> Dict[str, float]:
        """Фичи текущей волатильности."""
        row = df.iloc[idx]
        close = float(row["close"])

        # ATR нормализованный
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        atr_normalized = atr / close if close > 0 else 0.0

        # ATR ratio vs SMA(20) ATR
        atr_window = df.iloc[max(0, idx - 19):idx + 1]["atr"]
        atr_sma = atr_window.mean() if len(atr_window) > 0 else atr
        atr_ratio = atr / atr_sma if pd.notna(atr_sma) and atr_sma > 0 else 1.0

        # Bollinger Bands
        bb_width = float(row["bb_width"]) if pd.notna(row["bb_width"]) else 0.0

        # Изменение BB Width за 3 свечи
        bb_width_3ago = float(df.iloc[idx - 3]["bb_width"]) if pd.notna(df.iloc[idx - 3]["bb_width"]) else bb_width
        bb_width_change = bb_width - bb_width_3ago

        # BB Position: где цена внутри канала [0, 1]
        bb_upper = float(row["bb_upper"]) if pd.notna(row["bb_upper"]) else close
        bb_lower = float(row["bb_lower"]) if pd.notna(row["bb_lower"]) else close
        bb_range = bb_upper - bb_lower
        bb_position = (close - bb_lower) / bb_range if bb_range > 0 else 0.5

        # Размах текущей свечи vs ATR
        high_low_range = (float(row["high"]) - float(row["low"])) / atr if atr > 0 else 1.0

        # Volume ratio (если доступен)
        volume_ratio = 1.0
        if "volume" in df.columns:
            vol = float(row["volume"]) if pd.notna(row["volume"]) else 0.0
            vol_window = df.iloc[max(0, idx - 19):idx + 1]["volume"]
            vol_sma = vol_window.mean() if len(vol_window) > 0 else vol
            volume_ratio = vol / vol_sma if pd.notna(vol_sma) and vol_sma > 0 else 1.0

        return {
            "atr_normalized": float(np.clip(atr_normalized * 100, 0.0, 10.0)),  # в %
            "atr_ratio": float(np.clip(atr_ratio, 0.0, 5.0)),
            "bb_width": float(np.clip(bb_width, 0.0, 0.5)),
            "bb_width_change": float(np.clip(bb_width_change, -0.1, 0.1)),
            "bb_position": float(np.clip(bb_position, 0.0, 1.0)),
            "high_low_range": float(np.clip(high_low_range, 0.0, 5.0)),
            "volume_ratio": float(np.clip(volume_ratio, 0.0, 10.0)),
        }

    # ------------------------------------------------------------------
    # Structure Features (для StructureScorer)
    # ------------------------------------------------------------------
    @staticmethod
    def _structure_features(df: pd.DataFrame, idx: int, direction: str) -> Dict[str, float]:
        """Фичи рыночной структуры (тренд, диапазон, переход)."""
        row = df.iloc[idx]

        # ADX и его изменение за 5 свечей
        adx = float(row["adx"]) if pd.notna(row["adx"]) else 0.0
        adx_5ago = float(df.iloc[idx - 5]["adx"]) if pd.notna(df.iloc[idx - 5]["adx"]) else adx
        adx_slope = adx - adx_5ago

        # EMA alignment: все три в "правильном" порядке
        ema21 = float(row["ema_21"]) if pd.notna(row["ema_21"]) else 0
        ema50 = float(row["ema_50"]) if pd.notna(row["ema_50"]) else 0
        ema200 = float(row["ema_200"]) if pd.notna(row["ema_200"]) else 0

        if ema21 > ema50 > ema200:
            ema_alignment = 1.0   # бычий порядок
        elif ema21 < ema50 < ema200:
            ema_alignment = -1.0  # медвежий порядок
        else:
            ema_alignment = 0.0   # неопределённый

        # Direction-aware: для SELL инвертируем
        dir_mult = 1.0 if direction == "BUY" else -1.0
        ema_alignment_directed = ema_alignment * dir_mult

        # Bollinger squeeze: текущая ширина < минимальная за 20 свечей?
        bb_width_window = df.iloc[max(0, idx - 19):idx + 1]["bb_width"].dropna()
        bb_squeeze = 0.0
        if len(bb_width_window) > 5:
            current_bb = float(row["bb_width"]) if pd.notna(row["bb_width"]) else 0.0
            min_bb = bb_width_window.min()
            if current_bb <= min_bb * 1.1:  # в пределах 10% от минимума
                bb_squeeze = 1.0

        # Higher Highs / Lower Lows (последние 5 свечей)
        lookback = min(5, idx)
        recent_highs = df.iloc[idx - lookback:idx + 1]["high"].values
        recent_lows = df.iloc[idx - lookback:idx + 1]["low"].values

        higher_highs = 0
        lower_lows = 0
        for i in range(1, len(recent_highs)):
            if recent_highs[i] > recent_highs[i - 1]:
                higher_highs += 1
            if recent_lows[i] < recent_lows[i - 1]:
                lower_lows += 1

        # Позиция цены относительно середины диапазона 20 свечей
        range_window = df.iloc[max(0, idx - 19):idx + 1]
        range_high = range_window["high"].max()
        range_low = range_window["low"].min()
        range_size = range_high - range_low
        close = float(row["close"])
        close_vs_mid = ((close - (range_low + range_size / 2)) / range_size) if range_size > 0 else 0.0

        return {
            "adx_normalized": float(np.clip(adx / 50.0, 0.0, 1.0)),
            "adx_slope": float(np.clip(adx_slope / 10.0, -1.0, 1.0)),
            "ema_alignment": float(ema_alignment_directed),
            "bb_squeeze": float(bb_squeeze),
            "higher_highs": float(higher_highs / max(lookback, 1)),
            "lower_lows": float(lower_lows / max(lookback, 1)),
            "close_vs_range_mid": float(np.clip(close_vs_mid * dir_mult, -1.0, 1.0)),
        }

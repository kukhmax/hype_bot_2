"""
Fibo Bot — Feature Engine.

Вычисление всех фичей для стратегии:
- Структурные (ATR, импульс, угол)
- VWAP (daily, anchored, z-score)
- Order Flow (delta, CVD, imbalance)
- Волатильность (BB width, expansion)
- Контекстные (HTF bias, session, funding)
- Временные (hour, day_of_week, session)

Все фичи возвращаются как словарь для использования в
Strategy Engine и ML Engine.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import numpy as np

from engines.market_engine import CandleBuffer
from utils.logger import get_logger

logger = get_logger("feature_engine")


# ─── ATR ─────────────────────────────────────────────────────────────────────

def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Average True Range."""
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)

    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        )
    )
    # Prepend NaN for first element
    tr = np.concatenate([[np.nan], tr])

    atr = np.full(len(closes), np.nan)
    # Simple MA for first ATR value
    atr[period] = np.mean(tr[1:period + 1])
    # Exponential smoothing
    for i in range(period + 1, len(closes)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


# ─── EMA ─────────────────────────────────────────────────────────────────────

def compute_ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    ema = np.full(len(data), np.nan)
    if len(data) < period:
        return ema
    ema[period - 1] = np.mean(data[:period])
    multiplier = 2 / (period + 1)
    for i in range(period, len(data)):
        ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


# ─── RSI ─────────────────────────────────────────────────────────────────────

def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    rsi = np.full(len(closes), np.nan)
    if len(closes) < period + 1:
        return rsi

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        rsi[period] = 100
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (100 / (1 + rs))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100 - (100 / (1 + rs))

    return rsi


# ─── ADX ─────────────────────────────────────────────────────────────────────

def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Average Directional Index."""
    n = len(closes)
    adx = np.full(n, np.nan)
    if n < period * 2:
        return adx

    atr = compute_atr(highs, lows, closes, period)

    dm_plus = np.zeros(n)
    dm_minus = np.zeros(n)

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        dm_plus[i] = up_move if (up_move > down_move and up_move > 0) else 0
        dm_minus[i] = down_move if (down_move > up_move and down_move > 0) else 0

    smooth_dm_plus = compute_ema(dm_plus, period)
    smooth_dm_minus = compute_ema(dm_minus, period)

    di_plus = np.where(atr > 0, 100 * smooth_dm_plus / atr, 0)
    di_minus = np.where(atr > 0, 100 * smooth_dm_minus / atr, 0)

    di_sum = di_plus + di_minus
    dx = np.where(di_sum > 0, 100 * np.abs(di_plus - di_minus) / di_sum, 0)
    adx = compute_ema(dx, period)

    return adx


# ─── Bollinger Bands ──────────────────────────────────────────────────────────

def compute_bollinger_bands(closes: np.ndarray, period: int = 20,
                            std_dev: float = 2.0):
    """Bollinger Bands: upper, middle, lower, width."""
    n = len(closes)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    width = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window)
        middle[i] = m
        upper[i] = m + std_dev * s
        lower[i] = m - std_dev * s
        width[i] = (upper[i] - lower[i]) / m if m > 0 else 0

    return upper, middle, lower, width


# ─── VWAP ─────────────────────────────────────────────────────────────────────

def compute_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 volumes: np.ndarray) -> np.ndarray:
    """
    Volume Weighted Average Price.
    Простой VWAP — кумулятивный от начала буфера.
    """
    typical_price = (highs + lows + closes) / 3
    cum_tp_vol = np.cumsum(typical_price * volumes)
    cum_vol = np.cumsum(volumes)
    vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, closes)
    return vwap


def compute_anchored_vwap(highs: np.ndarray, lows: np.ndarray,
                          closes: np.ndarray, volumes: np.ndarray,
                          anchor_idx: int) -> np.ndarray:
    """
    Anchored VWAP от заданной точки (anchor_idx).
    """
    n = len(closes)
    avwap = np.full(n, np.nan)
    if anchor_idx < 0 or anchor_idx >= n:
        return avwap

    typical_price = (highs + lows + closes) / 3
    cum_tp_vol = 0.0
    cum_vol = 0.0

    for i in range(anchor_idx, n):
        cum_tp_vol += typical_price[i] * volumes[i]
        cum_vol += volumes[i]
        avwap[i] = cum_tp_vol / cum_vol if cum_vol > 0 else closes[i]

    return avwap


# ─── Feature Engine ──────────────────────────────────────────────────────────

@dataclass
class FeatureResult:
    """Результат вычисления фичей для одного момента времени."""
    # Структурные
    atr: float = 0.0
    atr_ratio: float = 0.0
    rsi: float = 50.0
    adx: float = 0.0

    # VWAP
    vwap: float = 0.0
    distance_to_vwap: float = 0.0
    vwap_slope: float = 0.0
    zscore_from_vwap: float = 0.0

    # Bollinger
    bb_width: float = 0.0
    bb_position: float = 0.5  # 0 = нижняя, 1 = верхняя
    volatility_expansion: bool = False

    # Временные / контекстные
    hour_of_day: int = 0
    day_of_week: int = 0
    session: str = "OTHER"  # ASIA / EU / US / OTHER

    # Дополнительные
    body_ratio: float = 0.0  # body / range
    upper_wick_ratio: float = 0.0
    lower_wick_ratio: float = 0.0
    volume_sma_ratio: float = 1.0  # volume / SMA(volume, 20)

    # Тренд
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    trend_ema_alignment: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL

    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь."""
        return {
            "atr": self.atr,
            "atr_ratio": self.atr_ratio,
            "rsi": self.rsi,
            "adx": self.adx,
            "vwap": self.vwap,
            "distance_to_vwap": self.distance_to_vwap,
            "vwap_slope": self.vwap_slope,
            "zscore_from_vwap": self.zscore_from_vwap,
            "bb_width": self.bb_width,
            "bb_position": self.bb_position,
            "volatility_expansion": self.volatility_expansion,
            "hour_of_day": self.hour_of_day,
            "day_of_week": self.day_of_week,
            "session": self.session,
            "body_ratio": self.body_ratio,
            "upper_wick_ratio": self.upper_wick_ratio,
            "lower_wick_ratio": self.lower_wick_ratio,
            "volume_sma_ratio": self.volume_sma_ratio,
            "ema_20": self.ema_20,
            "ema_50": self.ema_50,
            "ema_200": self.ema_200,
            "trend_ema_alignment": self.trend_ema_alignment,
        }


class FeatureEngine:
    """
    Вычисляет все фичи на основе CandleBuffer.
    Возвращает FeatureResult с текущими значениями всех индикаторов.
    """

    def __init__(self, atr_period: int = 14, rsi_period: int = 14,
                 adx_period: int = 14, bb_period: int = 20):
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.bb_period = bb_period
        self._call_count = 0

        logger.info(
            f"[FeatureEngine] Инициализирован: ATR={atr_period}, RSI={rsi_period}, "
            f"ADX={adx_period}, BB={bb_period}"
        )

    def compute(self, buffer: CandleBuffer) -> Optional[FeatureResult]:
        """
        Вычислить все фичи для текущего состояния буфера.
        Возвращает None если данных недостаточно.
        """
        start_time = time.time()
        self._call_count += 1

        n = len(buffer)
        if n < 50:
            logger.warning(f"[FeatureEngine] Недостаточно данных: {n} свечей (мин. 50)")
            return None

        # Получаем массивы
        opens = buffer.opens()
        highs = buffer.highs()
        lows = buffer.lows()
        closes = buffer.closes()
        volumes = buffer.volumes()

        result = FeatureResult()

        try:
            # ─── ATR ────────────────────────────────────────────────
            atr_arr = compute_atr(highs, lows, closes, self.atr_period)
            result.atr = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0
            avg_atr = np.nanmean(atr_arr[-50:])
            result.atr_ratio = result.atr / avg_atr if avg_atr > 0 else 1.0

            # ─── RSI ────────────────────────────────────────────────
            rsi_arr = compute_rsi(closes, self.rsi_period)
            result.rsi = float(rsi_arr[-1]) if not np.isnan(rsi_arr[-1]) else 50.0

            # ─── ADX ────────────────────────────────────────────────
            adx_arr = compute_adx(highs, lows, closes, self.adx_period)
            result.adx = float(adx_arr[-1]) if not np.isnan(adx_arr[-1]) else 0

            # ─── VWAP ───────────────────────────────────────────────
            vwap_arr = compute_vwap(highs, lows, closes, volumes)
            result.vwap = float(vwap_arr[-1])
            current_price = float(closes[-1])
            result.distance_to_vwap = (
                (current_price - result.vwap) / result.vwap * 100
                if result.vwap > 0 else 0
            )

            # VWAP slope (наклон за последние 10 свечей)
            if len(vwap_arr) >= 10:
                vwap_recent = vwap_arr[-10:]
                vwap_valid = vwap_recent[~np.isnan(vwap_recent)]
                if len(vwap_valid) >= 2:
                    result.vwap_slope = float(
                        (vwap_valid[-1] - vwap_valid[0]) / vwap_valid[0] * 100
                    )

            # Z-score от VWAP
            if result.vwap > 0 and result.atr > 0:
                result.zscore_from_vwap = (current_price - result.vwap) / result.atr

            # ─── Bollinger Bands ────────────────────────────────────
            bb_upper, bb_mid, bb_lower, bb_w = compute_bollinger_bands(
                closes, self.bb_period
            )
            result.bb_width = float(bb_w[-1]) if not np.isnan(bb_w[-1]) else 0

            if not np.isnan(bb_upper[-1]) and not np.isnan(bb_lower[-1]):
                bb_range = bb_upper[-1] - bb_lower[-1]
                if bb_range > 0:
                    result.bb_position = float(
                        (current_price - bb_lower[-1]) / bb_range
                    )

            # Volatility expansion: текущая ширина > средняя за 50 свечей
            bb_w_valid = bb_w[~np.isnan(bb_w)]
            if len(bb_w_valid) >= 20:
                avg_bb_w = np.mean(bb_w_valid[-50:])
                result.volatility_expansion = result.bb_width > avg_bb_w * 1.2

            # ─── EMA ────────────────────────────────────────────────
            ema20 = compute_ema(closes, 20)
            ema50 = compute_ema(closes, 50)
            ema200 = compute_ema(closes, 200) if n >= 200 else np.full(n, np.nan)

            result.ema_20 = float(ema20[-1]) if not np.isnan(ema20[-1]) else 0
            result.ema_50 = float(ema50[-1]) if not np.isnan(ema50[-1]) else 0
            result.ema_200 = float(ema200[-1]) if not np.isnan(ema200[-1]) else 0

            # EMA alignment
            if result.ema_20 > result.ema_50:
                result.trend_ema_alignment = "BULLISH"
            elif result.ema_20 < result.ema_50:
                result.trend_ema_alignment = "BEARISH"

            # ─── Candle structure ───────────────────────────────────
            last_candle = buffer[0]
            candle_range = last_candle.candle_range
            if candle_range > 0:
                result.body_ratio = last_candle.body / candle_range
                result.upper_wick_ratio = last_candle.upper_wick / candle_range
                result.lower_wick_ratio = last_candle.lower_wick / candle_range

            # Volume / SMA(volume, 20)
            if len(volumes) >= 20:
                vol_sma = np.mean(volumes[-20:])
                if vol_sma > 0:
                    result.volume_sma_ratio = float(volumes[-1] / vol_sma)

            # ─── Время / Сессия ─────────────────────────────────────
            now = datetime.now(timezone.utc)
            result.hour_of_day = now.hour
            result.day_of_week = now.weekday()
            result.session = self._detect_session(now.hour)

        except Exception as e:
            logger.error(f"[FeatureEngine] Ошибка вычисления фичей: {e}", exc_info=True)
            return None

        elapsed_ms = (time.time() - start_time) * 1000

        # Логируем каждый вызов
        logger.info(
            f"[FeatureEngine] Фичи рассчитаны ({elapsed_ms:.1f}мс) | "
            f"ATR={result.atr:.2f} RSI={result.rsi:.1f} ADX={result.adx:.1f} | "
            f"VWAP dist={result.distance_to_vwap:.3f}% | "
            f"BB width={result.bb_width:.4f} | "
            f"EMA trend={result.trend_ema_alignment} | "
            f"Session={result.session}"
        )

        # Подробный лог каждые 10 вызовов
        if self._call_count % 10 == 0:
            logger.debug(
                f"[FeatureEngine] Детальные фичи #{self._call_count}: {result.to_dict()}"
            )

        return result

    @staticmethod
    def _detect_session(hour_utc: int) -> str:
        """Определение торговой сессии по UTC часу."""
        if 0 <= hour_utc < 8:
            return "ASIA"
        elif 8 <= hour_utc < 14:
            return "EU"
        elif 14 <= hour_utc < 21:
            return "US"
        else:
            return "ASIA"  # 21-00 UTC = начало азиатской

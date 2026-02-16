"""Движок сигналов на основе ADX/ATR.

Содержит расчёт вспомогательных индикаторов и принятие торгового решения
по последней свече (LONG/SHORT/None).
"""

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class SignalEngine:
    """Вычисление ADX/ATR и принятие решения."""

    @staticmethod
    def calculate_adx(df, period=14):
        """Добавляет в DataFrame столбцы tr, atr, +di, -di, dx, adx."""
        logger.debug("Расчёт ADX/ATR period=%s", period)
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift()),
                abs(df["low"] - df["close"].shift())
            )
        )

        df["+dm"] = np.where(
            (df["high"] - df["high"].shift()) >
            (df["low"].shift() - df["low"]),
            np.maximum(df["high"] - df["high"].shift(), 0),
            0
        )

        df["-dm"] = np.where(
            (df["low"].shift() - df["low"]) >
            (df["high"] - df["high"].shift()),
            np.maximum(df["low"].shift() - df["low"], 0),
            0
        )

        df["atr"] = df["tr"].rolling(period).mean()
        df["+di"] = 100 * (df["+dm"].rolling(period).mean() / df["atr"])
        df["-di"] = 100 * (df["-dm"].rolling(period).mean() / df["atr"])

        df["dx"] = 100 * abs(df["+di"] - df["-di"]) / (df["+di"] + df["-di"])
        df["adx"] = df["dx"].rolling(period).mean()

        return df

    @staticmethod
    def check_signal(df, adx_threshold, atr_threshold):
        """Возвращает LONG/SHORT или None по последней свече с учётом порогов ADX/ATR."""
        last = df.iloc[-1]

        if last["adx"] <= adx_threshold:
            return None

        if last["atr"] <= atr_threshold:
            return None

        if last["+di"] > last["-di"]:
            return "LONG"

        return "SHORT"

    @staticmethod
    def decide_from_candles(df: pd.DataFrame, adx_thresh: float, atr_thresh: float) -> str | None:
        """Высокоуровневая функция: принимает df OHLC, считает индикаторы и возвращает сигнал."""
        if df is None or len(df) < 50:
            logger.debug("Недостаточно данных для сигнала: len=%s", 0 if df is None else len(df))
            return None
        df = SignalEngine.calculate_adx(df.copy(), period=14)
        signal = SignalEngine.check_signal(df, adx_thresh, atr_thresh)
        logger.debug("Решение по последней свече: %s", signal)
        return signal


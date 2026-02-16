import pandas as pd
import numpy as np

class SignalEngine:

    @staticmethod
    def calculate_adx(df, period=14):
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
        last = df.iloc[-1]

        if last["adx"] <= adx_threshold:
            return None

        if last["atr"] <= atr_threshold:
            return None

        if last["+di"] > last["-di"]:
            return "LONG"

        return "SHORT"

    @staticmethod
    def check_realtime(pair, adx, atr):
        # здесь должен быть кэш последних свечей
        # для production нужно хранить OHLCV в Redis

        # временно — рандом логика для теста
        import random
        if random.random() > 0.995:
            return "LONG"
        return None


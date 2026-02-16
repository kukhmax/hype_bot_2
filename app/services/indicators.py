import pandas as pd
import numpy as np

def calculate_atr(df):
    high = df['high']
    low = df['low']
    close = df['close']

    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low - close.shift())
    ], axis=1).max(axis=1)

    return tr.rolling(ATR_LEN).mean()

def calculate_adx(df):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_rma = rma(true_range, DI_LEN)

    plus_di = 100 * rma(pd.Series(plus_dm), DI_LEN) / tr_rma
    minus_di = 100 * rma(pd.Series(minus_dm), DI_LEN) / tr_rma

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)
    adx = rma(dx, ADX_LEN)

    return adx

def ml_supertrend(df):

    df = df.copy()
    df["atr"] = calculate_atr(df)

    if len(df) < TRAINING_PERIOD:
        return None, None

    atr_series = df["atr"].dropna().values[-TRAINING_PERIOD:]
    upper = np.max(atr_series)
    lower = np.min(atr_series)

    centroids = np.array([
        lower + (upper - lower) * H_VOL,
        lower + (upper - lower) * M_VOL,
        lower + (upper - lower) * L_VOL
    ])

    for _ in range(10):
        clusters = {0: [], 1: [], 2: []}
        for value in atr_series:
            idx = np.argmin(np.abs(centroids - value))
            clusters[idx].append(value)
        for i in range(3):
            if clusters[i]:
                centroids[i] = np.mean(clusters[i])

    current_atr = df["atr"].iloc[-1]
    cluster_idx = np.argmin(np.abs(centroids - current_atr))
    assigned_atr = centroids[cluster_idx]

    hl2 = (df["high"] + df["low"]) / 2
    upper_band = hl2 + FACTOR * assigned_atr
    lower_band = hl2 - FACTOR * assigned_atr

    direction = 1
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper_band.iloc[i - 1]:
            direction = -1
        elif df["close"].iloc[i] < lower_band.iloc[i - 1]:
            direction = 1

    return direction

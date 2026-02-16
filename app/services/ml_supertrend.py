import numpy as np
import pandas as pd
from app.services.signal_engine import SignalEngine


class MLAdaptiveSupertrendEngine:
    @staticmethod
    def _atr(df: pd.DataFrame, length: int) -> pd.Series:
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
        tr[0] = high[0] - low[0]
        atr = pd.Series(tr).rolling(length).mean()
        return atr

    @staticmethod
    def _kmeans_centroids(series: pd.Series, train_len: int, p_high: float, p_mid: float, p_low: float, max_iter: int = 20, tol: float = 1e-8):
        arr = series.dropna().values
        if len(arr) < train_len:
            return None, None, None
        window = arr[-train_len:]
        lower = float(np.min(window))
        upper = float(np.max(window))
        a = lower + (upper - lower) * p_high
        b = lower + (upper - lower) * p_mid
        c = lower + (upper - lower) * p_low
        for _ in range(max_iter):
            d1 = np.abs(window - a)
            d2 = np.abs(window - b)
            d3 = np.abs(window - c)
            g1 = window[(d1 < d2) & (d1 < d3)]
            g2 = window[(d2 < d1) & (d2 < d3)]
            g3 = window[(d3 < d1) & (d3 < d2)]
            a_new = float(np.mean(g1)) if g1.size > 0 else a
            b_new = float(np.mean(g2)) if g2.size > 0 else b
            c_new = float(np.mean(g3)) if g3.size > 0 else c
            if abs(a_new - a) < tol and abs(b_new - b) < tol and abs(c_new - c) < tol:
                a, b, c = a_new, b_new, c_new
                break
            a, b, c = a_new, b_new, c_new
        return a, b, c

    @staticmethod
    def _assigned_centroid(value: float, centroids):
        a, b, c = centroids
        d = [abs(value - a), abs(value - b), abs(value - c)]
        idx = int(np.argmin(d))
        return idx, [a, b, c][idx]

    @staticmethod
    def _supertrend_last(df: pd.DataFrame, factor: float, atr_scalar: float):
        n = len(df)
        hl2 = (df["high"].values + df["low"].values) / 2.0
        close = df["close"].values
        upper = np.zeros(n)
        lower = np.zeros(n)
        st = np.zeros(n)
        dir_arr = np.zeros(n, dtype=int)
        for i in range(n):
            u0 = hl2[i] + factor * atr_scalar
            l0 = hl2[i] - factor * atr_scalar
            if i == 0:
                upper[i] = u0
                lower[i] = l0
                dir_arr[i] = 1
                st[i] = upper[i]
                continue
            prev_u = upper[i - 1]
            prev_l = lower[i - 1]
            lower[i] = l0 if (l0 > prev_l or close[i - 1] < prev_l) else prev_l
            upper[i] = u0 if (u0 < prev_u or close[i - 1] > prev_u) else prev_u
            prev_st = st[i - 1]
            if prev_st == prev_u:
                d = -1 if close[i] > upper[i] else 1
            else:
                d = 1 if close[i] < lower[i] else -1
            dir_arr[i] = d
            st[i] = lower[i] if d == -1 else upper[i]
        return st[-1], dir_arr[-2] if n >= 2 else 1, dir_arr[-1]

    @staticmethod
    def evaluate(df: pd.DataFrame, factor: float = 3.0, atr_len: int = 10, training_len: int = 100, p_high: float = 0.75, p_mid: float = 0.5, p_low: float = 0.25, adx_confirm: float = 20.0):
        if df is None or len(df) < max(atr_len, training_len) + 2:
            return None
        atr = MLAdaptiveSupertrendEngine._atr(df, atr_len)
        centroids = MLAdaptiveSupertrendEngine._kmeans_centroids(atr, training_len, p_high, p_mid, p_low)
        if centroids[0] is None:
            return None
        current_atr = float(atr.iloc[-1])
        cluster, assigned = MLAdaptiveSupertrendEngine._assigned_centroid(current_atr, centroids)
        st_last, dir_prev, dir_curr = MLAdaptiveSupertrendEngine._supertrend_last(df, factor, assigned)
        adx_df = SignalEngine.calculate_adx(df.copy(), period=14)
        adx_last = float(adx_df.iloc[-1]["adx"])
        signal = None
        if adx_last >= adx_confirm:
            if dir_prev > 0 and dir_curr < 0:
                signal = "LONG"
            elif dir_prev < 0 and dir_curr > 0:
                signal = "SHORT"
        return {
            "centroids": centroids,
            "cluster": cluster,
            "assigned": assigned,
            "st": st_last,
            "dir_prev": int(dir_prev),
            "dir": int(dir_curr),
            "adx": adx_last,
            "signal": signal,
        }

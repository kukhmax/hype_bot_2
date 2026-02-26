import pandas as pd
import numpy as np

from core.logger import setup_logger

logger = setup_logger("feature_engineer")

class FeatureEngineer:
    """
    Класс для расчета технических индикаторов (EMA, RSI, ATR, Bollinger Bands, ADX).
    Использует чистый Pandas для надежной работы без внешних зависимостей.
    """

    @staticmethod
    def add_ema(df: pd.DataFrame, lengths: list[int] = [21, 50, 200]) -> pd.DataFrame:
        """Добавляет к DataFrame колонки EMA с заданными периодами."""
        for length in lengths:
            df[f"ema_{length}"] = df['close'].ewm(span=length, adjust=False).mean()
        return df

    @staticmethod
    def add_rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        """Рассчитывает RSI."""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=length, min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=length, min_periods=1).mean()
        
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        # Сглаживание по Уайлдеру, часто аппроксимируется ewm
        gain = delta.where(delta > 0, 0).ewm(alpha=1/length, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/length, adjust=False).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        """Рассчитывает ATR."""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        
        # ATR по Уайлдеру использует RMA, что эквивалентно EWM с alpha = 1 / length
        df["atr"] = true_range.ewm(alpha=1/length, adjust=False).mean()
        return df

    @staticmethod
    def add_bollinger_bands(df: pd.DataFrame, length: int = 20, std: int = 2) -> pd.DataFrame:
        """Рассчитывает Bollinger Bands (Upper, Lower, Mid, Bandwidth)."""
        df["bb_mid"] = df['close'].rolling(window=length).mean()
        rolling_std = df['close'].rolling(window=length).std()
        
        df["bb_upper"] = df["bb_mid"] + (rolling_std * std)
        df["bb_lower"] = df["bb_mid"] - (rolling_std * std)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        return df

    @staticmethod
    def add_adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        """Рассчитывает ADX (Average Directional Index)."""
        high_diff = df['high'].diff()
        low_diff = df['low'].diff()
        
        # +DM / -DM
        pos_dm = np.where((high_diff > 0) & (high_diff > -low_diff), high_diff, 0.0)
        neg_dm = np.where((low_diff < 0) & (-low_diff > high_diff), -low_diff, 0.0)
        
        pos_dm = pd.Series(pos_dm, index=df.index)
        neg_dm = pd.Series(neg_dm, index=df.index)
        
        # Сглаженный +DM, -DM и TR
        tr = df['high'] - df['low']
        tr = pd.concat([tr, (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        
        smoothed_tr = tr.ewm(alpha=1/length, adjust=False).mean()
        smoothed_pos_dm = pos_dm.ewm(alpha=1/length, adjust=False).mean()
        smoothed_neg_dm = neg_dm.ewm(alpha=1/length, adjust=False).mean()
        
        df["di_plus"] = 100 * (smoothed_pos_dm / smoothed_tr)
        df["di_minus"] = 100 * (smoothed_neg_dm / smoothed_tr)
        
        dx = 100 * np.abs(df["di_plus"] - df["di_minus"]) / (df["di_plus"] + df["di_minus"])
        df["adx"] = dx.ewm(alpha=1/length, adjust=False).mean()
        return df

    @classmethod
    def process_all_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Главный метод, который прогоняет датафрейм через все индикаторы разом."""
        logger.info("Расчет всех базовых индикаторов (Feature Engineering)...")
        # Копируем чтобы избежать SettingWithCopyWarning
        df = df.copy()
        
        # Проверяем наличие колонок open, high, low, close, volume
        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            logger.error(f"В DataFrame отсутствуют обязательные колонки: {required_cols - set(df.columns)}")
            return df

        # Применяем расчеты
        df = cls.add_ema(df, lengths=[21, 50, 200])
        df = cls.add_rsi(df, length=14)
        df = cls.add_atr(df, length=14)
        df = cls.add_bollinger_bands(df, length=20, std=2)
        df = cls.add_adx(df, length=14)
        
        # ВАЖНО: Мы больше НЕ делаем dropna() здесь!
        # В режиме бэктеста NaN строки просто игнорируются (сигналы начинаются с индекса 200).
        # В live-режиме удаление NaN строк (возникших от новых свечей на стыке с окном расчета) 
        # приводило к полному исчезновению DataFrame (баг обрезания по 19 строк).
        
        logger.debug(f"Feature Engineering завершен. Строк для анализа: {len(df)}")
        return df

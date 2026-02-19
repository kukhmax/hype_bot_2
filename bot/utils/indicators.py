import numpy as np
import pandas as pd

def calculate_ema(prices: np.array, period: int) -> np.array:
    """Рассчет EMA"""
    return pd.Series(prices).ewm(span=period, adjust=False).mean().values

def calculate_adx(high: np.array, low: np.array, close: np.array, period: int = 14) -> float:
    """Рассчет ADX"""
    df = pd.DataFrame({
        'high': high,
        'low': low,
        'close': close
    })
    
    # True Range
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    
    # Directional Movement
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)
    
    # Smoothed ATR and DM
    df['atr'] = df['tr'].rolling(window=period).mean()
    df['plus_di'] = 100 * (df['plus_dm'].rolling(window=period).mean() / df['atr'])
    df['minus_di'] = 100 * (df['minus_dm'].rolling(window=period).mean() / df['atr'])
    
    # ADX
    df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
    df['adx'] = df['dx'].rolling(window=period).mean()
    
    return df['adx'].iloc[-1], df['plus_di'].iloc[-1], df['minus_di'].iloc[-1]
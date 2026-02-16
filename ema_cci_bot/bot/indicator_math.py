import pandas_ta as ta
import pandas as pd
from bot.config import (
    EMA_CANDIDATES, HISTORY_FOR_EMA_SCORING, EMA_TOLERANCE_PCT,
    CCI_LENGTH, MACD_FAST, MACD_SLOW, MACD_SIGNAL
)

def add_all_indicators(df: pd.DataFrame):
    """Добавляет все варианты EMA, CCI и MACD в DataFrame."""
    # 1. CCI
    df.ta.cci(length=CCI_LENGTH, append=True)
    # Имя столбца CCI будет выглядеть как CCI_14_0.015

    # 2. MACD
    df.ta.macd(fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL, append=True)
    # Имена столбцов: MACD_12_26_9, MACDh_12_26_9 (гистограмма), MACDs_12_26_9 (сигнальная)

    # 3. Все варианты EMA
    for length in EMA_CANDIDATES:
        df.ta.ema(length=length, append=True)
        # Имена столбцов: EMA_50, EMA_100 и т.д.
    
    df.dropna(inplace=True)
    return df

def find_best_ema(df: pd.DataFrame, direction: str) -> dict:
    """
    Алгоритм выбора лучшей EMA на основе системы баллов.
    direction: "long" или "short"
    """
    # Берем срез истории для анализа
    analysis_df = df.tail(HISTORY_FOR_EMA_SCORING).copy()
    
    ema_scores = {}

    for length in EMA_CANDIDATES:
        ema_col_name = f"EMA_{length}"
        if ema_col_name not in analysis_df.columns:
            continue
            
        score = 0
        ema_series = analysis_df[ema_col_name]
        
        # Рассчитываем зоны допуска
        tolerance_val = ema_series * EMA_TOLERANCE_PCT
        upper_band = ema_series + tolerance_val
        lower_band = ema_series - tolerance_val
        
        for i in range(len(analysis_df)):
            candle = analysis_df.iloc[i]
            
            if direction == "long":
                # Условие хорошего теста для Лонга:
                # Цена закрытия ВЫШЕ EMA
                is_close_valid = candle['close'] > candle[ema_col_name]
                # Минимум свечи зашел в зону допуска (коснулся или почти коснулся)
                is_touching = candle['low'] <= upper_band
                
                # Условие пробоя (плохой знак): Закрытие НИЖЕ EMA
                is_breakout = candle['close'] < candle[ema_col_name]

                if is_close_valid and is_touching:
                    score += 2 # Начисляем баллы за хороший тест
                elif is_breakout:
                    score -= 3 # Сильно штрафуем за пробой

            elif direction == "short":
                # Условие хорошего теста для Шорта:
                # Цена закрытия НИЖЕ EMA
                is_close_valid = candle['close'] < candle[ema_col_name]
                # Максимум свечи зашел в зону допуска
                is_touching = candle['high'] >= lower_band
                
                # Условие пробоя: Закрытие ВЫШЕ EMA
                is_breakout = candle['close'] > candle[ema_col_name]

                if is_close_valid and is_touching:
                    score += 2
                elif is_breakout:
                    score -= 3

        ema_scores[length] = score

    # Находим EMA с максимальным баллом
    if not ema_scores:
        return None

    best_length = max(ema_scores, key=ema_scores.get)
    return {
        "length": best_length,
        "col_name": f"EMA_{best_length}",
        "score": ema_scores[best_length]
    }
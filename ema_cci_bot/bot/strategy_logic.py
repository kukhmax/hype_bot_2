import pandas as pd
from bot.config import CCI_LENGTH, CCI_LEVEL, MACD_FAST, MACD_SLOW, MACD_SIGNAL, RR_TP1, RR_TP2
from bot.indicator_math import find_best_ema

def check_signals(df: pd.DataFrame, symbol: str):
    """Проверяет последнюю закрытую свечу на наличие сигнала."""
    # Нам нужны минимум две последние свечи: 
    # -2 (предпоследняя, для проверки предыдущего состояния CCI)
    # -1 (последняя закрытая свеча, на которой ищем сигнал)
    if len(df) < 3:
        return None

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]

    # Имена столбцов индикаторов
    cci_col = f"CCI_{CCI_LENGTH}_0.015"
    macd_col = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    macdh_col = f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}" # Гистограмма

    # --- ПРОВЕРКА НА ЛОНГ ---
    # 1. Определяем тренд (цена выше какой-то EMA?)
    # Простой способ: проверяем, находится ли цена выше 200 EMA или 100 EMA для общего тренда
    # В ТЗ было "Находит лучшие EMA". Сначала определим направление, потом найдем лучшую.
    
    # Предположение тренда для первичного анализа
    is_uptrend_bias = last_candle['close'] > last_candle['EMA_200']
    is_downtrend_bias = last_candle['close'] < last_candle['EMA_200']

    signal_data = None

    if is_uptrend_bias:
        # Ищем лучшую EMA для лонга
        best_ema_info = find_best_ema(df, "long")
        best_ema_col = best_ema_info['col_name']
        
        # --- Условия ЛОНГА ---
        # 1. Тренд: Цена закрытия ВЫШЕ лучшей EMA
        trend_ok = last_candle['close'] > last_candle[best_ema_col]
        
        # 2. CCI: Консервативный вход.
        # На предыдущей свече CCI был ниже уровня перепроданности (-100)
        # На текущей свече CCI вернулся ВЫШЕ уровня -100
        cci_ok = (prev_candle[cci_col] < -CCI_LEVEL) and (last_candle[cci_col] > -CCI_LEVEL)

        # 3. MACD: Подтверждение импульса. Гистограмма растет или выше нуля.
        # Вариант: Гистограмма стала выше, чем на предыдущей свече
        macd_ok = last_candle[macdh_col] > prev_candle[macdh_col]

        if trend_ok and cci_ok and macd_ok:
             signal_data = calculate_risk_management(last_candle, best_ema_info, "LONG", symbol)

    elif is_downtrend_bias:
         # Ищем лучшую EMA для шорта
        best_ema_info = find_best_ema(df, "short")
        best_ema_col = best_ema_info['col_name']

        # --- Условия ШОРТА ---
        # 1. Тренд: Цена закрытия НИЖЕ лучшей EMA
        trend_ok = last_candle['close'] < last_candle[best_ema_col]

        # 2. CCI: Консервативный вход.
        # На предыдущей CCI был ВЫШЕ уровня перекупленности (100)
        # На текущей CCI вернулся НИЖЕ уровня 100
        cci_ok = (prev_candle[cci_col] > CCI_LEVEL) and (last_candle[cci_col] < CCI_LEVEL)

        # 3. MACD: Гистограмма падает
        macd_ok = last_candle[macdh_col] < prev_candle[macdh_col]

        if trend_ok and cci_ok and macd_ok:
             signal_data = calculate_risk_management(last_candle, best_ema_info, "SHORT", symbol)

    return signal_data


def calculate_risk_management(candle, ema_info, side, symbol):
    """Расчет ТВХ, Стопа и Тейков на основе R:R."""
    entry_price = candle['close']
    # Стоп за EMA на сигнальной свече
    sl_price = candle[ema_info['col_name']]
    
    risk_amount = abs(entry_price - sl_price)

    if side == "LONG":
        # Если EMA слишком близко, даем минимальный отступ (например, 0.1%) для безопасности
        if risk_amount < entry_price * 0.001:
             risk_amount = entry_price * 0.001
             sl_price = entry_price - risk_amount

        tp1_price = entry_price + (risk_amount * RR_TP1)
        tp2_price = entry_price + (risk_amount * RR_TP2)
        
    else: # SHORT
        if risk_amount < entry_price * 0.001:
             risk_amount = entry_price * 0.001
             sl_price = entry_price + risk_amount

        tp1_price = entry_price - (risk_amount * RR_TP1)
        tp2_price = entry_price - (risk_amount * RR_TP2)

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry_price,
        "sl": sl_price,
        "tp1": tp1_price,
        "tp2": tp2_price,
        "used_ema": ema_info['length'],
        "ema_score": ema_info['score'],
        "timestamp": candle['timestamp']
    }
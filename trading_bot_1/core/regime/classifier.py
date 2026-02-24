import pandas as pd
from typing import Dict, Any

class RegimeClassifier:
    """
    Классификатор фазы рынка.
    Определяет текущий режим рынка: Strong Trend, Weak Trend, Range, High Volatility.
    """
    
    @staticmethod
    def classify(df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """
        Возвращает словарь с текущим режимом и метриками.
        """
        if len(df) <= current_idx or current_idx < 1:
            return {"regime": "unknown", "confidence": 0.0}
            
        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]
        
        # Получаем необходимые метрики
        adx = row.get('adx', 0)
        ema_200 = row.get('ema_200', None)
        close = row.get('close', 0)
        
        # Для Bollinger Width 
        upper_bb = row.get('upper_bb', None)
        lower_bb = row.get('lower_bb', None)
        bb_width_percent = 0
        if pd.notna(upper_bb) and pd.notna(lower_bb) and close:
            bb_width_percent = (upper_bb - lower_bb) / close * 100
            
        # Slope EMA200
        prev_ema_200 = prev_row.get('ema_200', None)
        ema_slope = 0
        if pd.notna(ema_200) and pd.notna(prev_ema_200):
            ema_slope = (ema_200 - prev_ema_200) / prev_ema_200 * 100
            
        # Логика классификации
        regime = "unknown"
        
        # 1. High Volatility (Ширина Bollinger Bands резко увеличилась)
        if bb_width_percent > 2.0: # Порог подбирается, например 2%
            regime = "high_volatility"
            
        # 2. Strong Trend (Высокий ADX, явный наклон EMA)
        elif adx > 25 and abs(ema_slope) > 0.01:
            regime = "strong_trend"
            
        # 3. Range (Низкий ADX, цена пилит средние)
        elif adx < 20:
            regime = "range"
            
        # 4. Weak Trend (переходное состояние)
        else:
            regime = "weak_trend"
            
        return {
            "regime": regime,
            "adx": float(adx) if pd.notna(adx) else 0.0,
            "bb_width_percent": float(bb_width_percent),
            "ema_slope": float(ema_slope)
        }

"""
Fibo Bot — Bootstrap ML Script.

Скрипт для создания первоначального набора весов (Zero-to-One ML Bootstrap).
1. Скачивает историю с MEXC за последние N дней
2. Симулирует пролет цены (собирая фичи и генерируя сигналы)
3. "Заглядывает в будущее", чтобы разметить сигналы:
   - 1: Цена достигла TP (Take Profit)
   - 0: Цена достигла SL (Stop Loss) или время вышло
4. Обучает XGBoost, LightGBM, LogReg и сохраняет модели.
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

# Необходимо добавить корень проекта в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from config import config
from engines.market_engine import fetch_historical_candles, CandleBuffer, Candle
from engines.strategy_engine import StrategyEngine
from engines.feature_engine import FeatureEngine
from ml.ml_engine import ml_engine
from utils.logger import get_logger

logger = get_logger("bootstrap")


async def simulate_history(days: int = 30) -> tuple:
    """
    Скачивание истории и генерация разметки:
    Возвращает (X, y) Numpy массивы для обучения.
    """
    symbol = config.trading.default_symbol
    timeframe = config.trading.default_timeframe
    limit = 2000  # Максимально отдает MEXC
    
    # 1. Загрузка данных
    logger.info(f"📥 Загрузка истории {symbol} {timeframe} за {days} дней...")
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    # Примерно рассчитываем сколько свечей нужно 
    # (15m * 100 свечей = 1500м = 25 часов)
    # Для 30 дней 15-минуток нужно ~2880 свечей.
    
    all_candles = []
    # Качаем кусками по 'limit' свечей
    while True:
        try:
            candles = await fetch_historical_candles(
                symbol=symbol,
                timeframe=timeframe,
                market_type=config.exchange.market_type,
                limit=limit,
                end_time=end_time
            )
            if not candles:
                break
                
            all_candles = candles + all_candles  # Добавляем в начало
            
            # Сдвигаем end_time для следующего запроса
            earliest_candle = candles[0]
            end_time = earliest_candle.timestamp - 1
            
            # Проверяем не вышли ли за нужный период дней
            first_candle_time = datetime.fromtimestamp(earliest_candle.timestamp/1000, tz=timezone.utc)
            if (datetime.now(timezone.utc) - first_candle_time).days > days:
                break
                
            if len(candles) < limit:
                break # Дошли до начала
                
            # Задержка чтобы не спамить API
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Ошибка при скачивании истории: {e}")
            break

    # Обрезаем лишнее
    target_start = datetime.now(timezone.utc) - timedelta(days=days)
    all_candles = [c for c in all_candles if datetime.fromtimestamp(c.timestamp/1000, tz=timezone.utc) >= target_start]
    
    logger.info(f"✅ Скачано всего свечей: {len(all_candles)}")
    if len(all_candles) < 200:
        logger.error("Слишком мало свечей для обучения.")
        return np.array([]), np.array([])

    # 2. Симуляция Strategy Engine
    logger.info("⚙️ Запуск симулятора...")
    strategy = StrategyEngine()
    features = FeatureEngine(buffer_size=150)
    
    buffer = CandleBuffer(size=300)
    
    signals_log = [] # List of tuples: (index, signal, feature_result)
    
    # "Прокручиваем" свечи одну за другой
    for i, candle in enumerate(all_candles):
        buffer.add(candle)
        
        # Нам нужно как минимум 150 свечей для расчета фичей
        if len(buffer) < 150:
            continue
            
        # Симулируем поведение on_candle
        # Внимание: для ускорения StrategyEngine::analyze внутри считает feature_engine.compute(buffer)
        signal = await strategy.analyze(symbol, timeframe, buffer)
        
        if signal:
            # Если сигнал найден, пересчитываем фичи (как это делает main.py)
            fr = strategy.feature_engine.compute(buffer)
            # Запоминаем индекс свечи, на которой произошел сигнал
            signals_log.append((i, signal, fr))
            logger.debug(f"[{i}/{len(all_candles)}] Найден сигнал: {signal.direction} {signal.setup}")
            
    logger.info(f"📊 Всего сигналов сгенерировано на истории: {len(signals_log)}")

    # 3. Labeling (Разметка)
    logger.info("🏷️ Разметка сигналов (поиск SL или TP в будущем)...")
    X_list = []
    y_list = []
    
    for i, (sig_idx, signal, fr) in enumerate(signals_log):
        # Заглядываем в будущее (от sig_idx до конца списка)
        future_candles = all_candles[sig_idx+1:]
        
        # Если сигнал в самом конце и будущего нет - пропускаем (не знаем исход)
        if not future_candles:
            continue
            
        is_tp = False
        is_sl = False
        
        # Берем TP1 как минимум для успеха (можно поменять на TP2)
        target_tp = signal.tp1 if signal.tp1 > 0 else signal.tp2
        
        for fc in future_candles:
            if signal.direction == "LONG":
                if fc.high >= target_tp:
                    is_tp = True
                    break
                if fc.low <= signal.stop_loss:
                    is_sl = True
                    break
            else: # SHORT
                if fc.low <= target_tp:
                    is_tp = True
                    break
                if fc.high >= signal.stop_loss:
                    is_sl = True
                    break
                    
        # Вычисляем лейбл (Отбрасываем сигналы, которые еще не закрылись)
        if is_tp:
            label = 1
        elif is_sl:
            label = 0
        else:
            continue # Не закрылся до конца истории (expired)
            
        # Извлекаем вектор фичей
        x_vec = ml_engine.extract_features(fr, signal)
        
        X_list.append(x_vec[0]) # Избавляемся от внешней размерности (1, N)
        y_list.append(label)

    X = np.array(X_list)
    y = np.array(y_list)
    
    logger.info(f"✅ Разметка завершена. Готовых сэмплов X: {X.shape}, y: {y.shape}")
    if len(y) > 0:
        win_rate = (y.sum() / len(y)) * 100
        logger.info(f"📊 Базовый (чистый) Win Rate стратегии на истории: {win_rate:.1f}%")
        
    return X, y


async def run():
    logger.info("=" * 60)
    logger.info("🚀 Запуск Bootstrap ML")
    
    # 30 дней достаточно для нормальной разметки 15-минуток
    X, y = await simulate_history(days=30)
    
    if len(X) < 50:
        logger.error("❌ Слишком мало данных (нужно хотя бы 50 закрытых сигналов). "
                    "Попробуйте увеличить timeframe или кол-во days.")
        return
        
    # Обучение и сохранение моделей
    ml_engine.train_models(X, y)
    
    logger.info("✅ Bootstrap ML успешно завершен. Модели готовы к бою!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())

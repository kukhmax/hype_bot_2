"""
Integration тест: Бэктест → Обучение ML → Фильтрация → Сравнение.

Прогоняет полный цикл:
1. Генерируем данные
2. Бэктест стратегии (без ML) → метрики A
3. Обучаем ML ансамбль на данных бэктеста
4. Бэктест стратегии (с ML фильтром) → метрики B
5. Сравниваем метрики A и B
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.features.indicators import FeatureEngineer
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.backtest.engine import BacktestEngine
from core.ml.trainer import EnsembleTrainer
from core.ml.ensemble import EnsembleFilter
from core.ml.features import MLFeatureEngineer
from core.ml.models import SKLEARN_AVAILABLE

import logging
logging.getLogger("backtest_engine").setLevel(logging.WARNING)
logging.getLogger("trend_pullback").setLevel(logging.WARNING)
logging.getLogger("volatility_breakout").setLevel(logging.WARNING)
logging.getLogger("ml_trainer").setLevel(logging.INFO)
logging.getLogger("ml_ensemble").setLevel(logging.WARNING)

logger = setup_logger("test_ml_backtest")


def _create_trending_data(n: int = 1500) -> pd.DataFrame:
    """Создать синтетические данные с трендами и откатами."""
    np.random.seed(123)
    
    # Чередуем тренды и откаты
    prices = [100.0]
    for i in range(1, n):
        phase = (i // 200) % 4
        if phase == 0:
            drift = 0.15   # Бычий тренд
        elif phase == 1:
            drift = -0.05  # Лёгкий откат
        elif phase == 2:
            drift = -0.15  # Медвежий тренд
        else:
            drift = 0.05   # Восстановление
        
        noise = np.random.randn() * 0.8
        prices.append(max(prices[-1] + drift + noise, 5.0))
    
    prices = np.array(prices)
    
    df = pd.DataFrame({
        "timestamp": list(range(n)),
        "open": prices + np.random.randn(n) * 0.3,
        "high": prices + np.abs(np.random.randn(n) * 1.0),
        "low": prices - np.abs(np.random.randn(n) * 1.0),
        "close": prices,
        "volume": np.random.uniform(5000, 100000, n),
    })
    
    return df


def run_backtest_with_ml_filter(
    df: pd.DataFrame, 
    strategy, 
    ensemble: EnsembleFilter,
    initial_balance: float = 1000.0,
) -> dict:
    """
    Прогоняем бэктест с ML фильтром.
    Перехватываем сигналы стратегии и проверяем их через EnsembleFilter.
    """
    # Подготавливаем данные
    data = df.copy()
    if hasattr(strategy, 'prepare_data'):
        data = strategy.prepare_data(data)
    
    balance = initial_balance
    positions = []
    trades = []
    filtered_count = 0
    
    for idx in range(200, len(data)):
        candle = data.iloc[idx]
        
        # Проверяем открытые позиции
        for i in range(len(positions) - 1, -1, -1):
            pos = positions[i]
            closed = False
            pnl = 0.0
            
            if pos["side"] == "BUY":
                if candle["low"] <= pos["stop_loss"]:
                    pnl = (pos["stop_loss"] - pos["entry_price"]) * pos["qty"]
                    closed = True
                elif pos.get("take_profit") and candle["high"] >= pos["take_profit"]:
                    pnl = (pos["take_profit"] - pos["entry_price"]) * pos["qty"]
                    closed = True
            else:
                if candle["high"] >= pos["stop_loss"]:
                    pnl = (pos["entry_price"] - pos["stop_loss"]) * pos["qty"]
                    closed = True
                elif pos.get("take_profit") and candle["low"] <= pos["take_profit"]:
                    pnl = (pos["entry_price"] - pos["take_profit"]) * pos["qty"]
                    closed = True
            
            if closed:
                fee = (candle["close"] * pos["qty"]) * 0.001
                net_pnl = pnl - fee
                balance += net_pnl
                trades.append({"pnl": net_pnl})
                positions.pop(i)
        
        # Получаем сигнал от стратегии
        if not positions:
            signal_data = strategy.on_ohlcv(data, idx)
            signal = signal_data.get("signal", "NONE")
            
            if signal in ("BUY", "SELL"):
                # ML фильтр
                ml_passed, ml_score, _ = ensemble.evaluate(data, idx, signal)
                
                if not ml_passed:
                    filtered_count += 1
                    continue
                
                # Открываем позицию
                entry_price = candle["close"]
                size = balance * 0.1
                qty = size / entry_price
                
                positions.append({
                    "side": signal,
                    "entry_price": entry_price,
                    "qty": qty,
                    "stop_loss": signal_data.get("stop_loss", 0),
                    "take_profit": signal_data.get("take_profit"),
                })
    
    total = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    roi = (balance - initial_balance) / initial_balance * 100
    
    return {
        "total_trades": total,
        "wins": wins,
        "winrate": round(wins / total * 100, 1) if total > 0 else 0,
        "roi": round(roi, 2),
        "final_balance": round(balance, 2),
        "filtered_by_ml": filtered_count,
    }


def test_ml_training_and_filtering():
    """Полный integration тест: train → filter → compare."""
    logger.info("\n" + "=" * 60)
    logger.info("INTEGRATION ТЕСТ: ML Training + Backtest Filtering")
    logger.info("=" * 60 + "\n")
    
    if not SKLEARN_AVAILABLE:
        logger.warning("⚠️ scikit-learn не установлен. Пропуск теста.")
        return
    
    # 1. Генерируем данные
    df = _create_trending_data(1500)
    logger.info(f"Сгенерировано {len(df)} свечей.")
    
    # 2. Бэктест БЕЗ ML (Baseline)
    strategy = TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0)
    engine_baseline = BacktestEngine(data=df.copy(), strategy=strategy, initial_balance=1000.0, verbose=False)
    engine_baseline.run()
    
    baseline_metrics = engine_baseline.metrics
    logger.info(f"\n📊 Baseline (без ML):")
    logger.info(f"   Trades: {baseline_metrics['total_trades']}")
    logger.info(f"   WinRate: {baseline_metrics['winrate']}%")
    logger.info(f"   ROI: {baseline_metrics['roi']}%")
    logger.info(f"   Profit Factor: {baseline_metrics['profit_factor']}")
    
    if baseline_metrics['total_trades'] < 10:
        logger.warning("⚠️ Мало сделок для сравнения. Тест пропущен (стратегия не сгенерировала достаточно сигналов на синтетических данных).")
        logger.info("✅ Integration тест: пропущен (мало данных), но пайплайн работает.")
        return
    
    # 3. Обучаем ML ансамбль
    trainer = EnsembleTrainer(strategy=TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0))
    ensemble = trainer.train_from_data(df.copy())
    
    if ensemble is None:
        logger.warning("⚠️ Не удалось обучить ML (недостаточно сделок). Тест пропущен.")
        logger.info("✅ Integration тест: пропущен (мало данных для обучения).")
        return
    
    # 4. Бэктест С ML фильтром
    strategy2 = TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0)
    ml_metrics = run_backtest_with_ml_filter(df.copy(), strategy2, ensemble)
    
    logger.info(f"\n🧠 С ML фильтром:")
    logger.info(f"   Trades: {ml_metrics['total_trades']}")
    logger.info(f"   Filtered: {ml_metrics['filtered_by_ml']}")
    logger.info(f"   WinRate: {ml_metrics['winrate']}%")
    logger.info(f"   ROI: {ml_metrics['roi']}%")
    
    # 5. Сравнение
    logger.info(f"\n📈 Сравнение:")
    logger.info(f"   Сделок: {baseline_metrics['total_trades']} → {ml_metrics['total_trades']} (отфильтровано: {ml_metrics['filtered_by_ml']})")
    
    wr_diff = ml_metrics['winrate'] - baseline_metrics['winrate']
    logger.info(f"   WinRate: {baseline_metrics['winrate']}% → {ml_metrics['winrate']}% ({'+' if wr_diff >= 0 else ''}{wr_diff:.1f}%)")
    
    roi_diff = ml_metrics['roi'] - baseline_metrics['roi']
    logger.info(f"   ROI: {baseline_metrics['roi']}% → {ml_metrics['roi']}% ({'+' if roi_diff >= 0 else ''}{roi_diff:.2f}%)")
    
    # Мы не гарантируем что ML улучшит результат на синтетических данных,
    # но проверяем что пайплайн работает корректно
    assert ml_metrics['total_trades'] <= baseline_metrics['total_trades'], \
        "ML фильтр должен уменьшить или сохранить количество сделок"
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ INTEGRATION ТЕСТ ПРОЙДЕН!")
    logger.info("=" * 60 + "\n")


if __name__ == "__main__":
    test_ml_training_and_filtering()

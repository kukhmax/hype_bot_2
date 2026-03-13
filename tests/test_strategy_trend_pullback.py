import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.backtest.engine import BacktestEngine

logger = setup_logger("test_trend_pullback")

async def run_trend_pullback_test():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем бэктест Trend Pullback для {symbol} (15m свечи)")
    
    # 1. Загружаем историю (15-минутки)
    # 15m - хороший таймфрейм для внутридневных трендов
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=15)
    
    if df.empty:
        logger.error("Нет данных для бэктеста!")
        return
        
    # 2. Инициализируем стратегию
    # Настроим RSI порог на 40, SL на 1.5 ATR и R:R на 2.0
    strategy = TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0)
    
    # 3. Запускаем движок на стартовом балансе 1000 USDT
    engine = BacktestEngine(data=df, strategy=strategy, initial_balance=1000.0)
    engine.run()
    
if __name__ == "__main__":
    asyncio.run(run_trend_pullback_test())

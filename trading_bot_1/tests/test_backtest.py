import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.simple_ma import SimpleMAStrategy
from core.backtest.engine import BacktestEngine

logger = setup_logger("test_backtest")

async def run_backtest_test():
    symbol = "BTC_USDT"
    logger.info(f"Начинаем MVP-бэктест для {symbol} (15m свечи)")
    
    # 1. Загружаем историю (например 15-минутки)
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=15)
    
    if df.empty:
        logger.error("Нет данных для бэктеста!")
        return
        
    # 2. Инициализируем простую стратегию (EMA пересечения)
    strategy = SimpleMAStrategy(fast_ma=10, slow_ma=20)
    
    # 3. Запускаем движок на стартовом балансе 1000 USDT
    engine = BacktestEngine(data=df, strategy=strategy, initial_balance=1000.0)
    engine.run()
    
if __name__ == "__main__":
    asyncio.run(run_backtest_test())

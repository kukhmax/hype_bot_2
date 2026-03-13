import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.breakout import BreakoutStrategy
from core.backtest.engine import BacktestEngine

logger = setup_logger("test_breakout")

async def run_breakout_test():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем проверку Volatility Breakout для {symbol} (5m свечи)")
    
    # 1. Загружаем историю (5m - хороший ТФ для скальп-пробоя)
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=5)
    
    if df.empty:
        logger.error("Нет данных для бэктеста!")
        return
        
    # 2. Инициализируем стратегию
    # Сужение: BB Width < 2.5% (очень узко)
    strategy = BreakoutStrategy(bb_width_threshold=0.025, adx_threshold=20.0, sl_atr_mult=1.0, rr_ratio=1.5)
    
    # 3. Запускаем движок на стартовом балансе 1000 USDT
    engine = BacktestEngine(data=df, strategy=strategy, initial_balance=1000.0)
    engine.run()
    
if __name__ == "__main__":
    asyncio.run(run_breakout_test())

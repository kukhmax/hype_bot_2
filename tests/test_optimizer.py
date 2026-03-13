import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.backtest.optimizer import StrategyOptimizer

# Получаем корневой логгер и глушим его до уровня WARNING, 
# чтобы бэктест движок не спамил миллионом логов во время оптимизации
logging.getLogger("backtest_engine").setLevel(logging.WARNING)
logging.getLogger("trend_pullback").setLevel(logging.WARNING)

logger = setup_logger("test_optimizer")

async def run_optimization_test():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем оптимизацию Trend Pullback для {symbol} (15m свечи)")
    
    # 1. Загружаем историю (15m)
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=15)
    
    if df.empty:
        logger.error("Нет данных для бэктеста!")
        return
        
    # 2. Настраиваем сетку параметров для перебора (Grid Search)
    param_grid = {
        "rsi_threshold": [35, 40, 45],
        "sl_atr_mult": [1.0, 1.5, 2.0],
        "rr_ratio": [1.5, 2.0, 2.5]
    }
    
    # Итого будет 3 * 3 * 3 = 27 прогонов (комбинаций)
    
    # 3. Запускаем оптимизатор
    optimizer = StrategyOptimizer(data=df, strategy_class=TrendPullbackStrategy, initial_balance=1000.0)
    optimizer.optimize(param_grid)
    
    # 4. Выводим ТОП-5 лучших комбинаций (по ROI)
    optimizer.print_top_results(top_n=5)
    
if __name__ == "__main__":
    asyncio.run(run_optimization_test())

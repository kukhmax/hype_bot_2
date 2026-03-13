import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.data.historical import MEXCHistoricalDownloader
from core.strategies.liquidity_sweep import LiquiditySweepStrategy
from core.backtest.engine import BacktestEngine

logger = setup_logger("test_liquidity_sweep")

async def run_liquidity_sweep_test():
    symbol = "SOL_USDT"
    logger.info(f"Начинаем проверку Liquidity Sweep для {symbol} (15m свечи)")
    
    # 1. Загружаем историю (15m - оптимально для поиска сквизов)
    df = await MEXCHistoricalDownloader.get_klines(symbol, timeframe_minutes=15)
    
    if df.empty:
        logger.error("Нет данных для бэктеста!")
        return
        
    # 2. Инициализируем стратегию
    # Ищем экстремумы за последние 20 свечей, RSI порог 40 с отступом стопа 1.0 ATR
    strategy = LiquiditySweepStrategy(lookback_period=20, rsi_ob_os=40, sl_atr_mult=1.0, rr_ratio=2.0)
    
    # 3. Запускаем движок на стартовом балансе 1000 USDT
    engine = BacktestEngine(data=df, strategy=strategy, initial_balance=1000.0)
    engine.run()
    
if __name__ == "__main__":
    asyncio.run(run_liquidity_sweep_test())

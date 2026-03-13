import asyncio
import os
import sys

# Добавляем корневую папку в sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.execution.live_engine import LiveEngine

logger = setup_logger("test_live_engine")

async def run_live_test():
    symbol = "SOL_USDT"
    timeframe = 15 # Минуты
    
    logger.info(f"=== Запуск бота в режиме PAPER TRADING: {symbol} ({timeframe}m) ===")
    
    # 1. Загружаем стратегию с нашими оптимизированными параметрами
    # Из прошлого шага: rsi=40, sl=1.5, rr=2.0 давали хороший профит
    strategy = TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0)
    
    # 2. Инициализируем Live Engine в режиме симуляции (paper_trading=True)
    engine = LiveEngine(symbol=symbol, timeframe_minutes=timeframe, strategy=strategy, paper_trading=True)
    
    # Инициализация скачает историю и выставит коллбэки
    is_ready = await engine.initialize()
    if not is_ready:
        logger.error("Не удалось инициализировать движок.")
        return
        
    # Запускаем таск на 5 минут работы (чтобы проверить сбор свечей)
    logger.info("Бот слушает WebSockets. Ждём данных...")
    task = asyncio.create_task(engine.run_forever())
    
    # Ждем 30 секунд (чтобы убедиться, что тики собираются и вебсокет не падает)
    await asyncio.sleep(10)
    logger.info("Отменяем таск Live Engine (окончание теста)")
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Тест LiveEngine (Paper Trading) успешно завершён!")

if __name__ == "__main__":
    asyncio.run(run_live_test())

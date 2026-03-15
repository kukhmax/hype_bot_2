import asyncio
import argparse

from core.logger import setup_logger
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.strategies.liquidity_sweep import LiquiditySweepStrategy
from core.execution.live_engine import LiveEngine
from core.execution.mexc_live_engine import MEXCLiveEngine

logger = setup_logger("run_bot")

async def main():
    parser = argparse.ArgumentParser(description="Запуск Торгового Бота (Live Engine)")
    parser.add_argument("--symbol", type=str, default="SOL_USDT", help="Торговая пара на MEXC (например, SOL_USDT)")
    parser.add_argument("--tf", type=int, default=15, help="Таймфрейм свечей в минутах")
    parser.add_argument("--strategy", type=str, choices=["trend", "breakout", "sweep"], default="trend", help="Какую стратегию использовать")
    parser.add_argument("--live", action="store_true", help="Включить реальную торговлю (по умолчанию Paper Trading)")
    
    args = parser.parse_args()
    
    # 1. Запуск
    paper_trading = not args.live
    mode_str = "PAPER TRADING (Без реальных ордеров)" if paper_trading else "LIVE TRADING (Осторожно, реальные деньги!)"
    
    logger.info(f"=== ЗАПУСК БОТА: {mode_str} ===")
    logger.info(f"Символ: {args.symbol} | Таймфрейм: {args.tf}m")
    logger.info("Стратегия будет выбираться АВТОМАТИЧЕСКИ благодаря Regime Classifier!")
    
    # 2. Инициализируем движок
    from bot.settings import bot_settings
    exchange = bot_settings.get("exchange", "mexc").lower()
    
    if exchange == "mexc" and not paper_trading:
        engine = MEXCLiveEngine(symbol=args.symbol, timeframe_minutes=args.tf, paper_trading=paper_trading)
    else:
        engine = LiveEngine(symbol=args.symbol, timeframe_minutes=args.tf, paper_trading=paper_trading)
    
    is_ready = await engine.initialize()
    if not is_ready:
        logger.error("Критическая ошибка инициализации. Бот остановлен.")
        return
        
    # 3. Запускаем вечный цикл WebSockets
    logger.info("Переход в режим ожидания сигналов...")
    await engine.run_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C).")

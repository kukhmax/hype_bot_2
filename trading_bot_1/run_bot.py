import asyncio
import argparse

from core.logger import setup_logger
from core.strategies.trend_pullback import TrendPullbackStrategy
from core.strategies.breakout import BreakoutStrategy
from core.strategies.liquidity_sweep import LiquiditySweepStrategy
from core.execution.live_engine import LiveEngine

logger = setup_logger("run_bot")

async def main():
    parser = argparse.ArgumentParser(description="Запуск Торгового Бота (Live Engine)")
    parser.add_argument("--symbol", type=str, default="SOL_USDT", help="Торговая пара на MEXC (например, SOL_USDT)")
    parser.add_argument("--tf", type=int, default=15, help="Таймфрейм свечей в минутах")
    parser.add_argument("--strategy", type=str, choices=["trend", "breakout", "sweep"], default="trend", help="Какую стратегию использовать")
    parser.add_argument("--live", action="store_true", help="Включить реальную торговлю (по умолчанию Paper Trading)")
    
    args = parser.parse_args()
    
    # 1. Выбираем стратегию
    if args.strategy == "trend":
        logger.info("Выбрана стратегия: Trend Pullback")
        # Параметры из тестов оптимизации
        strategy = TrendPullbackStrategy(rsi_threshold=40, sl_atr_mult=1.5, rr_ratio=2.0)
    elif args.strategy == "breakout":
        logger.info("Выбрана стратегия: Volatility Breakout")
        strategy = BreakoutStrategy(bb_width_threshold=0.03, adx_threshold=20.0, sl_atr_mult=1.0, rr_ratio=1.5)
    else:
        logger.info("Выбрана стратегия: Liquidity Sweep")
        strategy = LiquiditySweepStrategy(lookback_period=20, rsi_ob_os=40, sl_atr_mult=1.0, rr_ratio=2.0)
        
    paper_trading = not args.live
    mode_str = "PAPER TRADING (Без реальных ордеров)" if paper_trading else "LIVE TRADING (Осторожно, реальные деньги!)"
    
    logger.info(f"=== ЗАПУСК БОТА: {mode_str} ===")
    logger.info(f"Символ: {args.symbol} | Таймфрейм: {args.tf}m")
    
    # 2. Инициализируем движок
    engine = LiveEngine(symbol=args.symbol, timeframe_minutes=args.tf, strategy=strategy, paper_trading=paper_trading)
    
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

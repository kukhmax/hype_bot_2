import asyncio
from typing import Dict, Optional, Callable, Awaitable

from core.logger import setup_logger
from core.execution.live_engine import LiveEngine

logger = setup_logger("engine_manager")


class EngineManager:
    """
    Менеджер торговых движков.
    Управляет несколькими LiveEngine экземплярами (по одному на каждую торговую пару).
    """

    def __init__(self):
        self.engines: Dict[str, LiveEngine] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
    
    async def add_pair(
        self, 
        symbol: str, 
        timeframe: int, 
        leverage: int = 1,
        paper_trading: bool = True,
        tg_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> bool:
        """Добавляет пару и запускает для неё движок."""
        if symbol in self.engines:
            logger.warning(f"Движок для {symbol} уже запущен.")
            return False
        
        engine = LiveEngine(
            symbol=symbol,
            timeframe_minutes=timeframe,
            paper_trading=paper_trading,
            leverage=leverage,
            tg_callback=tg_callback
        )
        
        is_ready = await engine.initialize()
        if not is_ready:
            logger.error(f"Не удалось инициализировать движок для {symbol}")
            return False
        
        self.engines[symbol] = engine
        self.tasks[symbol] = asyncio.create_task(engine.run_forever())
        logger.info(f"Движок для {symbol} успешно запущен (Leverage: {leverage}x)")
        return True
    
    async def remove_pair(self, symbol: str) -> bool:
        """Останавливает и удаляет движок для указанной пары."""
        if symbol not in self.engines:
            logger.warning(f"Движок для {symbol} не найден.")
            return False
        
        task = self.tasks.pop(symbol, None)
        if task and not task.done():
            task.cancel()
            
        self.engines.pop(symbol, None)
        logger.info(f"Движок для {symbol} остановлен и удалён.")
        return True
    
    async def stop_all(self):
        """Останавливает все движки."""
        symbols = list(self.tasks.keys())
        for symbol in symbols:
            await self.remove_pair(symbol)
        logger.info("Все движки остановлены.")
    
    def get_status(self) -> list:
        """Возвращает статус всех активных движков."""
        statuses = []
        for symbol, engine in self.engines.items():
            task = self.tasks.get(symbol)
            is_running = task and not task.done()
            statuses.append({
                "symbol": symbol,
                "regime": engine.current_regime,
                "strategy": engine.active_strategy.__class__.__name__,
                "leverage": engine.leverage,
                "has_position": engine.current_position is not None,
                "position_side": engine.current_position["side"] if engine.current_position else None,
                "running": is_running
            })
        return statuses
    
    @property
    def is_running(self) -> bool:
        return len(self.engines) > 0

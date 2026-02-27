import asyncio
from typing import Dict, Optional, Callable, Awaitable

from core.logger import setup_logger
from core.execution.live_engine import LiveEngine
from core.data.mexc_client import MEXCWebSocketClient

logger = setup_logger("engine_manager")


class EngineManager:
    """
    Менеджер торговых движков.
    Управляет несколькими LiveEngine экземплярами (по одному на каждую торговую пару).
    Владеет ОДНИМ общим WebSocket соединением для всех пар.
    """

    def __init__(self):
        self.engines: Dict[str, LiveEngine] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        # Общий WebSocket клиент для всех пар
        self._ws_client: Optional[MEXCWebSocketClient] = None
        self._ws_task: Optional[asyncio.Task] = None
    
    def _ensure_ws_client(self) -> MEXCWebSocketClient:
        """Создаёт WS клиент при первом использовании (lazy init)."""
        if self._ws_client is None:
            self._ws_client = MEXCWebSocketClient()
            logger.info("Создан общий WebSocket клиент для всех пар")
        return self._ws_client
    
    def _start_ws_if_needed(self):
        """Запускает WS connect task если ещё не запущен."""
        if self._ws_task is None or self._ws_task.done():
            ws = self._ensure_ws_client()
            self._ws_task = asyncio.create_task(self._run_ws(ws))
            logger.info("WebSocket connection task запущен")
    
    async def _run_ws(self, ws_client: MEXCWebSocketClient):
        """Бесконечный цикл WS (единственный на все пары)."""
        try:
            await ws_client.connect()
        except asyncio.CancelledError:
            logger.info("WebSocket task отменён")
        except Exception as e:
            logger.error(f"WebSocket fatal error: {e}")
    
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
        
        # Получаем/создаём общий WS клиент
        ws_client = self._ensure_ws_client()
        
        engine = LiveEngine(
            symbol=symbol,
            timeframe_minutes=timeframe,
            paper_trading=paper_trading,
            leverage=leverage,
            tg_callback=tg_callback,
            ws_client=ws_client  # Передаём shared WS клиент
        )
        
        is_ready = await engine.initialize()
        if not is_ready:
            logger.error(f"Не удалось инициализировать движок для {symbol}")
            return False
        
        self.engines[symbol] = engine
        
        # Запускаем WS если ещё не запущен
        self._start_ws_if_needed()
        
        # Подписываемся на символ (если WS уже подключён)
        if ws_client.ws and ws_client.ws.close_code is None:
            await ws_client.subscribe_symbol(symbol)
        
        logger.info(f"Движок для {symbol} успешно запущен (Leverage: {leverage}x)")
        return True
    
    async def remove_pair(self, symbol: str) -> bool:
        """Останавливает и удаляет движок для указанной пары."""
        if symbol not in self.engines:
            logger.warning(f"Движок для {symbol} не найден.")
            return False
        
        # Отписываемся от символа
        if self._ws_client:
            await self._ws_client.unsubscribe_symbol(symbol)
            
        self.engines.pop(symbol, None)
        logger.info(f"Движок для {symbol} остановлен и удалён.")
        return True
    
    async def stop_all(self):
        """Останавливает все движки и WebSocket."""
        symbols = list(self.engines.keys())
        for symbol in symbols:
            await self.remove_pair(symbol)
        
        # Останавливаем WS
        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
            
        logger.info("Все движки и WebSocket остановлены.")
    
    def get_status(self) -> list:
        """Возвращает статус всех активных движков."""
        statuses = []
        for symbol, engine in self.engines.items():
            statuses.append({
                "symbol": symbol,
                "regime": engine.current_regime,
                "strategy": engine.active_strategy.__class__.__name__,
                "leverage": engine.leverage,
                "has_position": engine.current_position is not None,
                "position_side": engine.current_position["side"] if engine.current_position else None,
                "running": True  # Если в engines — значит работает
            })
        return statuses
    
    @property
    def is_running(self) -> bool:
        return len(self.engines) > 0

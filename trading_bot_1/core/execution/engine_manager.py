import asyncio
from typing import Dict, Optional, Callable, Awaitable

from core.logger import setup_logger
from core.execution.live_engine import LiveEngine
from core.data.mexc_client import MEXCWebSocketClient
from core.data.hyperliquid_client import HyperliquidWebSocketClient
from bot.settings import bot_settings

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
        self._ws_client = None
        self._ws_task: Optional[asyncio.Task] = None
    
    def _ensure_ws_client(self):
        """Создаёт WS клиент при первом использовании (lazy init)."""
        if self._ws_client is None:
            exchange = bot_settings.get("exchange", "mexc").lower()
            if exchange == "hyperliquid":
                self._ws_client = HyperliquidWebSocketClient()
                logger.info("Создан общий WebSocket клиент Hyperliquid для всех пар")
            else:
                self._ws_client = MEXCWebSocketClient()
                logger.info("Создан общий WebSocket клиент MEXC для всех пар")
        return self._ws_client
    
    def _start_ws_if_needed(self):
        """Запускает WS connect task если ещё не запущен."""
        if self._ws_task is None or self._ws_task.done():
            ws = self._ensure_ws_client()
            self._ws_task = asyncio.create_task(self._run_ws(ws))
            logger.info("WebSocket connection task запущен")
    
    async def _run_ws(self, ws_client):
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
        tg_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        tg_delete_callback: Optional[Callable[[int], Awaitable[None]]] = None,
        strategy_params: dict = None,
        max_daily_loss_percent: float = 5.0,
        enable_gemini: bool = True,
        tg_position_callback: Optional[Callable[[str, str], Awaitable[None]]] = None
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
            tg_delete_callback=tg_delete_callback,
            ws_client=ws_client,
            strategy_params=strategy_params,
            max_daily_loss_percent=max_daily_loss_percent,
            enable_gemini=enable_gemini,
            tg_position_callback=tg_position_callback
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
            try:
                await ws_client.subscribe_symbol(symbol)
            except Exception as e:
                logger.error(f"WS Subscribe Error for {symbol}: {e}")
        
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
            pos_pnl = 0.0
            pnl_percent = 0.0
            if engine.current_position:
                pos = engine.current_position
                current_price = None
                # Пытаемся получить текущую цену из свечей
                if getattr(engine, 'candle_builder', None) and engine.candle_builder.current_candle:
                    current_price = engine.candle_builder.current_candle["close"]
                elif hasattr(engine, 'df') and not engine.df.empty:
                    current_price = engine.df.iloc[-1]["close"]
                
                if current_price and "entry_price" in pos and "qty" in pos:
                    if pos["side"] == "BUY":
                        pos_pnl = (current_price - pos["entry_price"]) * pos["qty"]
                    elif pos["side"] == "SELL":
                        pos_pnl = (pos["entry_price"] - current_price) * pos["qty"]
                        
                    # Расчет PnL в процентах с учетом плеча
                    position_value = pos["entry_price"] * pos["qty"]
                    if position_value > 0:
                        pnl_percent = (pos_pnl / position_value) * 100 * engine.leverage

            # Расчет текущей дневной просадки
            current_drawdown_percent = 0.0
            if engine.risk_manager.is_session_started and engine.risk_manager.session_start_balance > 0:
                # Если PnL отрицательный, считаем процент просадки от стартового баланса
                if engine.risk_manager.current_session_pnl < 0:
                    loss = abs(engine.risk_manager.current_session_pnl)
                    current_drawdown_percent = (loss / engine.risk_manager.session_start_balance) * 100

            statuses.append({
                "symbol": symbol,
                "tf": engine.timeframe_minutes,
                "regime": engine.current_regime,
                "strategy": engine.active_strategy.__class__.__name__,
                "leverage": engine.leverage,
                "has_position": engine.current_position is not None,
                "position_side": engine.current_position["side"] if engine.current_position else None,
                "pnl": pos_pnl,
                "pnl_percent": pnl_percent,
                "current_drawdown_percent": current_drawdown_percent,
                "running": True  # Если в engines — значит работает
            })
        return statuses
    
    @property
    def is_running(self) -> bool:
        return len(self.engines) > 0

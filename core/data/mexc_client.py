import json
import asyncio
from typing import Dict, Any, Optional, Callable, List

from core.data.websocket_client import BaseWebSocketClient
from core.logger import setup_logger

logger = setup_logger("mexc_client")

class MEXCWebSocketClient(BaseWebSocketClient):
    """
    Клиент для MEXC Futures WebSocket API.
    Поддерживает ОДНО соединение для ВСЕХ торговых пар.
    
    Использование:
        ws = MEXCWebSocketClient()
        ws.add_symbol_callback("SOL_USDT", on_sol_message)
        ws.add_symbol_callback("BTC_USDT", on_btc_message)
        await ws.connect()  # Одно соединение, все символы
    
    Документация: https://mexcdevelop.github.io/apidocs/contract_v1_en/#websocket-api
    """
    
    WS_URL = "wss://contract.mexc.com/edge"

    def __init__(self, symbols: list[str] = None):
        super().__init__(self.WS_URL)
        # Символы для подписки при подключении (начальные)
        self._initial_symbols: list[str] = symbols or []
        # Все активные символы
        self._subscribed_symbols: set[str] = set()
        # Per-symbol callbacks: symbol -> [callback1, callback2, ...]
        self._symbol_callbacks: Dict[str, List[Callable]] = {}
        self._req_id = 0

    def _get_next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id
    
    @staticmethod
    def _format_symbol(symbol: str) -> str:
        """Форматирует символ в формат MEXC (SOL_USDT)."""
        if "_" not in symbol and symbol.endswith("USDT"):
            return f"{symbol[:-4]}_USDT"
        return symbol

    def add_symbol_callback(self, symbol: str, callback: Callable):
        """
        Регистрирует callback для конкретного символа.
        Тики с push.deal для этого символа будут маршрутизированы в этот callback.
        """
        formatted = self._format_symbol(symbol)
        if formatted not in self._symbol_callbacks:
            self._symbol_callbacks[formatted] = []
        self._symbol_callbacks[formatted].append(callback)
        logger.debug(f"Зарегистрирован callback для {formatted}")
    
    def remove_symbol_callbacks(self, symbol: str):
        """Удаляет все callbacks для символа."""
        formatted = self._format_symbol(symbol)
        self._symbol_callbacks.pop(formatted, None)
        logger.debug(f"Callbacks для {formatted} удалены")

    async def on_connect(self):
        """Отправляем запрос на подписку для всех символов сразу после подключения."""
        # Подписываемся на начальные символы + все зарегистрированные через callbacks
        all_symbols = set(self._format_symbol(s) for s in self._initial_symbols)
        all_symbols.update(self._symbol_callbacks.keys())
        
        for symbol in all_symbols:
            await self._subscribe_symbol(symbol)

    async def _subscribe_symbol(self, formatted_symbol: str):
        """Внутренний метод: отправить подписку на один символ."""
        msg = {
            "method": "sub.deal",
            "param": {"symbol": formatted_symbol}
        }
        logger.info(f"Подписка на {formatted_symbol}")
        await self.send_message(msg)
        self._subscribed_symbols.add(formatted_symbol)
    
    async def _unsubscribe_symbol(self, formatted_symbol: str):
        """Отписка от символа."""
        msg = {
            "method": "unsub.deal",
            "param": {"symbol": formatted_symbol}
        }
        logger.info(f"Отписка от {formatted_symbol}")
        await self.send_message(msg)
        self._subscribed_symbols.discard(formatted_symbol)

    async def subscribe_symbol(self, symbol: str):
        """Публичный метод: подписаться на символ (можно вызывать после connect)."""
        formatted = self._format_symbol(symbol)
        if formatted not in self._subscribed_symbols:
            await self._subscribe_symbol(formatted)
    
    async def unsubscribe_symbol(self, symbol: str):
        """Публичный метод: отписаться от символа."""
        formatted = self._format_symbol(symbol)
        if formatted in self._subscribed_symbols:
            await self._unsubscribe_symbol(formatted)
        self.remove_symbol_callbacks(formatted)

    async def _process_message(self, message: str | bytes):
        """
        Маршрутизация сообщений по символам.
        push.deal сообщения отправляются ТОЛЬКО в callbacks соответствующего символа.
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга JSON от MEXC: {message}")
            return

        channel = data.get("channel", "")
        
        # Пропускаем pong/служебные ответы (не логируем)
        if channel == "" and data.get("data") == "pong":
            return
        
        # Маршрутизация по символу для push.deal
        if channel == "push.deal":
            symbol = data.get("symbol", "")
            callbacks = self._symbol_callbacks.get(symbol, [])
            
            if not callbacks:
                # Символ не зарегистрирован — пропускаем (может быть задержка отписки)
                return
            
            for callback in callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Ошибка callback [{symbol}]: {e}")
        else:
            # Общие сообщения (подтверждения подписки и т.д.) — в глобальные callbacks
            for callback in self.callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Ошибка при выполнении callback: {e}")

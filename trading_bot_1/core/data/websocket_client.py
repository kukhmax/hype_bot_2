import asyncio
import json
import logging
import websockets
from typing import Callable, Optional, Dict, Any, List, Coroutine
from websockets.exceptions import ConnectionClosed

from core.logger import setup_logger

logger = setup_logger("websocket_client")

class BaseWebSocketClient:
    """
    Базовый асинхронный клиент для подключения к WebSocket биржам.
    Реализует логику:
    - Подключения и удержания соединения
    - Автоматического реконнекта с экспоненциальной задержкой (Exponential Backoff)
    - Маршрутизации входящих JSON-сообщений через callbacks
    """

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_running = False
        self.callbacks: List[Callable[[Dict[str, Any]], Any]] = []
        
        # Настройки реконнекта
        self.reconnect_delay = 1.0  # Начальная задержка (сек)
        self.max_reconnect_delay = 60.0  # Максимальная задержка (сек)
        
    def add_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        """Добавляет функцию-обработчик для входящих JSON сообщений."""
        self.callbacks.append(callback)

    async def connect(self):
        """Подключается к WebSocket и запускает цикл прослушивания (с поддержкой переподключений)."""
        self.is_running = True
        self.reconnect_delay = 1.0  # Сброс задержки при новой попытке старта
        
        while self.is_running:
            try:
                logger.info(f"Подключение к {self.url}...")
                async with websockets.connect(self.url) as ws:
                    self.ws = ws
                    logger.info("Успешно подключились к WebSocket.")
                    
                    # Сбрасываем задержку после успешного подключения
                    self.reconnect_delay = 1.0
                    
                    # Хук для отправки сообщений сразу после подключения (например, подписки на каналы)
                    await self.on_connect()
                    
                    await self._listen()
                    
            except ConnectionClosed as e:
                logger.error(f"Соединение закрыто: {e}")
            except Exception as e:
                logger.error(f"Ошибка WebSocket: {e}")
                
            if not self.is_running:
                break
                
            logger.info(f"Переподключение через {self.reconnect_delay} секунд...")
            await asyncio.sleep(self.reconnect_delay)
            # Экспоненциальное увеличение задержки
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def on_connect(self):
        """
        Метод для переопределения в дочерних классах (для конкретной биржи). 
        Вызывается сразу после успешного подключения (например, для отправки { 'method': 'SUBSCRIBE', ... }).
        """
        pass

    async def _listen(self):
        """Внутренний бесконечный цикл прослушивания массива входящих сообщений."""
        if not self.ws:
            return
            
        async for message in self.ws:
            if not self.is_running:
                break
            await self._process_message(message)

    async def _process_message(self, message: str | bytes):
        """Распарсинг и маршрутизация сообщения по всем callback-функциям."""
        try:
            data = json.loads(message)
            for callback in self.callbacks:
                try:
                    # Запускаем callback асинхронно или синхронно в зависимости от его сигнатуры
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Ошибка при выполнении callback обработчика: {e}")
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга JSON: {message}")
            
    async def send_message(self, message: Dict[str, Any]):
        """Отправка JSON-сообщения на сервер."""
        if self.ws:
            try:
                await self.ws.send(json.dumps(message))
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения: {e}")
        else:
            logger.warning("Попытка отправки сообщения, но WebSocket не открыт.")

    async def stop(self):
        """Остановка клиента."""
        self.is_running = False
        if self.ws:
            await self.ws.close()
            logger.info("WebSocket соединение закрыто.")

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
    - Ping/pong keepalive для поддержания соединения
    - Маршрутизации входящих JSON-сообщений через callbacks
    """

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_running = False
        self.callbacks: List[Callable[[Dict[str, Any]], Any]] = []
        self._ping_task: Optional[asyncio.Task] = None
        
        # Настройки реконнекта
        self.reconnect_delay = 1.0  # Начальная задержка (сек)
        self.max_reconnect_delay = 60.0  # Максимальная задержка (сек)
        
        # Настройки keepalive
        self.ping_interval = 20  # Секунд между ping-сообщениями
        
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
                async with websockets.connect(
                    self.url,
                    ping_interval=None,  # Отключаем встроенный ping — используем свой
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self.ws = ws
                    logger.info("Успешно подключились к WebSocket.")
                    
                    # Сбрасываем задержку после успешного подключения
                    self.reconnect_delay = 1.0
                    
                    # Хук для отправки сообщений сразу после подключения (подписки)
                    await self.on_connect()
                    
                    # Запускаем keepalive ping
                    self._ping_task = asyncio.create_task(self._keepalive_ping())
                    
                    try:
                        await self._listen()
                    finally:
                        # Останавливаем ping при любом выходе из listen
                        if self._ping_task and not self._ping_task.done():
                            self._ping_task.cancel()
                            try:
                                await self._ping_task
                            except asyncio.CancelledError:
                                pass
                    
            except ConnectionClosed as e:
                logger.warning(f"WebSocket соединение закрыто: code={e.code} reason={e.reason}")
            except Exception as e:
                logger.error(f"Ошибка WebSocket: {type(e).__name__}: {e}")
                
            if not self.is_running:
                break
                
            logger.info(f"Переподключение через {self.reconnect_delay:.0f} секунд...")
            await asyncio.sleep(self.reconnect_delay)
            # Экспоненциальное увеличение задержки
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def _keepalive_ping(self):
        """Периодически отправляет ping для поддержания соединения."""
        try:
            while self.is_running and self.ws:
                await asyncio.sleep(self.ping_interval)
                if self.ws and self.ws.close_code is None:
                    try:
                        await self.ws.send(json.dumps({"method": "ping"}))
                        logger.debug("Ping отправлен")
                    except Exception as e:
                        logger.warning(f"Ошибка при отправке ping: {e}")
                        break
        except asyncio.CancelledError:
            pass

    async def on_connect(self):
        """
        Метод для переопределения в дочерних классах (для конкретной биржи). 
        Вызывается сразу после успешного подключения.
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
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        if self.ws:
            await self.ws.close()
            logger.info("WebSocket соединение закрыто.")

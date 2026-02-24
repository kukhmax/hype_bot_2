import json
import asyncio
from typing import Dict, Any, Optional

from core.data.websocket_client import BaseWebSocketClient
from core.logger import setup_logger

logger = setup_logger("mexc_client")

class MEXCWebSocketClient(BaseWebSocketClient):
    """
    Клиент для MEXC Futures WebSocket API.
    Документация: https://mexcdevelop.github.io/apidocs/contract_v1_en/#websocket-api
    """
    
    # MEXC Futures WebSocket Endpoint
    WS_URL = "wss://contract.mexc.com/edge"

    def __init__(self, symbols: list[str]):
        super().__init__(self.WS_URL)
        self.symbols = symbols
        # Счетчик для ID запросов
        self._req_id = 0

    def _get_next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def on_connect(self):
        """Отправляем запрос на подписку сразу после подключения."""
        await self.subscribe_trades()

    async def subscribe_trades(self):
        """Подписываемся на поток публичных сделок (deals) для выбранных символов."""
        # У MEXC Futures тема для сделок: deal_XXX
        # Например: deal_:BTC_USDT, но символ передается без подчеркивания, если так настроено, 
        # однако по доке MEXC Futures формат обычно: "sub.deal", {"symbol": "BTC_USDT"}
        
        # Согласно текущей доке MEXC Contract V1:
        # Request: {"method":"sub.deal","param":{"symbol":"BTC_USDT"}}
        
        for symbol in self.symbols:
            # Форматируем символ, если нужно (например SOLUSDT -> SOL_USDT)
            # Если пользователь передал SOLUSDT, то преобразуем к формату MEXC
            formatted_symbol = symbol
            if "_" not in symbol and symbol.endswith("USDT"):
                formatted_symbol = f"{symbol[:-4]}_USDT"
                
            msg = {
                "method": "sub.deal",
                "param": {
                    "symbol": formatted_symbol
                }
            }
            logger.info(f"Отправка запроса на подписку: {msg}")
            await self.send_message(msg)

    async def _process_message(self, message: str | bytes):
        """
        Переопределяем базовый метод для специфичной обработки MEXC,
        например, ответа на Ping (если требуется) или обработки ответа на подписку.
        """
        # Сначала базовый парсинг
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга JSON от MEXC: {message}")
            return

        # Логируем подтверждения подписки
        # Пример ответа MEXC: {"channel":"push.deal","data":{...},"symbol":"SOL_USDT","ts":1700000000000}
        
        # Передаем дальше в зарегистрированные callback-функции
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"Ошибка при выполнении callback: {e}")

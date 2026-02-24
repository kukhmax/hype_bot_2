import hmac
import hashlib
import time
import json
from urllib.parse import urlencode
from typing import Dict, Any, Optional

import aiohttp

from core.logger import setup_logger
from core.config import settings

logger = setup_logger("mexc_executor")

class MEXCExecutor:
    """
    Модуль для взаимодействия с MEXC REST API (v3).
    Отвечает за:
    1. Расчет подписей.
    2. Получение баланса.
    3. Выставление и отмену ордеров (Спот).
    """

    BASE_URL = "https://api.mexc.com"

    def __init__(self):
        self.api_key = settings.API_KEY
        self.api_secret = settings.API_SECRET
        self.session: Optional[aiohttp.ClientSession] = None

    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    def _generate_signature(self, query_string: str) -> str:
        """Генерация HMAC SHA256 подписи."""
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    async def _request(self, method: str, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Универсальная функция для отправки подписанных запросов."""
        await self.init_session()
        
        if params is None:
            params = {}
            
        # Добавляем временную метку (требование MEXC)
        params["timestamp"] = int(time.time() * 1000)
        # Окно получения (recvWindow)
        params["recvWindow"] = 5000 
        
        # Сначала кодируем параметры в строку
        query_string = urlencode(params)
        
        # Если API ключи пустые (мы тестируем), не подписываем по-настоящему, 
        # но код должен отработать.
        if self.api_secret:
            signature = self._generate_signature(query_string)
            query_string += f"&signature={signature}"
            
        url = f"{self.BASE_URL}{endpoint}?{query_string}"
        
        headers = {
            "X-MEXC-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
        
        try:
            async with self.session.request(method, url, headers=headers) as response:
                resp_data = await response.json()
                if response.status != 200:
                    logger.error(f"Ошибка API (Status {response.status}): {resp_data}")
                return resp_data
        except Exception as e:
            logger.error(f"Сетевая ошибка при вызове {endpoint}: {e}")
            return {}

    async def get_balance(self, asset: str = "USDT") -> float:
        """Получение свободного баланса для заданного актива."""
        if not self.api_key:
            logger.warning("API_KEY не установлен. Возвращаю фейковый баланс 1000 USDT для тестов.")
            return 1000.0
            
        resp = await self._request("GET", "/api/v3/account")
        balances = resp.get("balances", [])
        
        for bal in balances:
            if bal["asset"] == asset:
                return float(bal["free"])
        
        return 0.0

    async def place_market_order(self, symbol: str, side: str, quote_quantity: float) -> Dict[str, Any]:
        """
        Отправка рыночного ордера.
        quoteOrderQty - объем в USDT (а не в крипте).
        """
        logger.info(f"Отправка Market {side} ордера для {symbol} на сумму {quote_quantity} USDT")
        
        params = {
            "symbol": symbol,
            "side": side, # "BUY" или "SELL"
            "type": "MARKET",
            "quoteOrderQty": quote_quantity
        }
        return await self._request("POST", "/api/v3/order", params)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict[str, Any]:
        """
        Отправка лимитного ордера (обычно используется для SL/TP).
        Здесь quantity - объем в БАЗОВОЙ валюте (например, кол-во SOL).
        """
        logger.info(f"Отправка Limit {side} ордера для {symbol}: {quantity} по цене {price}")
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC", # Good Till Cancel
            "quantity": quantity,
            "price": price
        }
        return await self._request("POST", "/api/v3/order", params)
        
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Отмена ордера."""
        logger.info(f"Отмена ордера {order_id} для {symbol}")
        params = {
            "symbol": symbol,
            "orderId": order_id
        }
        return await self._request("DELETE", "/api/v3/order", params)

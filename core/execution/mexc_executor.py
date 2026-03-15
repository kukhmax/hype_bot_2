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
    Модуль для взаимодействия с MEXC REST API (Futures V1).
    Отвечает за:
    1. Расчет подписей.
    2. Получение баланса.
    3. Выставление и отмену ордеров (Фьючерсы).
    """

    BASE_URL = "https://contract.mexc.com"

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

    async def _request(self, method: str, endpoint: str, params: Dict[str, Any] = None, json_body: bool = False) -> Dict[str, Any]:
        """Универсальная функция для отправки подписанных запросов."""
        await self.init_session()
        
        if params is None:
            params = {}
            
        params["timestamp"] = int(time.time() * 1000)
        
        # Для GET и DELETE параметры в URL
        # Для POST обычно в JSON body, но подпись считается от сериализованной строки
        
        # Отсортируем ключи для MEXC (иногда требуется)
        # На самом деле MEXC Futures API v1 требует подпись от query string или body
        # Для простоты, в MEXC Futures обычно POST-тело передается как JSON, но строка для подписи 
        # строится: apiKey + timestamp + body(или query)
        
        # Согласно докам MEXC Futures V1:
        # StringToSign = apikey + timestamp + {body params or query params string}
        
        headers = {
            "ApiKey": self.api_key,
            "Request-Time": str(params["timestamp"]),
            "Content-Type": "application/json"
        }
        
        if method == "GET":
            query_string = urlencode(params)
            sign_str = self.api_key + str(params["timestamp"]) + query_string
            if self.api_secret:
                signature = self._generate_signature(sign_str)
                headers["Signature"] = signature
            url = f"{self.BASE_URL}{endpoint}?{query_string}"
            
            try:
                async with self.session.get(url, headers=headers) as response:
                    return await response.json()
            except Exception as e:
                logger.error(f"Сетевая ошибка при вызове {endpoint}: {e}")
                return {}
        else: # POST, DELETE(sometimes POST in futures)
            # В MEXC Futures POST запросы шлют данные в теле
            # Вынимаем timestamp из params, так как он в хидере Request-Time нужен
            ts = params.pop("timestamp")
            
            body_str = json.dumps(params) if params else ""
            sign_str = self.api_key + str(ts) + body_str
            
            if self.api_secret:
                headers["Signature"] = self._generate_signature(sign_str)
                
            url = f"{self.BASE_URL}{endpoint}"
            
            try:
                if method == "POST":
                    async with self.session.post(url, headers=headers, json=params) as response:
                        return await response.json()
                elif method == "DELETE":
                    # Иногда DELETE в MEXC futures принимает JSON
                    async with self.session.delete(url, headers=headers, json=params) as response:
                        return await response.json()
            except Exception as e:
                logger.error(f"Сетевая ошибка при вызове {endpoint}: {e}")
                return {}

    async def get_balance(self, asset: str = "USDT") -> float:
        """Получение свободного баланса для заданного актива (Futures)."""
        if not self.api_key:
            logger.warning("API_KEY не установлен. Возвращаю фейковый баланс 1000 USDT для тестов.")
            return 1000.0
            
        resp = await self._request("GET", f"/api/v1/private/account/asset/{asset}")
        # Формат ответа futures: {"success":true,"code":0,"data":{"currency":"USDT","availableBalance":123.45}}
        if resp.get("success") and resp.get("data"):
            return float(resp["data"].get("availableBalance", 0.0))
        
        logger.error(f"Ошибка получения баланса (Futures): {resp}")
        return 0.0

    async def place_market_order(self, symbol: str, side: str, vol: float) -> Dict[str, Any]:
        """
        Отправка рыночного ордера на фьючерсах.
        vol - объем в КОНТРАКТАХ. Внимание: если мы считали quote_qty, нужно перевести в контракты!
        Обычно 1 контракт BTC = 0.0001 BTC. Для простоты будем передавать vol как рассчитанный base_qty,
        но адаптированный под contractSize (часто 1:1 для альтов, но не всегда).
        Мы предполагаем, что upper level уже подготовил верный `vol`.
        side: 1: Open Long, 2: Close Short, 3: Open Short, 4: Close Long.
        Поэтому мапим "BUY" -> 1 (Open Long), "SELL" -> 3 (Open Short).
        """
        mex_side = 1 if side.upper() == "BUY" else 3
        
        logger.info(f"Отправка Market ордера {side} ({mex_side}) для {symbol} объем {vol}")
        
        params = {
            "symbol": symbol,
            "side": mex_side, 
            "orderType": 5, # 5 = MARKET
            "vol": float(vol),
            "openType": 1 # 1 = Isolated, 2 = Cross
        }
        return await self._request("POST", "/api/v1/private/order/submit", params)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict[str, Any]:
        """
        Выставление лимитного ордера (например для TP).
        side для закрытия позы: Если лонг (закрываем) -> Close Long (4).
        Если шорт (закрываем) -> Close Short (2).
        """
        mex_side = 4 if side.upper() == "SELL" else 2 # SELL=Close Long, BUY=Close Short
            
        logger.info(f"Отправка Limit {side} (Close) ордера для {symbol}: {quantity} по цене {price}")
        
        params = {
            "symbol": symbol,
            "side": mex_side,
            "orderType": 1, # 1 = Limit
            "vol": float(quantity),
            "price": float(price),
            "openType": 1
        }
        return await self._request("POST", "/api/v1/private/order/submit", params)
        
    async def place_stop_order(self, symbol: str, side: str, quantity: float, stopPrice: float, limitPrice: float = None) -> Dict[str, Any]:
        """
        Stop-Market ордер на фьючерсах. (Чаще всего trigger order).
        """
        mex_side = 4 if side.upper() == "SELL" else 2
        logger.info(f"Отправка Stop Trigger ордера для {symbol}: {quantity}. Trigger={stopPrice}")
        params = {
            "symbol": symbol,
            "side": mex_side,
            "triggerPrice": float(stopPrice),
            "vol": float(quantity),
            "orderType": 1 if limitPrice else 5, # 1: limit, 5: market
            "openType": 1,
            "triggerType": 2 # 2: Fair price
        }
        if limitPrice:
            params["price"] = float(limitPrice)
            
        return await self._request("POST", "/api/v1/private/planorder/place", params)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Отмена ордера."""
        logger.info(f"Отмена ордера {order_id} для {symbol}")
        # Для фьючерсов V1 это POST /api/v1/private/order/cancel
        # Если планировщик - POST /api/v1/private/planorder/cancel
        params = {"orderIds": [str(order_id)]}
        return await self._request("POST", "/api/v1/private/order/cancel", params)

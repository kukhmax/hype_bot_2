import asyncio
import websockets
import json
import os
import logging
from typing import Dict, List, Callable, Optional
import numpy as np
from datetime import datetime

class HyperLiquidWebSocket:
    def __init__(self):
        self.ws_url = "wss://api.hyperliquid.xyz/ws"
        self.subscriptions = {}  # token -> {timeframe: callback}
        self.candle_data = {}  # token_timeframe -> {timestamps, opens, highs, lows, closes, volumes}
        self.logger = logging.getLogger(__name__)
        
    async def connect(self):
        """Подключение к WebSocket HyperLiquid"""
        self.logger.info("Connecting to HyperLiquid WS: %s", self.ws_url)
        self.websocket = await websockets.connect(self.ws_url)
        asyncio.create_task(self._listen())
    
    async def subscribe_candles(self, token: str, timeframe: str, callback: Callable):
        """Подписка на свечные данные"""
        key = f"{token}_{timeframe}"
        
        if key not in self.subscriptions:
            self.logger.info(
                "Subscribing to candles: token=%s timeframe=%s key=%s",
                token,
                timeframe,
                key,
            )
            self.subscriptions[key] = []
            
            # Формируем запрос на подписку
            subscribe_msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "candle",
                    "coin": token,
                    "interval": timeframe
                }
            }
            
            await self.websocket.send(json.dumps(subscribe_msg))
        
        self.subscriptions[key].append(callback)
        
        # Инициализируем хранилище свечей
        if key not in self.candle_data:
            self.candle_data[key] = {
                'timestamps': [],
                'opens': [],
                'highs': [],
                'lows': [],
                'closes': [],
                'volumes': []
            }
            self.logger.info("Initialized candle storage for %s", key)
        else:
            count = len(self.candle_data[key]['timestamps'])
            if count:
                try:
                    start_dt = datetime.fromtimestamp(self.candle_data[key]['timestamps'][0] / 1000)
                    end_dt = datetime.fromtimestamp(self.candle_data[key]['timestamps'][-1] / 1000)
                    self.logger.info(
                        "Existing candle history for %s: count=%d range=%s -> %s",
                        key,
                        count,
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                    )
                except Exception:
                    self.logger.info(
                        "Existing candle history for %s: count=%d ts_from=%s ts_to=%s",
                        key,
                        count,
                        self.candle_data[key]['timestamps'][0],
                        self.candle_data[key]['timestamps'][-1],
                    )
    
    async def unsubscribe(self, token: str, timeframe: str, callback: Callable):
        """Отписка от свечей"""
        key = f"{token}_{timeframe}"
        if key in self.subscriptions:
            if callback in self.subscriptions[key]:
                self.subscriptions[key].remove(callback)
    
    async def _listen(self):
        """Прослушивание входящих сообщений"""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                
                # Обрабатываем только свечные данные
                if data.get('channel') == 'candle':
                    self._process_candle(data['data'])
                    
        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket connection closed. Reconnecting...")
            await asyncio.sleep(5)
            await self.connect()
    
    def _process_candle(self, candle_data: Dict):
        """Обработка полученной свечи"""
        coin = candle_data.get('coin') or candle_data.get('s')
        timeframe = candle_data.get('interval') or candle_data.get('i')
        if not coin or not timeframe:
            self.logger.warning("Unexpected candle payload keys=%s", list(candle_data.keys()))
            return
        key = f"{coin}_{timeframe}"
        
        if key in self.candle_data:
            # Добавляем новую свечу
            self.candle_data[key]['timestamps'].append(candle_data['t'])
            self.candle_data[key]['opens'].append(float(candle_data['o']))
            self.candle_data[key]['highs'].append(float(candle_data['h']))
            self.candle_data[key]['lows'].append(float(candle_data['l']))
            self.candle_data[key]['closes'].append(float(candle_data['c']))
            self.candle_data[key]['volumes'].append(float(candle_data['v']))
            count = len(self.candle_data[key]['timestamps'])
            try:
                start_dt = datetime.fromtimestamp(self.candle_data[key]['timestamps'][0] / 1000)
                end_dt = datetime.fromtimestamp(self.candle_data[key]['timestamps'][-1] / 1000)
                self.logger.info(
                    "New candle %s: total=%d range=%s -> %s close=%.4f",
                    key,
                    count,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    self.candle_data[key]['closes'][-1],
                )
            except Exception:
                self.logger.info(
                    "New candle %s: total=%d ts_from=%s ts_to=%s close=%.4f",
                    key,
                    count,
                    self.candle_data[key]['timestamps'][0],
                    self.candle_data[key]['timestamps'][-1],
                    self.candle_data[key]['closes'][-1],
                )
            
            # Ограничиваем историю
            max_candles = int(os.getenv("MAX_CANDLES_HISTORY", 200))
            for arr in self.candle_data[key].values():
                if len(arr) > max_candles:
                    arr.pop(0)
            
            # Вызываем колбэки
            if key in self.subscriptions:
                for callback in self.subscriptions[key]:
                    asyncio.create_task(callback(self.candle_data[key]))
    
    def get_candle_array(self, token: str, timeframe: str, field: str) -> np.array:
        """Получение массива свечей для анализа"""
        key = f"{token}_{timeframe}"
        if key in self.candle_data:
            return np.array(self.candle_data[key][field])
        return np.array([])

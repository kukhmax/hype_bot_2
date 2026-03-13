import asyncio
import json
import websockets
from typing import Callable, Dict, List

from core.logger import setup_logger

logger = setup_logger("hyperliquid_ws")

class HyperliquidWebSocketClient:
    """
    Общий WebSocket клиент для Hyperliquid.
    Один коннект для всех пар, подписывается на trades.
    Транслирует данные в том же формате `push.deal`, как это делал MEXC.
    """
    
    # https://api.hyperliquid.xyz/ws
    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(self):
        self.ws = None
        self.callbacks: Dict[str, List[Callable]] = {}
        self.subscribed_symbols = set()
        self._ping_task = None
        self._running = False

    def add_symbol_callback(self, symbol: str, callback: Callable):
        if symbol not in self.callbacks:
            self.callbacks[symbol] = []
        if callback not in self.callbacks[symbol]:
            self.callbacks[symbol].append(callback)

    def _format_symbol(self, symbol: str) -> str:
        """Hyperliquid uses standard coin names like 'BTC', 'ETH' for trades stream"""
        for suffix in ["_USDC", "_USDT", "USDC", "USDT"]:
            if symbol.endswith(suffix):
                return symbol[:-len(suffix)]
        return symbol

    async def connect(self):
        """Подключение и бесконечный цикл чтения."""
        self._running = True
        while self._running:
            try:
                logger.info(f"Подключение к Hyperliquid WS: {self.WS_URL}")
                async with websockets.connect(self.WS_URL, ping_interval=None) as ws:
                    self.ws = ws
                    logger.info("Успешное подключение к Hyperliquid WS")
                    
                    # Восстанавливаем подписки при переподключении
                    for sys_sym in list(self.subscribed_symbols):
                         await self._send_subscribe(sys_sym)
                    
                    self._ping_task = asyncio.create_task(self._send_ping())
                    
                    async for message in ws:
                        if not self._running:
                            break
                        self._process_message(message)
                        
            except websockets.ConnectionClosed as e:
                logger.warning(f"Hyperliquid WS отключился (code {e.code}). Реконнект через 5 сек...")
            except Exception as e:
                logger.error(f"Ошибка в WS цикле Hyperliquid: {e}")
                
            if self.ws:
                self.ws = None
            if self._ping_task:
                 self._ping_task.cancel()
                 
            if self._running:
                await asyncio.sleep(5)

    async def _send_ping(self):
        """Hyperliquid WS requires periodic ping messages."""
        while self._running and self.ws:
            try:
                await self.ws.send(json.dumps({"method": "ping"}))
                await asyncio.sleep(50)  # Ping every 50 seconds
            except Exception:
                break

    async def subscribe_symbol(self, symbol: str):
        self.subscribed_symbols.add(symbol)
        if self.ws and not self.ws.closed:
             await self._send_subscribe(symbol)
        logger.info(f"[{symbol}] Добавлен в подписки Hyperliquid WS")

    async def unsubscribe_symbol(self, symbol: str):
        if symbol in self.subscribed_symbols:
            self.subscribed_symbols.remove(symbol)
        
        if self.ws and not self.ws.closed:
            hl_symbol = self._format_symbol(symbol)
            msg = {
                "method": "unsubscribe",
                "subscription": {
                    "type": "trades",
                    "coin": hl_symbol
                }
            }
            try:
                await self.ws.send(json.dumps(msg))
            except Exception as e:
                logger.error(f"Ошибка отписки от {symbol}: {e}")

    async def _send_subscribe(self, symbol: str):
         hl_symbol = self._format_symbol(symbol)
         msg = {
             "method": "subscribe",
             "subscription": {
                 "type": "trades",
                 "coin": hl_symbol
             }
         }
         try:
             await self.ws.send(json.dumps(msg))
         except Exception as e:
             logger.error(f"Ошибка отправки подписки для {symbol}: {e}")

    def _process_message(self, message: str):
        try:
            data = json.loads(message)
            channel = data.get("channel")
            if channel == "trades":
                trades_data = data.get("data", [])
                
                # Group by coin since a single message contains trades for a specific subscription
                # Format to match MEXC deal push format
                for trade in trades_data:
                    hl_coin = trade.get("coin")
                    
                    # reconstruct the bot's internal symbol format BTC_USDC
                    sys_symbol = f"{hl_coin}_USDC" 
                    if sys_symbol not in self.callbacks:
                         sys_symbol = f"{hl_coin}_USDT" # fallback for mixed usage if any                    
                    if sys_symbol in self.callbacks:
                        mexc_format_deal = {
                             "p": trade.get("px"),
                             "v": trade.get("sz"),
                             "t": trade.get("time") # MS timestamp
                        }
                        mock_mexc_msg = {
                             "channel": "push.deal",
                             "data": [mexc_format_deal]
                        }
                        
                        for cb in self.callbacks[sys_symbol]:
                            try:
                                cb(mock_mexc_msg)
                            except Exception as e:
                                logger.error(f"Ошибка в callback: {e}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Ошибка обработки WS сообщения: {e} | {message}")

    async def stop(self):
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
        if self.ws:
            await self.ws.close()
            self.ws = None

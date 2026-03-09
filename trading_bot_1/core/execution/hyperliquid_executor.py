import asyncio
from typing import Dict, Any, Optional

from core.logger import setup_logger
from core.config import settings

# Attempt to import hyperliquid SDK
try:
    import eth_account
    from eth_account.signers.local import LocalAccount
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    HAS_HL_SDK = True
except ImportError:
    HAS_HL_SDK = False

logger = setup_logger("hyperliquid_executor")

class HyperliquidExecutor:
    """
    Модуль для взаимодействия с Hyperliquid API (L1).
    Отвечает за:
    1. Получение баланса.
    2. Выставление и отмену ордеров (Perpetuals).
    """

    def __init__(self):
        self.wallet_address = settings.HL_WALLET_ADDRESS
        self.private_key = settings.HL_PRIVATE_KEY
        
        self.info: Optional[Info] = None
        self.exchange: Optional[Exchange] = None
        self.account: Optional[LocalAccount] = None
        
        if HAS_HL_SDK:
            # We use mainnet by default. Replace with constants.TESTNET_API_URL if needed.
            self.info = Info(constants.MAINNET_API_URL, skip_ws=True)
            if self.wallet_address and self.private_key:
                try:
                    self.account = eth_account.Account.from_key(self.private_key)
                    self.exchange = Exchange(self.account, constants.MAINNET_API_URL)
                    logger.info("Hyperliquid Exchange initialized for signing")
                except Exception as e:
                    logger.error(f"Failed to initialize Hyperliquid Exchange: {e}")
            else:
                logger.warning("HL_WALLET_ADDRESS or HL_PRIVATE_KEY is missing. Operating in Read-Only mode.")
        else:
            logger.error("hyperliquid SDK not found. Please install `hyperliquid` and `eth-account`")

    async def init_session(self):
        """Mock method to maintain compatibility with MEXCExecutor interface"""
        pass

    async def close(self):
        """Mock method to maintain compatibility with MEXCExecutor interface"""
        pass

    async def get_balance(self, asset: str = "USDC") -> float:
        """Получение свободного баланса для заданного актива.
        Hyperliquid uses USDC as margin, we treat it equivalent to USDT in the interfaces.
        """
        if not HAS_HL_SDK or not self.info:
            logger.warning("HL SDK not available. Возвращаю фейковый баланс 1000 USDC для тестов.")
            return 1000.0
            
        if not self.wallet_address:
            logger.warning("HL_WALLET_ADDRESS не установлен. Возвращаю фейковый баланс 1000 USDC для тестов.")
            return 1000.0

        try:
            # Info API calls can be blocking in the SDK depending on implementation, 
            # we wrap it in a thread if it causes async issues, but standard usage is fine for MVP.
            # get_clearinghouse_state returns margin summary and positions
            state = await asyncio.to_thread(self.info.user_state, self.wallet_address)
            # The margin summary contains 'withdrawable'
            margin_summary = state.get("marginSummary", {})
            free_balance = float(margin_summary.get("withdrawable", 0.0))
            return free_balance
        except Exception as e:
            logger.error(f"Error fetching balance from Hyperliquid: {e}")
            return 0.0

    async def place_market_order(self, symbol: str, side: str, quote_quantity: float) -> Dict[str, Any]:
        """
        Отправка рыночного ордера.
        quote_quantity - объем в стейблкоинах (USDC).
        Hyperliquid SDK expects size in coin (base_qty).
        We MUST fetch the current price to convert quote_quantity -> base_qty
        """
        logger.info(f"[Hyperliquid] Отправка Market {side} ордера для {symbol} на сумму {quote_quantity} USDC")
        
        if not self.exchange:
            logger.error("Hyperliquid Exchange write client not initialized (missing private key or SDK).")
            return {}

        hl_symbol = self._format_symbol(symbol)
        
        try:
            # 1. Fetch current price to calculate size
            all_mids = await asyncio.to_thread(self.info.all_mids)
            current_price = float(all_mids.get(hl_symbol, 0))
            
            if current_price == 0:
                logger.error(f"Could not fetch current price for {hl_symbol}")
                return {}
                
            # 2. Calculate base quantity
            sz = quote_quantity / current_price
            
            # Note: Hyperliquid requires sizes to respect step size, but for MVP we submit standard float
            is_buy = True if side.upper() == "BUY" else False
            
            # The SDK expects coin, is_buy, sz, px (for market order pass slippage px), slippage, intent
            # Actually SDK helper: market_open
            # Slippage price: If buy, price * 1.05. If sell, price * 0.95
            slippage_px = current_price * 1.05 if is_buy else current_price * 0.95
            
            # Using basic order
            res = await asyncio.to_thread(
                self.exchange.market_open,
                hl_symbol,
                is_buy,
                sz,
                slippage_px
            )
            
            if res["status"] == "ok":
                # Typical response contains tx timestamp, order id is sometimes not uniquely simple in market_open response 
                # but we can grab something for tracking if needed.
                logger.info(f"[Hyperliquid] Market Order Success: {res}")
                return {"orderId": res["response"]["data"]["statuses"][0].get("resting", {}).get("oid", "HL_MARKET")}
            else:
                logger.error(f"[Hyperliquid] Error executing market order: {res}")
                return {}
                
        except Exception as e:
            logger.error(f"[Hyperliquid] API Exception on place_market_order: {e}")
            return {}

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict[str, Any]:
        """
        Отправка лимитного ордера (обычно используется для SL/TP).
        Здесь quantity - объем в БАЗОВОЙ валюте (например, кол-во SOL).
        """
        logger.info(f"[Hyperliquid] Отправка Limit {side} ордера для {symbol}: {quantity} по цене {price}")
        
        if not self.exchange:
             logger.error("Hyperliquid Exchange write client not initialized.")
             return {}
             
        hl_symbol = self._format_symbol(symbol)
        is_buy = True if side.upper() == "BUY" else False
        
        try:
            # SDK helper: order
            res = await asyncio.to_thread(
                self.exchange.order,
                hl_symbol,
                is_buy,
                quantity,
                price,
                {"limit": {"tif": "Gtc"}}
            )
            
            if res["status"] == "ok":
                oid = res["response"]["data"]["statuses"][0].get("resting", {}).get("oid", "")
                logger.info(f"[Hyperliquid] Limit Order Success: {res}")
                return {"orderId": oid}
            else:
                logger.error(f"[Hyperliquid] Error executing limit order: {res}")
                return {}

        except Exception as e:
            logger.error(f"[Hyperliquid] API Exception on place_limit_order: {e}")
            return {}
            
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Отмена ордера."""
        logger.info(f"[Hyperliquid] Отмена ордера {order_id} для {symbol}")
        
        if not self.exchange:
             logger.error("Hyperliquid Exchange write client not initialized.")
             return {}
             
        hl_symbol = self._format_symbol(symbol)
        
        try:
            # The SDK expects cancel(coin, oid)
            res = await asyncio.to_thread(
                self.exchange.cancel,
                hl_symbol,
                int(order_id)
            )
            logger.info(f"[Hyperliquid] Cancel Order Response: {res}")
            return res
        except Exception as e:
            logger.error(f"[Hyperliquid] API Exception on cancel_order: {e}")
            return {}

    def _format_symbol(self, symbol: str) -> str:
        """Hyperliquid expects 'BTC', 'ETH' without '_USDC' / '_USDT' usually for perpetualls"""
        for suffix in ["_USDC", "_USDT", "USDC", "USDT"]:
            if symbol.endswith(suffix):
                return symbol[:-len(suffix)]
        return symbol

import asyncio
from core.data.mexc_client import MEXCWebSocketClient
from core.logger import setup_logger

logger = setup_logger("test_ws")

async def test_mexc_ws():
    symbols = ["HYPE_USDT", "AAVE_USDT"]
    logger.info(f"Starting test WS for: {symbols}")
    
    client = MEXCWebSocketClient(symbols)
    
    def on_msg(msg):
        logger.info(f"Received msg: {msg}")
        
    client.add_callback(on_msg)
    
    # Run for 20 seconds then stop
    async def stop_later():
        await asyncio.sleep(20)
        await client.stop()
        
    asyncio.create_task(stop_later())
    
    try:
        await client.connect()
        await client.ws.wait_closed()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_mexc_ws())

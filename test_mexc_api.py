import asyncio
import aiohttp
from core.execution.mexc_executor import MEXCExecutor

async def main():
    executor = MEXCExecutor()
    await executor.init_session()
    # Check exchange info
    res = await executor._request("GET", "/api/v3/exchangeInfo")
    
    # Just print orderTypes for the first symbol to see if OCO or STOP_LOSS are supported
    if "symbols" in res and len(res["symbols"]) > 0:
        print("Order types:", res["symbols"][0].get("orderTypes"))
    
    await executor.close()

asyncio.run(main())

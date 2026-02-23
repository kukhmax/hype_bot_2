import asyncio
import websockets
async def test_mexc():
    urls = ["wss://contract.mexc.com/ws", "wss://contract.mexc.com/edge"]
    for url in urls:
        try:
            print(f"Connecting to {url}")
            async with websockets.connect(url) as ws:
                print(f"Success: {url}")
        except Exception as e:
            print(f"Failed {url}: {e}")
asyncio.run(test_mexc())

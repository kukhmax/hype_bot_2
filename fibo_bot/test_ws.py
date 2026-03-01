import asyncio
import websockets
import json

async def test():
    async with websockets.connect("wss://contract.mexc.com/edge") as ws:
        await ws.send(json.dumps({
            "method": "sub.kline",
            "param": {"symbol": "SOL_USDT", "interval": "Min1"}
        }))
        while True:
            msg = await ws.recv()
            if "push.kline" in msg:
                print(msg)
                break

asyncio.run(test())

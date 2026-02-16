import asyncio
import websockets
import json
from app.config import WS_URL
from app.services.signal_engine import send_signal

async def ws_loop(bot):

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:

                # subscribe logic

                while True:
                    msg = await ws.recv()

                    # indicator calculation
                    # если есть сигнал:
                    # await send_signal(...)
        except:
            await asyncio.sleep(5)

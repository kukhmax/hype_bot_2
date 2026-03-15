import asyncio
import aiohttp
import time
import hmac
import hashlib

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://contract.mexc.com/api/v1/contract/detail") as response:
            print("Status:", response.status)
            data = await response.json()
            if data.get("success") and data.get("data"):
                print("First symbol:", data["data"][0].get("symbol"))

asyncio.run(main())

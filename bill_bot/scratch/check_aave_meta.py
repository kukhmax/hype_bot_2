import asyncio
import os
import json
import aiohttp

async def main():
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "meta"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            uni = data.get("universe", [])
            for it in uni:
                if it.get("name") == "AAVE":
                    print(json.dumps(it, indent=2))
                    break

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
import json
from eth_account.signers.local import LocalAccount
import eth_account
from hyperliquid.info import Info
from hyperliquid.utils import constants

async def main():
    key = os.getenv("HL_PRIVATE_KEY")
    if not key:
        print("HL_PRIVATE_KEY not found")
        return
    account: LocalAccount = eth_account.Account.from_key(key)
    address = account.address
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    
    print(f"Fetching open orders for {address}...")
    orders = info.frontend_open_orders(address)
    print(json.dumps(orders, indent=2))

if __name__ == "__main__":
    asyncio.run(main())

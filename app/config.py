import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_REST = "https://api.hyperliquid.xyz"

MAX_SUBSCRIPTIONS = 3
RATE_LIMIT_SECONDS = 1

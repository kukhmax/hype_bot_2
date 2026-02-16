import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
WS_URL = "wss://api.hyperliquid.xyz/ws"

MAX_SUBSCRIPTIONS = 3

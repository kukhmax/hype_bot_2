import os

# --- НАСТРОЙКИ TELEGRAM ---
# Вставьте сюда токен вашего бота, полученный от @BotFather
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "ВСТАВЬТЕ_ВАШ_ТОКЕН_СЮДА")

# Ваш ID в Telegram (чтобы бот слал сигналы только вам или в вашу группу)
# Можно узнать у бота @userinfobot
ADMIN_ID = int(os.getenv("TG_ADMIN_ID", "123456789"))


# --- НАСТРОЙКИ СТРАТЕГИИ ---
# Список пар для мониторинга на Hyperliquid (без USDT)
MONITORED_PAIRS = [
    "BTC", "ETH", "SOL", "HYPE", "LTC", "DOT", "XRP", "ADA", "LINK", "UNI"
]

TIMEFRAME = "15m"

# Настройки EMA для перебора
EMA_CANDIDATES = [50, 75, 100, 125, 150, 175, 200, 250]

# Настройки алгоритма выбора "лучшей EMA"
HISTORY_FOR_EMA_SCORING = 300 # Сколько свечей анализировать для выбора EMA
EMA_TOLERANCE_PCT = 0.002     # Зона допуск 0.2%

# Настройки индикаторов
CCI_LENGTH = 14
CCI_LEVEL = 100
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Настройки Риск-Менеджмента
RR_TP1 = 1.5  # Соотношение Риск:Прибыль для первой цели
RR_TP2 = 3.0  # Соотношение Риск:Прибыль для второй цели
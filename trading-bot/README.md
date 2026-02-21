# 📈 EMA Channel Breakout — Trading Signal Bot

Telegram-бот для поиска торговых сетапов на Hyperliquid в реальном времени.  
Стратегия: пробой канала EMA20(High/Low) с подтверждением структуры и фильтрацией через DeepSeek AI.

---

## Стратегия (кратко)

```
LONG:  close > EMA20_High  AND  ADX > 20  AND  +DI > -DI
       AND  PrevHigh > EMA20_High  AND  close > PrevHigh

SHORT: close < EMA20_Low   AND  ADX > 20  AND  -DI > +DI
       AND  PrevLow < EMA20_Low   AND  close < PrevLow
```

Каждый сигнал проходит AI-фильтр DeepSeek, который проверяет объём,
свечную структуру и выносит вердикт: `AGGRESSIVE_ENTRY / CAUTIOUS_ENTRY / SKIP`.

---

## Стек

| Компонент | Технология |
|---|---|
| Telegram Bot | aiogram 3.x (asyncio) |
| Хранилище | Redis 7 (свечи, подписки, FSM, кулдаун) |
| Рыночные данные | Hyperliquid WebSocket + REST |
| AI анализ | DeepSeek Chat API (JSON mode) |
| Индикаторы | numpy (EMA, ADX, swing levels) |
| Деплой | Docker + Docker Compose |

---

## Быстрый старт

### 1. Клонировать и настроить окружение

```bash
git clone <repo>
cd trading-bot
cp .env.example .env
```

Заполнить `.env`:

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
REDIS_URL=redis://redis:6379/0
DEEPSEEK_API_KEY=sk-...
```

### 2. Запустить

```bash
docker-compose up -d --build
```

### 3. Проверить логи

```bash
docker-compose logs -f bot
```

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather | — |
| `REDIS_URL` | URL подключения к Redis | `redis://redis:6379/0` |
| `DEEPSEEK_API_KEY` | Ключ DeepSeek API | — |
| `DEEPSEEK_MODEL` | Модель DeepSeek | `deepseek-chat` |
| `HL_WS_URL` | WebSocket Hyperliquid | `wss://api.hyperliquid.xyz/ws` |
| `MIN_CANDLES` | Минимум свечей для расчёта | `50` |
| `EMA_PERIOD` | Период EMA канала | `20` |
| `ADX_PERIOD` | Период ADX | `14` |
| `ADX_THRESHOLD` | Минимальный ADX для сигнала | `20` |

---

## Структура проекта

```
trading-bot/
├── main.py                    # Точка входа
├── config.py                  # Все настройки из .env
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── core/
│   ├── strategy.py            # ★ Оркестрация стратегии (Strategy, StrategyContext)
│   ├── indicators.py          # EMA, ADX, swing levels, check_setup()
│   ├── worker.py              # Пайплайн: свеча → стратегия → AI → сигнал
│   ├── hyperliquid_ws.py      # WebSocket клиент + REST для истории
│   ├── deepseek_client.py     # Промпт + парсинг JSON ответа DeepSeek
│   └── redis_client.py        # CRUD для подписок, свечей, кулдауна
│
└── bot/
    ├── handlers/
    │   ├── start.py           # /start + главное меню
    │   ├── subscribe.py       # FSM: ввод токена → TF → подписка
    │   └── subscriptions.py   # Список подписок, отмена, навигация
    ├── keyboards.py           # Все InlineKeyboard
    └── messages.py            # Тексты сообщений (константы)
```

---

## Поток данных

```
Hyperliquid WS
    │
    │  (закрытая свеча OHLCV)
    ▼
core/worker.py :: on_candle()
    │
    ├─► Redis: push_candle()         — сохраняем свечу в буфер
    │
    ├─► Strategy.evaluate()
    │       │
    │       ├─► indicators.py        — EMA, ADX, swing levels
    │       └─► check_setup()        — проверка условий LONG/SHORT
    │
    ├─► [нет сигнала] → выход
    │
    ├─► Redis: is_on_cooldown()      — защита от спама
    │
    ├─► DeepSeek API                 — AI фильтр + расчёт SL/TP
    │       └─► verdict: AGGRESSIVE / CAUTIOUS / SKIP
    │
    ├─► [SKIP] → выход
    │
    ├─► Redis: set_cooldown()        — кулдаун 5 минут
    │
    └─► Bot.send_message()           — сигнал пользователю
```

---

## Формат сигнала в Telegram

```
🟢 СИГНАЛ: LONG | ETH | M5
━━━━━━━━━━━━━━━━━━━━━
🤖 Вход: ⚡️ Агрессивный
📊 Уверенность AI: 78%

📍 Вход: 3245.10 – 3248.60
🛑 Стоп-лосс: 3221.40
🎯 TP1: 3285.00  (RR 1.7:1)
🎯 TP2: 3320.00  (RR 2.8:1)

📈 ADX: 26.4  +DI 28.1 / -DI 14.3
📦 Объём: 1.8x от среднего

💬 Пробой структурного максимума с объёмом выше среднего...
━━━━━━━━━━━━━━━━━━━━━
⏱ Таймфрейм: M5 | 🔗 Hyperliquid
```

---

## Расширение стратегии

Для добавления нового фильтра — только `core/strategy.py` и `core/indicators.py`.  
Для изменения промпта AI — только `core/deepseek_client.py`.  
Для нового источника данных (Binance, Bybit) — новый файл по образцу `core/hyperliquid_ws.py`.

---

## Лицензия

MIT — используй как хочешь, торгуй осознанно ⚠️
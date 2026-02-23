# 📊 Trading Signal Bot — MEXC Futures 15m+1h

Телеграм-бот: свечные паттерны × мультитаймфрейм × AI Gemini → сигнал с подтверждением.

## Поток сигнала

```
MEXC Futures WebSocket
    ├── 15m свечи → CandleBuffer → 12 паттернов + ADX/RSI/CCI
    └── 1h  свечи → CandleBuffer → тренд-фильтр (EMA, ADX)
                              ↓
                  Направление 15m == тренд 1h?
                              ↓ Да
                        Gemini AI анализ
                              ↓ confidence ≥ MIN_CONFIDENCE
                     Confluence Score (0-100%)
                              ↓
              Telegram: сигнал + кнопки [✅ ВОЙТИ] [📋 Детали] [⏭ Пропустить]
                              ↓
              Таймер 5 мин → если нет ответа → «Сигнал устарел»
```

## Быстрый старт

```bash
# 1. Клонируй / распакуй проект
cd trading_bot

# 2. Настрой окружение
cp .env.example .env
nano .env   # вставь TELEGRAM_BOT_TOKEN и GEMINI_API_KEY

# 3. Установи зависимости
pip install -r requirements.txt

# 4. Запуск
python main.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/pair SOL_USDT` | Сменить пару |
| `/status` | Статус + буферы |
| `/stop` | Остановить |

## Структура проекта

```
trading_bot/
├── main.py
├── config.py                    ← все параметры через .env
├── core/
│   ├── candle_buffer.py         ← кольцевой буфер свечей
│   ├── data_feed.py             ← WebSocket MEXC + REST история
│   ├── signal_engine.py         ← паттерны + индикаторы + Gemini
│   └── multi_tf_engine.py  ★   ← оркестратор 15m+1h
├── patterns/
│   ├── ema_patterns.py          ← Дриблинг, Подхват, MA50, 45°
│   ├── level_patterns.py        ← Уровни, Гэп, Откуп
│   └── volatility_patterns.py   ← Сжатие, Рыбий крюк, Пинг-Понг
├── indicators/calculator.py     ← ADX, RSI, CCI, ATR, EMA (numpy)
├── ai/gemini_client.py          ← Gemini 1.5 Flash
└── bot/
    ├── telegram_bot.py     ★   ← aiogram 3, FSM, pending сигнал
    ├── keyboards.py        ★   ← signal_action_kb с таймером
    └── signal_formatter.py ★   ← MTF форматирование
```

## Как работает Confluence Score

| Компонент | Вес |
|-----------|-----|
| AI Gemini confidence | 40% |
| Паттерны (кол-во × сила) | 25% |
| 1h индикаторы (ADX, DI, RSI) | 20% |
| Согласование 15m ↔ 1h | 15% |

## Получение ключей

- **Telegram Bot**: [@BotFather](https://t.me/BotFather) → `/newbot`
- **Gemini API**: [aistudio.google.com](https://aistudio.google.com/app/apikey) (бесплатный тариф)

## Пары MEXC Futures (формат)

`BTC_USDT`, `ETH_USDT`, `SOL_USDT`, `BNB_USDT`, `XRP_USDT`, `DOGE_USDT`

⚠️ **Дисклеймер**: Бот не даёт финансовых советов. Торгуйте осознанно, используйте риск-менеджмент.

---

## 🐳 Docker

### Запуск через Docker Compose (рекомендуется)

```bash
# 1. Настрой переменные окружения
cp .env.example .env
nano .env   # вставь токены

# 2. Сборка и запуск
docker compose up -d

# 3. Логи в реальном времени
docker compose logs -f

# 4. Остановка
docker compose down
```

### Или напрямую через Docker

```bash
# Сборка образа
docker build -t trading-bot .

# Запуск
docker run -d \
  --name trading_signal_bot \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  trading-bot

# Логи
docker logs -f trading_signal_bot

# Остановка
docker stop trading_signal_bot
```

### Обновление бота

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

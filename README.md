# Bill Bot (Hyperliquid) — Alligator + Fractals (dry-run)

Telegram-бот для отслеживания выбранных пар на Hyperliquid по стратегии Bill Williams:
Alligator EMA(13/8/5) + подтверждённые фракталы (кластера). Бот поддерживает **ДВА режима исполнения**: 
- **DRY-RUN** (безопасная симуляция с расчётом P&L без реальных ордеров на бирже)
- **LIVE** (реальная торговля на бирже Hyperliquid)

## Возможности

- Таймфрейм на пользователя (выбор из `TIMEFRAMES_AVAILABLE`)
- Выбор пар (multi-select) и запуск/остановка отслеживания
- Детект “сна” аллигатора по SpreadLines
- Подтверждённые фракталы 2+2 (две свечи слева и две справа), подтверждение +2 свечи
- Сетапы и ордера:
  - stop-market trigger на пробой кластера ± 1 tick (tick size вычисляется через meta Hyperliquid; есть fallback)
  - замена ордера на новый при появлении более “свежего” валидного кластера (если позиция ещё не открыта)
  - при “сне” допускается два стоп-ордера одновременно (LONG и SHORT) до открытия позиции
- Исполнение ордеров:
  - **DRY-RUN**: виртуальные отложенные ордера → позиция → закрытие по SL/TP (симуляция)
  - **LIVE**: настоящие trigger-ордера (Stop Entry), реальные TP/SL (reduce-only), расчёт объёмов от реального баланса кошелька
- Risk:Reward = 1:1 (TP = 1×SL)
- Trailing SL/TP:
  - 60% к TP: перенос SL в безубыток (entry)
  - 95% к текущему TP: SL → 85% пути, TP → 150% (циклично повторяется дальше)
- P&L (unrealized/realized) по пользователю/паре/TF
- График PNG: свечи + линии Alligator + фракталы + уровни Trigger/Entry/SL/TP
- Сигналы в Telegram: найден сетап / позиция открыта / позиция закрыта (в каждом сообщении прикладывается график)
- Журнал закрытых сделок (history) + выгрузка в Excel (.xlsx) из Telegram
- Часовой пояс Europe/Warsaw для дат в сообщениях и на графиках (формат `HH:MM DD/MM/YY`)
- Управление из Telegram: 
  - кнопка переключения **DRY / LIVE** режима
  - отмена ордеров и ручное закрытие позиции (dry-run)
- Очистка чата: автоудаление input-сообщений + кнопка “🗑 Удалить” (в т.ч. для графиков/сигналов)

## Стратегия (кратко)

- Alligator:
  - Jaw = EMA(Close, 13)
  - Teeth = EMA(Close, 8)
  - Lips = EMA(Close, 5)
- Кластера (фракталы, только подтверждённые 2+2):
  - HIGH (LONG-кластер): High[i] строго больше High[i-1], High[i-2], High[i+1], High[i+2]
  - LOW (SHORT-кластер): Low[i] строго меньше Low[i-1], Low[i-2], Low[i+1], Low[i+2]
- Сетап:
  - LONG: Alligator “спит”; есть подтверждённый HIGH-кластер; свеча кластера закрылась выше Teeth
  - SHORT: зеркально (LOW-кластер и close ниже Teeth)
- Вход: stop-market на пробой кластера ± 1 tick
- SL: ближайший противоположный кластер (с фильтром закрытия свечи кластера относительно Teeth)
- TP: RR=1:1
- Trailing:
  - при достижении 60% пути к TP: SL в безубыток
  - при достижении 95% пути к текущему TP: SL на 85% пути и TP на 150%; далее продолжаем считать новый TP за 100% и повторяем правило

## Быстрый старт (Docker, рекомендуемо)

### 1) Подготовить `.env`

Создайте файл `.env` рядом с `docker-compose.yml` (можно скопировать из `.env.example`) и заполните значения.

Минимально обязательные:
- `REDIS_PASSWORD` — пароль Redis
- `TELEGRAM_TOKEN` — токен вашего бота

Пример:

```env
REDIS_PASSWORD=change_me
TELEGRAM_TOKEN=change_me
LOG_LEVEL=INFO
PAIRS=BTC,SOL
TIMEFRAME=15m
TIMEFRAMES_AVAILABLE=15m,5m,1m
HISTORY_BARS=100
POLL_SECONDS=5
SLEEP_WINDOW=20
SLEEP_K=0.001
FRACTALS_MAX=200
TRADES_MAX=5000
TICK_SIZE=0.01
TICK_SIZES=BTC:0.5,ETH:0.05
VIRTUAL_EQUITY=10000
RISK_PCT=1.0
PAIRS_AVAILABLE=BTC,ETH,SOL

# --- Для реальной торговли ---
HYPERLIQUID_WALLET_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=...
```

### 2) Запуск

```bash
docker compose up -d --build
```

### 3) Логи

```bash
docker compose logs -f bot
```

Остановить:

```bash
docker compose down
```

## Docker: полезные команды

Запуск/остановка:

```bash
docker compose up -d --build
docker compose ps
docker compose stop
docker compose start
docker compose restart bot
docker compose down
docker compose down -v
```

Логи:

```bash
docker compose logs -f bot
docker compose logs -f redis
docker compose logs --tail 200 bot
```

Пересборка образа:

```bash
docker compose build --no-cache bot
docker compose up -d --build
```

Обновить базовые образы:

```bash
docker compose pull
docker compose up -d --build
```

Зайти внутрь контейнера:

```bash
docker compose exec bot /bin/sh
docker compose exec redis /bin/sh
```

Redis CLI (внутри Docker):

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD"
```

PowerShell вариант:

```powershell
docker compose exec redis redis-cli -a "$env:REDIS_PASSWORD"
```

Чистка Docker (осторожно удаляет неиспользуемые ресурсы):

```bash
docker system prune -f
docker volume prune -f
```

## Локальный запуск (без Docker)

Требования: Python 3.13.

1) Установить зависимости:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2) Экспортировать переменные окружения (или использовать `.env` через ваш способ загрузки env) и запустить:

```bash
python -m bill_bot.main
```

Логи пишутся в stdout.

## Troubleshooting

### Docker Desktop / Engine не запущен

Симптом: ошибки вида `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`.

Решение:
- Запустить Docker Desktop и дождаться статуса Running
- Повторить `docker compose up -d --build`

### Бот не стартует из-за зависимостей (pip resolution)

Если сборка Docker падает на зависимостях, проверь `requirements.txt` и пересобери без кэша:

```bash
docker compose build --no-cache bot
docker compose up -d
```

### Redis: неверный пароль

Симптомы: бот пишет `Redis connection failed` или `NOAUTH Authentication required`.

Проверь, что один и тот же `REDIS_PASSWORD` указан:
- в `.env` (и для сервиса redis, и для сервиса bot через `env_file`)

Проверка:

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" PING
```

### Telegram: бот не отвечает

Проверь:
- `TELEGRAM_TOKEN` в `.env`
- логи: `docker compose logs -f bot` (должно быть `Telegram bot polling started`)

## Как пользоваться в Telegram

Основная навигация — кнопками снизу (Reply Keyboard).

- `📌 Пары` — выбрать пары для отслеживания (multi-select)
- `⏱ TF` — выбрать таймфрейм для вашего пользователя
- `🕹 Режим` — переключение между безопасной симуляцией (DRY) и реальной торговлей на бирже (LIVE)
- `⚙️ Риск` — настройка риск-менеджмента: % маржи от реального депозита и % риска на отдельную сделку
- `▶️ Запуск` / `⏸ Стоп` — включить/выключить отслеживание
- `📈 Позиции` — текущие ордера/позиции (как виртуальные, так и реальные)
- `💰 P&L` — суммарный unrealized/realized по выбранным парам
- `📉 График` — выбрать пару и получить PNG (свечи + Alligator + фракталы + уровни сделки)
  - есть кнопка “⌨️ Ввести пару” для ввода произвольной пары Hyperliquid (например `HYPE` или `HYPE-USDC`)
- `📜 Сделки` — последние закрытые сделки (history) по выбранным парам
  - кнопка `📥 Excel` — выгрузка отчёта .xlsx (лист Trades + Summary)
- `📊 Статус` — текущий TF/пары/риск/активность
- `🗑 Удалить` — удалить последнее сообщение бота (а также есть inline-кнопки “🗑 Удалить” на многих экранах)

## Что смотреть в логах

Основные типы сообщений:
- Подключение к Redis: `Redis ping=...`
- Догрузка истории: `History loaded: pair=... tf=... bars=...`
- Новая закрытая свеча: `New closed candle: pair=... tf=... t=...`
- Alligator + сон: `Alligator: ... sleep=... med_spread=...`
- Подтверждённый фрактал: `Fractal confirmed: ... kind=HIGH/LOW ...`
- Кандидат сигнала: `Signal candidate: ... entry=... sl=... tp=... tick=...`
- Dry-run действия:
  - постановка ордера: `Dry-run order placed: ... trigger=... sl=... tp=... qty=...`
  - событие: `Dry-run event: ... position_opened / position_closed ...`

## Redis: где лежат данные (основные ключи)

Ключи “скоупнуты” по пользователю и таймфрейму.

- Окно свечей: `candles:<pair>:<tf>`
- Фракталы: `fractals:<pair>:<tf>`
- Подписки:
  - пары: `sub:user:<user_id>:pairs:<tf>`
  - активность: `sub:user:<user_id>:active:<tf>`
  - конфиг (risk): `sub:user:<user_id>:cfg:<tf>`
  - выбранный TF пользователя: `sub:user:<user_id>:timeframe`
  - индекс пользователей по паре: `sub:pair:<pair>:<tf>:users`
  - агрегат активных пар: `active_pairs:<tf>`
- Dry-run состояние и P&L:
  - состояние (ордер/позиция): `state:<user_id>:<pair>:<tf>`
  - P&L: `pnl:<user_id>:<pair>:<tf>`
  - журнал сделок: `trades:<user_id>:<pair>:<tf>` (хранится до `TRADES_MAX`)

### Проверка ключей через `redis-cli` (Docker)

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD"
```

PowerShell вариант:

```powershell
docker compose exec redis redis-cli -a "$env:REDIS_PASSWORD"
```

Примеры:

```redis
KEYS candles:*
GET state:<user_id>:SOL:15m
GET pnl:<user_id>:SOL:15m
LRANGE trades:<user_id>:SOL:15m 0 10
```

## Ограничения текущего этапа

- Рыночные данные в основном собираются через REST polling (`candle_snapshot`), WebSocket реализован пока только для отслеживания пользовательского стейта (балансы, позиции).
- Для LIVE режима необходимы ключи API L1-биржи Hyperliquid.

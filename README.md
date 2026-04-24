# Bill Bot (Hyperliquid) — Alligator + Fractals (dry-run)

Telegram-бот для отслеживания выбранных пар на Hyperliquid по стратегии Bill Williams:
Alligator EMA(13/8/5) + подтверждённые фракталы (кластера). Исполнение на текущем этапе — dry-run
(без реальных ордеров на бирже), с расчётом ордеров/позиций/P&L и визуализацией графика.

## Возможности

- Таймфрейм на пользователя (выбор из `TIMEFRAMES_AVAILABLE`)
- Выбор пар (multi-select) и запуск/остановка отслеживания
- Детект “сна” аллигатора по SpreadLines
- Подтверждённые фракталы (5 свечей, подтверждение +2)
- Сигналы: stop-market trigger на пробой кластера ± 1 tick (tick size берётся из логики стратегии/фоллбэка)
- Dry-run исполнение: pending order → position → закрытие по SL/TP
- P&L (unrealized/realized) по пользователю/паре/TF
- График PNG: свечи + линии Alligator + фракталы + уровни Trigger/Entry/SL/TP
- Журнал закрытых сделок (history) и просмотр из Telegram

## Быстрый старт (Docker, рекомендуемо)

### 1) Подготовить `.env`

Создайте файл `.env` рядом с `docker-compose.yml` (можно скопировать из `.env.example`) и заполните значения.

Минимально обязательные:
- `REDIS_PASSWORD` — пароль Redis
- `TELEGRAM_TOKEN` — токен вашего бота

Пример:

```env
REDIS_PASSWORD=change_me
LOG_LEVEL=INFO
PAIRS=BTC,SOL
TIMEFRAME=15m
TIMEFRAMES_AVAILABLE=15m,5m,1m
HISTORY_BARS=100
POLL_SECONDS=5
SLEEP_WINDOW=20
SLEEP_K=0.001
FRACTALS_MAX=200
TICK_SIZE=0.01
VIRTUAL_EQUITY=10000
RISK_PCT=1.0
TELEGRAM_TOKEN=change_me
PAIRS_AVAILABLE=BTC,ETH,SOL
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

## Как пользоваться в Telegram

Основная навигация — кнопками снизу (Reply Keyboard).

- `📌 Пары` — выбрать пары для отслеживания (multi-select)
- `⏱ TF` — выбрать таймфрейм для вашего пользователя
  - при смене TF бот переносит пары/риск (если в новом TF ещё пусто) и выключает трекинг; затем нажмите `▶️ Запуск`
- `⚙️ Риск` — выбрать риск на сделку (% от виртуального депозита)
- `▶️ Запуск` / `⏸ Стоп` — включить/выключить отслеживание
- `📈 Позиции` — текущие ордера/позиции по выбранным парам (dry-run)
- `💰 P&L` — суммарный unrealized/realized по выбранным парам
- `📉 График` — выбрать пару и получить PNG (свечи + Alligator + фракталы + уровни сделки)
- `📜 Сделки` — последние закрытые сделки (history) по выбранным парам
- `📊 Статус` — текущий TF/пары/риск/активность

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
  - журнал сделок: `trades:<user_id>:<pair>:<tf>`

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

- Реальная торговля на Hyperliquid пока не включена (dry-run).
- Данные по свечам сейчас берутся polling-ом через `candle_snapshot`.

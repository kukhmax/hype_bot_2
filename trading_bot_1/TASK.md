**подробное ТЗ и поэтапный план разработки** production-уровня intraday/scalping бота для фьючерсов с 2–3 стратегиями, Regime classifier, ML-фильтром, динамическим риск-менеджментом и ATR-трейлингом.

---

# 1. Цели проекта

**Задача:** создать автономного Telegram-бота для торговли крипто-фьючерсами (Binance/Bybit/Hyperliquid) с положительным матожиданием и контролируемой просадкой.

**Целевые метрики (после валидации):**

* Profit Factor ≥ 1.6
* Max DD ≤ 20%
* Sharpe ≥ 1.2
* Winrate 42–55%
* Средний R:R ≥ 1.8

---

# 2. Область применения

* Рынок: USDT-маржинальные perpetual futures
* Таймфреймы:

  * Скальпинг: 1m / 3m / 5m
  * Intraday: 5m / 15m / 1H (HTF фильтр)
* Плечо: 3x–10x (динамически)
* Режим: isolated

---

# 3. Функциональные требования

## 3.1 Data Layer

### Источники:

* WebSocket (ticks + kline)
* REST fallback
* Funding rate
* Open interest

### Требования:

* Потоковая обработка
* Построение свечей в реальном времени
* Кэширование состояния (Redis)
* Исторические данные для обучения

---

## 3.2 Regime Classifier

### Задача:

Определять текущий рыночный режим:

* Strong Trend
* Weak Trend
* Range
* High Volatility Expansion

### Версия 1:

Rule-based логика

### Версия 2:

LightGBM классификатор

### Features:

* ADX
* EMA200 slope
* ATR percentile (100)
* Bollinger width
* Volume percentile
* OI delta
* Funding rate

### Output:

```python
regime: str
confidence: float
```

---

## 3.3 Стратегии

## Strategy 1 — Trend Pullback (Core Intraday)

Активна в:

* strong_trend
* weak_trend

### Условия:

* HTF EMA200 направление
* EMA21/EMA50 alignment
* RSI pullback 30–40
* Volume filter
* Break confirmation

### SL:

1.5 × ATR

### TP:

2R + ATR trailing

---

## Strategy 2 — Volatility Breakout (Scalp Engine)

Активна в:

* high_vol

### Условия:

* BB squeeze
* ATR expansion
* ADX rising
* Breakout candle

### SL:

1 × ATR

### TP:

1.5R

Частичное закрытие.

---

## Strategy 3 — Liquidity Sweep Reversal

Активна в:

* range

### Условия:

* Sweep локального экстремума
* Возврат внутрь диапазона
* RSI divergence
* Volume spike

---

## 3.4 ML Probability Filter

### Назначение:

Фильтрация сигналов.

### Модель:

LightGBM / XGBoost

### Target:

Проход +1R в течение N свечей

### Features:

* RSI, RSI slope
* EMA distances
* EMA slope
* ATR
* Volume ratio
* ADX
* OI delta
* Funding
* Regime

### Логика:

```python
if proba > threshold:
    allow_trade
```

Threshold динамический (walk-forward).

---

## 3.5 Risk Manager

### Dynamic Position Sizing:

```text
Risk_per_trade = 0.5% – 1%
Size = Risk / SL_distance
```

Усиление при высокой ML вероятности.

Ограничения:

* Макс дневной риск
* Макс открытых позиций
* Корреляционный фильтр

---

## 3.6 Execution Engine

### Требования:

* Асинхронный
* Retry logic
* Проверка slippage
* Проверка margin
* Reduce-only ордера
* Частичное закрытие

---

## 3.7 Exit Management

### ATR Trailing

Механика:

* После 1R → BE+
* После 2R → trailing 1.2 ATR
* Adaptive trailing при сильном тренде

---

## 3.8 Telegram Interface

### Команды:

* /status
* /positions
* /balance
* /pause
* /resume
* /set_risk
* /stats

### Уведомления:

* Новый сигнал
* Открытие позиции
* Закрытие позиции
* Ошибки

---

# 4. Нефункциональные требования

* Async architecture
* Отказоустойчивость
* Логирование (PostgreSQL)
* Мониторинг (Prometheus)
* Dockerized deployment
* Backtesting engine
* Walk-forward validation

---

# 5. Архитектура системы

```text
core/
├── data/
│   ├── websocket_client.py
│   ├── candle_builder.py
│
├── features/
│   ├── indicators.py
│   ├── feature_engineer.py
│
├── regime/
│   ├── rule_based.py
│   ├── lgbm_classifier.py
│
├── strategies/
│   ├── trend_pullback.py
│   ├── breakout.py
│   ├── liquidity_sweep.py
│
├── ml/
│   ├── train.py
│   ├── inference.py
│
├── risk/
│   ├── position_sizer.py
│   ├── portfolio_manager.py
│
├── execution/
│   ├── order_manager.py
│   ├── exit_manager.py
│
├── backtest/
│   ├── engine.py
│   ├── metrics.py
│
└── bot/
    ├── telegram_bot.py
```

---

# 6. Этапы разработки

---

## ЭТАП 1 — Data + Backtesting (2–3 недели)

* WebSocket engine
* Candle builder
* Historical downloader
* Backtest engine
* Metrics module

🎯 Цель: тестировать стратегии оффлайн.

---

## ЭТАП 2 — Реализация стратегий (2 недели)

* Trend Pullback
* Breakout
* Liquidity Sweep
* Оптимизация параметров
* Walk-forward тесты

---

## ЭТАП 3 — ML Layer (3–4 недели)

* Feature engineering
* Dataset builder
* Labeling logic
* Model training
* Walk-forward validation
* Threshold optimization

---

## ЭТАП 4 — Regime Classifier (2 недели)

* Rule-based версия
* ML версия
* Тестирование переключения стратегий

---

## ЭТАП 5 — Execution Engine (2 недели)

* API интеграция
* Order lifecycle
* Error handling
* Slippage simulation

---

## ЭТАП 6 — Risk & Portfolio Management (1–2 недели)

* Position sizing
* Daily limits
* Exposure control
* Correlation filter

---

## ЭТАП 7 — Telegram + Monitoring (1 неделя)

* Bot interface
* Notifications
* Logging
* Dashboard

---

# 7. Тестирование

### Обязательно:

* Backtest ≥ 2 года данных
* Walk-forward validation
* Monte Carlo simulation
* Out-of-sample testing

---

# 8. Развертывание

* Docker
* VPS (4 CPU / 8GB RAM)
* Redis
* PostgreSQL
* Nginx (если нужен webhook)

---

# 9. Риски проекта

* Overfitting ML
* Regime misclassification
* Ликвидность альтов
* API outages
* Funding distortions

---

# 10. Оценка сроков

Полный цикл:
3–4 месяца соло-разработки.

MVP:
6–8 недель.

---

# 🔥 Если делать максимально серьёзно

Добавить:

* Meta-model для выбора стратегии
* Ensemble ML
* Adaptive threshold
* Online learning
* Kelly fraction ограниченный

---

Дальше предлагаю:

1. Сформировать MVP scope
2. Определить биржу
3. Выбрать основной таймфрейм
4. Решить: сначала backtest или сразу live paper trading?


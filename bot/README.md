# HyperLiquid EMA+ADX Trading Bot with AI Confirmation

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-supported-brightgreen)](https://www.docker.com/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue)](https://core.telegram.org/bots)

Телеграм бот для автоматического трейдинга на HyperLiquid с использованием стратегии на основе EMA и ADX, с подтверждением сигналов через DeepSeek AI.

## 📋 Содержание
- [Описание стратегии](#описание-стратегии)
- [Архитектура](#архитектура)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Запуск](#запуск)
- [Использование](#использование)
- [API и сервисы](#api-и-сервисы)
- [Мониторинг](#мониторинг)
- [Разработка](#разработка)
- [Лицензия](#лицензия)

## 🎯 Описание стратегии

### Математическая модель

**Вход в LONG (все условия должны выполняться одновременно):**
1. `Close > EMA20(High)` - пробой верхней границы канала
2. `ADX > 20` - наличие тренда
3. `Previous_High > EMA20(High) AND Close > Previous_High` - структурный пробой

**Вход в SHORT (все условия должны выполняться одновременно):**
1. `Close < EMA20(Low)` - пробой нижней границы канала
2. `ADX > 20` - наличие тренда
3. `Previous_Low < EMA20(Low) AND Close < Previous_Low` - структурный пробой

### Почему эта стратегия работает?

- **EMA20(High/Low)** создает динамический канал, адаптирующийся к волатильности
- **ADX > 20** отсеивает флэт и ложные пробои
- **Структурный пробой** подтверждает смену тренда на выбранном таймфрейме
- **ИИ-подтверждение** добавляет контекстный анализ (объемы, уровни, ликвидность)

## 🏗 Архитектура
```
┌─────────────┐ ┌──────────────┐ ┌─────────────┐
│ Telegram │────▶│ Bot Core │────▶│ Redis │
│ Users │◀────│ (Python) │◀────│ Storage │
└─────────────┘ └──────────────┘ └─────────────┘
│
┌──────┴──────┐
▼ ▼
┌─────────────┐ ┌─────────────┐
│ HyperLiquid │ │ DeepSeek │
│ WebSocket │ │ API │
└─────────────┘ └─────────────┘
```


### Компоненты системы

1. **Telegram Bot Interface** - взаимодействие с пользователями
2. **Redis** - хранение подписок и истории сигналов
3. **HyperLiquid WebSocket** - получение рыночных данных в реальном времени
4. **Стратегия EMA+ADX** - математический анализ
5. **DeepSeek AI** - подтверждение сигналов
6. **Docker** - контейнеризация

## 📦 Установка

### Предварительные требования

- Python 3.13+
- Docker и Docker Compose
- Redis (будет запущен в контейнере)
- API ключи (Telegram, DeepSeek)

### Локальная установка

1. **Клонирование репозитория**
```bash
git clone https://github.com/yourusername/hyperliquid-bot.git
cd hyperliquid-bot
```
2. **Создание виртуального окружения**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```
3. **Установка зависимостей**
```bash
pip install -r requirements.txt
```
4. **Установка TA-Lib (системная зависимость)**

Для Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install ta-lib libta-lib-dev
```
Для MacOS:
```bash
brew install ta-lib
```
Для Windows:
Скачайте с https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib

5. **Настройка конфигурации**
```bash
cp .env.example .env
# Отредактируйте .env файл с вашими ключами
```

## Установка через Docker

1. **Клонирование и настройка**
```bash
git clone https://github.com/yourusername/hyperliquid-bot.git
cd hyperliquid-bot
cp .env.example .env
# Отредактируйте .env файл
```
2. **Запуск через Docker Compose**
```bash
docker-compose up --build
```

## 🔧 Конфигурация

**Переменные окружения (.env)**
```
# Telegram Bot Token (получить у @BotFather)
TELEGRAM_TOKEN=your_telegram_bot_token_here

# DeepSeek API (https://platform.deepseek.com/)
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Bot Settings
MAX_CANDLES_HISTORY=200
CHECK_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
```

**Параметры стратегии (можно изменить в коде)**

- EMA_PERIOD = 20 - период EMA

- ADX_THRESHOLD = 20 - порог ADX для определения тренда

- ADX_PERIOD = 14 - период расчета ADX

- MIN_CANDLES = 50 - минимальное количество свечей для анализа

## 🚀 Запуск

**Запуск через Docker (рекомендуется)**
```bash
# Сборка и запуск
docker-compose up --build

# Запуск в фоне
docker-compose up -d

# Просмотр логов
docker-compose logs -f bot

# Остановка
docker-compose down
```
**Запуск локально (для разработки)**
```bash
# Запуск Redis отдельно
docker run -d -p 6379:6379 --name redis redis:7-alpine

# Запуск бота
python -m bot.main
```

## 📱 Использование

**Команды Telegram бота**

| Команда / Элемент | Описание |
|-------------------|----------|
| `/start` | Запуск бота и главное меню |
| **Кнопка "Подписаться"** | Добавить новый токен для отслеживания |
| **Кнопка "Активные подписки"** | Просмотр и управление подписками |

## Процесс работы

1. **Подписка на токен**
```
Пользователь: /start → "Подписаться" → "ETH 5m"
Бот: ✅ Подписка добавлена
```
2. **Анализ рынка**
- Бот получает свечи через WebSocket

- Проверяет условия стратегии на каждой новой свече

- При обнаружении сигнала отправляет запрос в DeepSeek
3. Получение сигнала
```
🚨 ТОРГОВЫЙ СИГНАЛ 🚨

ETH | 5m
LONG 🔥

🤖 Уверенность ИИ: 85%

📊 Вход: $3450 - $3480
🛑 Стоп: $3400
✅ TP1: $3550
✅ TP2: $3650

📝 Анализ: Сильный пробой с объемом...
```
4. **Управление подписками**
- Просмотр всех активных подписок
- Удаление ненужных токенов

## 🔌 API и сервисы

**HyperLiquid WebSocket**
Бот подключается к WebSocket API HyperLiquid для получения данных в реальном времени:

- wss://api.hyperliquid.xyz/ws

Поддерживаемые интервалы свечей:

- 1m, 5m, 15m

**DeepSeek API**
Каждый сигнал проходит проверку через DeepSeek AI:

- Анализ контекста рынка
- Проверка объемов
- Определение уровней стоп-лосс и тейк-профит
- Оценка уверенности (0-100%)

## 📊 Мониторинг

**Логирование**
Логи сохраняются в logs/bot.log с ротацией:

- Уровень: INFO (можно изменить в .env)
- Формат: timestamp - module - level - message

**Redis мониторинг**
```bash
# Подключение к Redis
docker exec -it hyperliquid-bot_redis_1 redis-cli

# Просмотр всех ключей
KEYS *

# Просмотр подписок пользователя
HGETALL user:123456789:subscriptions

# Просмотр истории сигналов
KEYS signals:*
```

## 🛠 Разработка

**Структура проекта для разработчиков**
```
hyperliquid_bot/
├── bot/
│   ├── handlers/          # Telegram обработчики
│   │   ├── start.py       # /start и меню
│   │   └── subscriptions.py # Управление подписками
│   ├── services/          # Бизнес-логика
│   │   ├── hyperliquid.py # WebSocket клиент
│   │   ├── strategy.py    # EMA+ADX стратегия
│   │   ├── deepseek.py    # AI интеграция
│   │   └── redis_service.py # Redis операции
│   ├── models/            # Pydantic модели
│   │   └── signal.py      # Модели данных
│   ├── utils/             # Утилиты
│   │   └── indicators.py  # Технические индикаторы
│   └── main.py            # Точка входа
├── docker/
│   └── Dockerfile
├── tests/                 # Тесты
├── docker-compose.yml
├── requirements.txt
└── .env
```
**Добавление новой стратегии**
1. Создайте новый класс в bot/services/
2. Реализуйте методы check_long_setup и check_short_setup
3. Интегрируйте в main.py

**Запуск тестов**
```bash
# TODO: Добавить тесты
pytest tests/
```

## ⚠️ Важные предупреждения
1. Риск потери капитала: Торговля криптовалютами связана с высоким риском
2. Не финансовый совет: Бот предоставляет только сигналы, решение принимаете вы
3. Тестирование: Сначала протестируйте на малых суммах
4. DeepSeek API: Имеет ограничения по частоте запросов

## 📈 Производительность
- Задержка: < 100ms от получения свечи до отправки сигнала
- Нагрузка: 1 бот может обрабатывать ~50 токенов одновременно
- Память: ~200MB RAM для 100 подписчиков

## 🐛 Известные проблемы и решения
1. WebSocket разрывы
- Автоматическое переподключение реализовано
- Буферизация данных при потере соединения
2. DeepSeek таймауты
- Retry механизм с экспоненциальной задержкой
- Fallback сигналы без AI подтверждения
3. Redis переполнение
- Автоматическое удаление старых сигналов (TTL 7 дней)

**🤝 Вклад в проект**
1. Fork репозитория
2. Создайте ветку (```git checkout -b feature/amazing-feature```)
3. Commit изменений (```git commit -m 'Add amazing feature```)
4. Push в ветку (```git push origin feature/amazing-feature```)
5. Open Pull Request

**📄 Лицензия**
MIT License. Смотрите файл ```LICENSE``` для деталей.

**📞 Контакты и поддержка**
- Создайте Issue в GitHub
- Telegram канал: @your_channel
- Email: your.email@example.com

**🙏 Благодарности**
- HyperLiquid за отличное API
- DeepSeek за мощную AI модель
- Сообществу open-source за библиотеки

## ⭐ Если проект полезен, поставьте звезду на GitHub!

#!/usr/bin/env bash
# Скрипт запуска Fibo Bot

set -e

echo "======================================"
echo "🚀 Запуск Fibo Bot"
echo "======================================"

# Переход в директорию скрипта
cd "$(dirname "$0")"

# Подтягиваем переменные окружения, если файл существует
if [ -f .env ]; then
  source .env
fi

# Проверка, установлен ли Docker и Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Ошибка: docker-compose не найден. Пожалуйста, установите Docker."
    exit 1
fi

echo "📦 Инициализация базы данных и кэша..."
docker-compose up -d redis postgres

echo "⏳ Ожидание запуска базы данных..."
sleep 5

echo "🛠️ Запуск Telegram бота и торгового движка..."
docker-compose up -d fibo_bot

echo "✅ Fibo Bot успешно запущен в фоновом режиме."
echo "   Чтобы посмотреть логи, используйте команду:"
echo "   docker-compose logs -f fibo_bot"
echo "======================================"

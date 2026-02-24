FROM python:3.11-slim

# Установка системных зависимостей, если понадобятся (например, для компиляции некоторых python пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала копируем только requirements.txt для кэширования слоев Docker
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Оставляем контейнер запущенным по умолчанию, чтобы можно было заходить внутрь и запускать тесты
CMD ["tail", "-f", "/dev/null"]

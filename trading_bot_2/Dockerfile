FROM python:3.13-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала копируем только requirements — чтобы слой кэшировался
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходники
COPY . .

# Директория для логов
RUN mkdir -p /app/logs

# Не запускать от root (закомментировано, чтобы volume logs мог писаться)
# RUN useradd -m botuser && chown -R botuser:botuser /app
# USER botuser
CMD ["python", "main.py"]

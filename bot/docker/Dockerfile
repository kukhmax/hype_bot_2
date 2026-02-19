FROM python:3.13-slim

WORKDIR /app

# Устанавливаем системные зависимости для TA-Lib
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libta-lib0 \
    libta-lib-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

CMD ["python", "-m", "bot.main"]
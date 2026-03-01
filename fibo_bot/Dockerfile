FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs charts_temp

CMD ["python", "main.py"]

#!/bin/bash

# Скрипт для рекурсивного удаления папок __pycache__ и скомпилированных файлов Python

echo "🚀 Очистка кэша Python..."

# Удаление директорий __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} +

# Удаление файлов .pyc, .pyo, .pyd
find . -type f -name "*.py[co]" -delete
find . -type f -name "*.pyd" -delete

echo "✅ Кэш очищен!"

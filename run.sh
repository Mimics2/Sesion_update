#!/bin/bash
echo "🚀 Starting bot..."
echo "📁 Current dir: $(pwd)"
echo "📄 Files:"
ls -la

# Создаем папку /app если её нет
mkdir -p /app

# Копируем файлы в /app
cp -r . /app/

# Переходим в /app
cd /app

# Проверяем что файлы на месте
echo "✅ Files in /app:"
ls -la /app

# Запускаем бота
python bot.py

#!/bin/bash

# Запускаем парсер в фоновом режиме (знак &)
echo "Starting parser.py in background..."
python3 parser.py &

# Ждем 30 секунд, чтобы парсер успел создать файл schedule_data.json
echo "Waiting 30 seconds for initial data scraping..."
sleep 30

# Запускаем бота в основном (foreground) режиме
echo "Starting bot.py..."
python3 bot.py
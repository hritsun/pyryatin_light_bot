import asyncio
import logging
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ' # ТВОЙ ТОКЕН
DATA_FILE = 'schedule_data.json'

# Таймзона Киева
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Хранилище подписок (в памяти). При перезапуске бота сбросится.
# Для продакшена лучше сохранять в базу данных SQLite.
user_subscriptions = {} 

def now_kyiv():
    """Текущее время в Киеве"""
    return datetime.now(KYIV_TZ)

# --- ЧТЕНИЕ ДАННЫХ ИЗ ФАЙЛА ---
def get_schedule_from_file():
    """Читает данные из JSON файла, созданного парсером."""
    if not os.path.exists(DATA_FILE):
        return "⚠️ Дані ще не зібрані. Спробуйте пізніше.", []

    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Проверка свежести данных (например, если файл старше 30 минут)
        last_update = datetime.fromtimestamp(data.get('timestamp', 0) if 'timestamp' in data else data.get('updated_at', 0), KYIV_TZ)
        is_old = (now_kyiv() - last_update).total_seconds() > 1800 # 30 минут

        text_lines = data.get('text_lines', [])
        periods = data.get('periods', [])

        # Формируем красивый текст
        header = f"💡 <b>Графік для 2 черги (1 підгрупи)</b>\n" \
                 f"📅 <i>Оновлено: {last_update.strftime('%H:%M')}</i>\n" \
                 f"{'─'*30}\n"
        
        body = "\n".join(text_lines)
        
        warning = "\n\n⚠️ <i>Дані застарілі, перевіряю джерело...</i>" if is_old else ""
        
        return header + body + warning, periods

    except Exception as e:
        logging.error(f"Ошибка чтения файла: {e}")
        return "❌ Помилка читання даних.", []

# --- ФОНОВАЯ ЗАДАЧА УВЕДОМЛЕНИЙ ---
async def monitor_schedule_task():
    """Проверяет файл и отправляет уведомления."""
    logging.info("🔔 Мониторинг запущен")
    await asyncio.sleep(5) 
    
    while True:
        try:
            current_time = now_kyiv()
            # Читаем данные из файла
            _, time_periods = get_schedule_from_file()
            
            if time_periods:
                for user_id in list(user_subscriptions.keys()): # list() чтобы можно было удалять если ошибка
                    for period in time_periods:
                        try:
                            # Преобразуем строки "HH:MM" в полноценные datetime объекты на сегодня
                            start_dt = datetime.strptime(period['start'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            end_dt = datetime.strptime(period['end'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            # ЛОГИКА УВЕДОМЛЕНИЙ (за 5 минут)
                            
                            # 1. Скоро отключение?
                            diff_start = (start_dt - current_time).total_seconds()
                            if 240 < diff_start <= 300: # Если осталось от 4 до 5 минут
                                await bot.send_message(
                                    user_id, 
                                    f"⚠️ <b>УВАГА!</b>\n🔴 Через 5 хвилин відключать світло!\n⏰ <b>{period['start']} - {period['end']}</b>",
                                    parse_mode="HTML"
                                )

                            # 2. Скоро включение?
                            diff_end = (end_dt - current_time).total_seconds()
                            if 240 < diff_end <= 300: # Если осталось от 4 до 5 минут
                                await bot.send_message(
                                    user_id, 
                                    f"✅ <b>СКОРО СВІТЛО!</b>\n💡 Через 5 хвилин увімкнуть!\n⏰ Орієнтовно о <b>{period['end']}</b>",
                                    parse_mode="HTML"
                                )

                        except Exception as e:
                            logging.error(f"Ошибка времени: {e}")

            # Проверяем каждую минуту
            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Ошибка в цикле мониторинга: {e}")
            await asyncio.sleep(60)

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [InlineKeyboardButton(text="⚡ Перевірити графік", callback_data="check_2_1")],
        [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="subscribe")],
        [InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
    await message.answer("Привіт! Я показую графік з energy-ua.info (2.1).", reply_markup=keyboard)

@dp.callback_query(F.data == "check_2_1")
async def cb_check(callback: types.CallbackQuery):
    # Данные берутся мгновенно из файла
    text, _ = get_schedule_from_file()
    
    kb = [[InlineKeyboardButton(text="🔄 Оновити", callback_data="check_2_1")]]
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data == "subscribe")
async def cb_sub(callback: types.CallbackQuery):
    user_subscriptions[callback.from_user.id] = True
    await callback.message.answer("✅ Ви підписалися на сповіщення (за 5 хв до події).")
    await callback.answer()

@dp.callback_query(F.data == "unsubscribe")
async def cb_unsub(callback: types.CallbackQuery):
    if callback.from_user.id in user_subscriptions:
        del user_subscriptions[callback.from_user.id]
    await callback.message.answer("🔕 Підписку скасовано.")
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    print("🤖 Бот запускается...")
    # Запускаем фоновую задачу
    asyncio.create_task(monitor_schedule_task())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
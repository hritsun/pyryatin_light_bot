import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
# Читаем токен из переменной окружения. Если её нет (например, локально), используем хардкод.
API_TOKEN = os.getenv('API_TOKEN', '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ') 
DATA_FILE = 'schedule_data.json'

# Таймзона Киева
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Хранилище подписок пользователей {user_id: True}
user_subscriptions = {} 

def now_kyiv():
    """Возвращает текущее время в таймзоне Киева"""
    return datetime.now(KYIV_TZ)

# --- ЧТЕНИЕ ДАННЫХ ИЗ ФАЙЛА ---
def get_schedule_from_file():
    """Читает данные из JSON файла, созданного парсером."""
    if not os.path.exists(DATA_FILE):
        return "⚠️ Дані ще не зібрані. Запустіть parser.py або спробуйте пізніше.", []

    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Проверка свежести данных (если файл старше 30 минут)
        last_update_ts = data.get('timestamp') or data.get('updated_at', 0)
        last_update_dt = datetime.fromtimestamp(last_update_ts, KYIV_TZ)
        is_old = (now_kyiv() - last_update_dt).total_seconds() > 1800 # 30 минут

        text_lines = data.get('text_lines', [])
        periods = data.get('periods', [])

        # Формируем красивый текст
        header = f"💡 <b>Графік для 2 черги (1 підгрупи)</b>\n" \
                 f"📅 <i>Дані з energy-ua.info. Оновлено парсером: {last_update_dt.strftime('%H:%M')}</i>\n" \
                 f"{'─'*30}\n"
        
        body = "\n".join(text_lines)
        
        warning = "\n\n⚠️ <i>Дані застарілі (> 30 хв). Парсер оновлює джерело...</i>" if is_old else ""
        
        return header + body + warning, periods

    except Exception as e:
        logging.error(f"Ошибка чтения файла: {e}")
        return "❌ Помилка читання даних: файл пошкоджений.", []

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
                for user_id in list(user_subscriptions.keys()): 
                    for period in time_periods:
                        try:
                            # Преобразуем строки "HH:MM" в полноценные datetime объекты на сегодня
                            start_dt = datetime.strptime(period['start'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            end_dt = datetime.strptime(period['end'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            # ЛОГИКА УВЕДОМЛЕНИЙ (за 5 минут)
                            
                            # 1. Скоро отключение? (240 < diff <= 300 секунд)
                            diff_start = (start_dt - current_time).total_seconds()
                            if 240 < diff_start <= 300: 
                                await bot.send_message(
                                    user_id, 
                                    f"⚠️ <b>УВАГА!</b>\n🔴 Через 5 хвилин відключать світло!\n⏰ <b>{period['start']} - {period['end']}</b>",
                                    parse_mode="HTML"
                                )

                            # 2. Скоро включение? (240 < diff <= 300 секунд)
                            # Примечание: Это уведомление должно срабатывать до начала периода включения
                            # (т.е. за 5 минут до времени окончания отключения).
                            diff_end = (end_dt - current_time).total_seconds()
                            if 240 < diff_end <= 300: 
                                await bot.send_message(
                                    user_id, 
                                    f"✅ <b>СКОРО СВІТЛО!</b>\n💡 Через 5 хвилин увімкнуть!\n⏰ Орієнтовно о <b>{period['end']}</b>",
                                    parse_mode="HTML"
                                )

                        except Exception as e:
                            logging.error(f"Ошибка обработки периода {period}: {e}")

            # Проверяем каждую минуту
            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Ошибка в цикле мониторинга: {e}")
            await asyncio.sleep(60)

# --- ОБРАБОТЧИКИ БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [InlineKeyboardButton(text="⚡ Перевірити графік", callback_data="check_2_1")],
        [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="subscribe")],
        [InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
    await message.answer(
        "👋 Привіт! Я бот моніторингу відключень світла (2 черга, 1 підгрупа).", 
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "check_2_1")
async def cb_check(callback: types.CallbackQuery):
    # Получаем текст и периоды из файла
    text, _ = get_schedule_from_file()
    
    # *** ДОБАВЛЯЕМ МЕТКУ ВРЕМЕНИ ДЛЯ УНИКАЛЬНОСТИ (Fix TelegramBadRequest) ***
    current_time_str = now_kyiv().strftime('%H:%M:%S')
    
    # Ищем заголовок 'Оновлено:' и вставляем точное время запроса
    if "Оновлено:" in text:
        # Разбиваем текст на части до и после метки "Оновлено:"
        text_parts = text.split("Оновлено:")
        
        # Находим часть, которая идет после старой метки времени
        old_time_part = text_parts[1].split("\n", 1)
        
        # Собираем его обратно, вставляя новую метку времени запроса
        text = text_parts[0] + f"Оновлено: **{current_time_str}**</i>\n" + old_time_part[1]
    else:
        # Если формат заголовка изменился, просто добавляем метку времени в конец
        text += f"\n\n⏱ Запит оброблено: {current_time_str}"
        
    kb = [[InlineKeyboardButton(text="🔄 Оновити", callback_data="check_2_1")]]
    
    # Редактируем сообщение (теперь текст гарантированно другой)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data == "subscribe")
async def cb_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_subscriptions:
        await callback.answer("Ви вже підписані!", show_alert=True)
        return
        
    user_subscriptions[user_id] = True
    await callback.message.answer("✅ Ви підписалися на сповіщення (за 5 хв до події).")
    await callback.answer()

@dp.callback_query(F.data == "unsubscribe")
async def cb_unsub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_subscriptions:
        del user_subscriptions[user_id]
        await callback.message.answer("🔕 Підписку скасовано.")
    else:
        await callback.answer("Ви не підписані!", show_alert=True)
        return
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    print("🤖 Бот запускается...")
    # Запускаем фоновую задачу
    asyncio.create_task(monitor_schedule_task())
    
    # Удаляем старые вебхуки и запускаем долгий опрос
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем.")
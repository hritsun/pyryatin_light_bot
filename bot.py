import asyncio
import logging
import json
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = os.getenv('API_TOKEN', '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ') 
DATA_FILE = 'schedule_data.json'

# Таймзона Киева
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Списки смайлов
SAD_EMOJIS = ["😢", "😭", "😞", "😫", "😕", "😿", "💔", "😥", "☹️", "о_О", "🫤", "😣", "😔", "😖", "😩", "🥺", "😦", "😧", "😨", "😰"]
HAPPY_EMOJIS = ["😍", "🥰", "🥳", "😏", "😎", "😇", "🙂", "🎉", "😍", "🤩", "😁", "😀", "😃", "😄", "😆", "😉", "😊", "😋", "😌", "🙌"]

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

def get_random_sad():
    return random.choice(SAD_EMOJIS)

def get_random_happy():
    return random.choice(HAPPY_EMOJIS)

# --- ЧТЕНИЕ ДАННЫХ ИЗ ФАЙЛА ---
def get_schedule_data():
    """Читает 'сырые' данные из JSON файла."""
    if not os.path.exists(DATA_FILE):
        return None, None, None

    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        last_update_ts = data.get('timestamp') or data.get('updated_at', 0)
        last_update_dt = datetime.fromtimestamp(last_update_ts, KYIV_TZ)
        
        text_lines = data.get('text_lines', [])
        periods = data.get('periods', [])
        
        return last_update_dt, text_lines, periods

    except Exception as e:
        logging.error(f"Ошибка чтения файла: {e}")
        return None, None, None

def format_schedule_message(last_update_dt, text_lines):
    """Формирует сообщение с графиком."""
    if not last_update_dt:
        return "⚠️ Дані ще не зібрані. Спробуйте пізніше."

    # Проверка на устаревание (30 мин)
    is_old = (now_kyiv() - last_update_dt).total_seconds() > 1800
    
    # Формирование тела графика
    clean_lines = []
    for line in text_lines:
        if "🔴" in line or "год." in line:
             clean_lines.append(line)
    
    if not clean_lines:
        schedule_body = "✅ Відключень не знайдено."
    else:
        schedule_body = "\n".join(clean_lines)

    # Основной шаблон
    message = (
        f"📅 <b>Графік для групи 2.1</b>\n\n"
        f"💡 <i>Оновлено: {last_update_dt.strftime('%H:%M')}</i>\n\n"
        f"<b>Графік відключень:</b>\n"
        f"{schedule_body}"
    )

    if is_old:
        message += "\n\n⚠️ <i>Дані застарілі, парсер оновлює джерело...</i>"

    return message

# --- ФОНОВАЯ ЗАДАЧА УВЕДОМЛЕНИЙ ---
async def monitor_schedule_task():
    """Проверяет файл и отправляет уведомления."""
    logging.info("🔔 Мониторинг запущен")
    await asyncio.sleep(5) 
    
    while True:
        try:
            current_time = now_kyiv()
            _, _, time_periods = get_schedule_data()
            
            if time_periods:
                for user_id in list(user_subscriptions.keys()): 
                    # Используем enumerate, чтобы знать индекс текущего периода
                    # Это нужно, чтобы посмотреть на следующий период (для времени ВКЛЮЧЕНИЯ)
                    for i, period in enumerate(time_periods):
                        try:
                            start_dt = datetime.strptime(period['start'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            end_dt = datetime.strptime(period['end'], '%H:%M').replace(
                                year=current_time.year, month=current_time.month, day=current_time.day, tzinfo=KYIV_TZ)
                            
                            # --- 1. ЛОГИКА ОТКЛЮЧЕНИЯ (СУМНИЙ СМАЙЛ) ---
                            # За 5 минут до start_dt
                            diff_start = (start_dt - current_time).total_seconds()
                            
                            if 240 < diff_start <= 300: # 4-5 минут до
                                await bot.send_message(
                                    user_id, 
                                    f"{get_random_sad()} Через 5 хвилин відключать світло!\n\n"
                                    f"🔴 Орієнтовно з {period['start']} до {period['end']}\n\n"
                                    f"🔕 /unsub - відписатись\n"
                                    f"👀 /check - перевірити графік",
                                    parse_mode="HTML"
                                )

                            # --- 2. ЛОГИКА ВКЛЮЧЕНИЯ (ВЕСЕЛИЙ СМАЙЛ) ---
                            # За 5 минут до end_dt
                            diff_end = (end_dt - current_time).total_seconds()
                            
                            if 240 < diff_end <= 300: # 4-5 минут до конца
                                # Логика: нам нужно узнать, когда СЛЕДУЮЩЕЕ отключение
                                next_off_time = "кінця доби" # Значение по умолчанию
                                
                                # Проверяем, есть ли следующий период в списке
                                if i + 1 < len(time_periods):
                                    next_period = time_periods[i+1]
                                    next_off_time = next_period['start']
                                
                                await bot.send_message(
                                    user_id, 
                                    f"{get_random_happy()} Через 5 хвилин увімкнуть світло!\n\n"
                                    f"🟢 Орієнтовно з {period['end']} до {next_off_time}\n\n"
                                    f"🔕 /unsub - відписатись\n"
                                    f"👀 /check - перевірити графік",
                                    parse_mode="HTML"
                                )

                        except Exception as e:
                            logging.error(f"Ошибка периода: {e}")

            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Ошибка мониторинга: {e}")
            await asyncio.sleep(60)

# --- КЛАВИАТУРЫ ---

def get_start_keyboard():
    kb = [
        [InlineKeyboardButton(text="⚡ Перевірити графік", callback_data="check_2_1")],
        [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="subscribe")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_schedule_keyboard():
    kb = [
        [InlineKeyboardButton(text="🔄 Оновити графік", callback_data="check_2_1")],
        [
            InlineKeyboardButton(text="🔔 Підписатись", callback_data="subscribe"),
            InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_unsub_button():
    kb = [[InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_sub_button():
    kb = [[InlineKeyboardButton(text="🔔 Підписатись", callback_data="subscribe")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# --- ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привіт! Я бот моніторингу відключень світла (2 черга, 1 підгрупа).\n\n" 
        "Ти можеш перевірити графік або підключити сповіщення за 5 хвилин до відключення або включення світла ✅", 
        reply_markup=get_start_keyboard()
    )

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    last_update, lines, _ = get_schedule_data()
    base_text = format_schedule_message(last_update, lines)
    
    current_time_str = now_kyiv().strftime('%H:%M:%S')
    final_text = f"{base_text}\n\n⏱ Запит оновлено: {current_time_str}"
    
    await message.answer(final_text, parse_mode="HTML", reply_markup=get_schedule_keyboard())

@dp.message(Command("unsub"))
async def cmd_unsub(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_subscriptions:
        del user_subscriptions[user_id]
        await message.answer("🔕 Підписку скасовано.", reply_markup=get_sub_button())
    else:
        await message.answer("Ви не були підписані.", reply_markup=get_sub_button())

@dp.callback_query(F.data == "check_2_1")
async def cb_check(callback: types.CallbackQuery):
    last_update, lines, _ = get_schedule_data()
    base_text = format_schedule_message(last_update, lines)
    
    current_time_str = now_kyiv().strftime('%H:%M:%S')
    final_text = f"{base_text}\n\n⏱ Запит оновлено: {current_time_str}"
    
    try:
        await callback.message.edit_text(
            final_text, 
            parse_mode="HTML", 
            reply_markup=get_schedule_keyboard()
        )
    except Exception:
        pass
        
    await callback.answer()

@dp.callback_query(F.data == "subscribe")
async def cb_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_subscriptions:
        await callback.answer("Ви вже підписані!", show_alert=True)
        return
        
    user_subscriptions[user_id] = True
    await callback.message.answer(
        "✅ Ви підписалися на сповіщення (за 5 хв до відключення та включення світла)",
        reply_markup=get_unsub_button()
    )
    await callback.answer()

@dp.callback_query(F.data == "unsubscribe")
async def cb_unsub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_subscriptions:
        del user_subscriptions[user_id]
        await callback.message.answer(
            "🔕 Підписку скасовано.",
            reply_markup=get_sub_button()
        )
    else:
        await callback.answer("Ви не підписані!", show_alert=True)
        return
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    print("🤖 Бот запускается...")
    asyncio.create_task(monitor_schedule_task())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено.")
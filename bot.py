import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import os

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = os.getenv('API_TOKEN', '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ')
BASE_URL = "https://energy-ua.info/cherga"

# Таймзона Киева для правильного времени
KYIV_TZ = ZoneInfo("Europe/Kyiv")

def now_kyiv():
    """Возвращает текущее время в таймзоне Киева"""
    return datetime.now(KYIV_TZ)

# Web Unlocker прокси от Bright Data
WEB_UNLOCKER_PROXY = {
    'http': 'http://brd-customer-hl_af9d374d-zone-web_unlocker:41lvtwmav2y8@brd.superproxy.io:33335',
    'https': 'http://brd-customer-hl_af9d374d-zone-web_unlocker:41lvtwmav2y8@brd.superproxy.io:33335'
}

# Кэш для расписания (чтобы не запрашивать сайт каждую минуту)
SCHEDULE_CACHE = {}
CACHE_TTL_MINUTES = 10

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Хранилище подписок пользователей {user_id: {'queue': '2', 'subgroup': '1'}}
user_subscriptions = {}

# --- ФУНКЦИЯ ПАРСИНГА С КЭШИРОВАНИЕМ ---
def get_schedule_cached(queue: str, subgroup: str, force=False):
    """Получает расписание с кэшированием на 10 минут."""
    cache_key = f"{queue}-{subgroup}"
    now = now_kyiv()
    
    # Проверяем кэш
    if not force and cache_key in SCHEDULE_CACHE:
        cached = SCHEDULE_CACHE[cache_key]
        if now - cached["timestamp"] < timedelta(minutes=CACHE_TTL_MINUTES):
            logging.info(f"Используем кэш для {cache_key}")
            return cached["text"], cached["periods"]
    
    # Получаем свежие данные
    text, periods = get_schedule(queue, subgroup)
    
    # Сохраняем в кэш
    SCHEDULE_CACHE[cache_key] = {
        "text": text,
        "periods": periods,
        "timestamp": now
    }
    
    return text, periods
def get_schedule(queue: str, subgroup: str):
    """Парсит график отключений через Bright Data Web Unlocker."""
    url = f"{BASE_URL}/{queue}-{subgroup}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
    }

    try:
        logging.info(f"Запрос к {url} через Web Unlocker")
        
        # Используем Web Unlocker прокси для обхода Cloudflare
        response = requests.get(
            url, 
            headers=headers, 
            proxies=WEB_UNLOCKER_PROXY,
            timeout=60,
            verify=False  # Отключаем проверку SSL для прокси
        )
        
        if response.status_code != 200:
            logging.error(f"Статус {response.status_code} для {url}")
            return f"⚠️ Сайт недоступен (код {response.status_code}). Попробуйте через минуту.", []
        
        # Проверяем, не Cloudflare ли challenge
        if "Just a moment" in response.text or "challenge" in response.text.lower():
            logging.warning("Получен Cloudflare challenge")
            return "⚠️ Сайт временно недоступен. Попробуйте через минуту.", []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        container = soup.find('div', class_='periods_items')

        if not container:
            logging.warning("Контейнер 'periods_items' не найден")
            return "✅ Графика нет або структура сайту змінилася.", []

        items = container.find_all('span')
        
        if not items:
            return "✅ На сьогодні відключень не заплановано.", []

        # Парсим расписание и извлекаем временные интервалы
        result_lines = []
        time_periods = []
        
        for item in items:
            text = item.get_text(separator=" ", strip=True)
            text = text.replace("год. год.", "год.").replace("тривалість", "⏳")
            
            if text and len(text) > 3:
                if "З" in text and "до" in text:
                    result_lines.append(f"🔴 {text}")
                    # Извлекаем время начала и конца
                    time_match = re.search(r'З\s*(\d{2}):(\d{2})\s*до\s*(\d{2}):(\d{2})', text)
                    if time_match:
                        start_hour, start_min, end_hour, end_min = time_match.groups()
                        time_periods.append({
                            'start': f"{start_hour}:{start_min}",
                            'end': f"{end_hour}:{end_min}",
                            'type': 'outage'
                        })
                else:
                    result_lines.append(f"   {text}")

        if not result_lines:
            return "✅ Відключень у графіку не знайдено.", []

        header = f"💡 <b>Графік для {queue} черги ({subgroup} підгрупи)</b>\n" \
                 f"📅 <i>Дані з energy-ua.info</i>\n" \
                 f"{'─'*30}\n"
        
        logging.info(f"Успешно получены данные: {len(time_periods)} периодов")
        return header + "\n".join(result_lines), time_periods

    except requests.exceptions.Timeout:
        logging.error("Таймаут запроса")
        return "❌ Таймаут: сайт не відповідає.", []
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка запроса: {e}")
        return f"❌ Помилка при отриманні даних: {type(e).__name__}", []
    except Exception as e:
        logging.error(f"Неожиданная ошибка: {e}", exc_info=True)
        return "❌ Непередбачена помилка при обробці.", []

# --- ФУНКЦИЯ ОТПРАВКИ УВЕДОМЛЕНИЙ ---
async def send_notification(user_id: int, message: str):
    """Отправляет уведомление пользователю."""
    try:
        await bot.send_message(user_id, message, parse_mode="HTML")
        logging.info(f"Уведомление отправлено пользователю {user_id}")
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# --- ФОНОВАЯ ЗАДАЧА МОНИТОРИНГА ---
async def monitor_schedule():
    """Фоновая задача для проверки расписания и отправки уведомлений."""
    await asyncio.sleep(10)  # Ждем запуска бота
    
    while True:
        try:
            current_time = now_kyiv()  # Используем киевское время
            logging.info(f"[MONITOR] Проверка расписания в {current_time.strftime('%H:%M')}")
            
            for user_id, subscription in user_subscriptions.items():
                queue = subscription.get('queue', '2')
                subgroup = subscription.get('subgroup', '1')
                
                # Получаем расписание из кэша
                schedule_text, time_periods = get_schedule_cached(queue, subgroup)
                
                if not time_periods:
                    continue
                
                # Проверяем каждый период
                for period in time_periods:
                    try:
                        # Парсим время начала отключения с таймзоной Киева
                        start_time = datetime.strptime(period['start'], '%H:%M').replace(
                            year=current_time.year,
                            month=current_time.month,
                            day=current_time.day,
                            tzinfo=KYIV_TZ
                        )
                        
                        # Парсим время окончания отключения с таймзоной Киева
                        end_time = datetime.strptime(period['end'], '%H:%M').replace(
                            year=current_time.year,
                            month=current_time.month,
                            day=current_time.day,
                            tzinfo=KYIV_TZ
                        )
                        
                        # Проверяем за 5 минут до отключения
                        time_before_start = start_time - timedelta(minutes=5)
                        if abs((current_time - time_before_start).total_seconds()) < 60:
                            message = (
                                f"⚠️ <b>УВАГА!</b>\n\n"
                                f"🔴 Через 5 хвилин відключать світло!\n"
                                f"⏰ Час відключення: <b>{period['start']}</b>\n"
                                f"⏳ До: <b>{period['end']}</b>"
                            )
                            await send_notification(user_id, message)
                        
                        # Проверяем за 5 минут до включения
                        time_before_end = end_time - timedelta(minutes=5)
                        if abs((current_time - time_before_end).total_seconds()) < 60:
                            message = (
                                f"✅ <b>ДОБРА НОВИНА!</b>\n\n"
                                f"💡 Через 5 хвилин увімкнуть світло!\n"
                                f"⏰ Час увімкнення: <b>{period['end']}</b>"
                            )
                            await send_notification(user_id, message)
                    
                    except Exception as e:
                        logging.error(f"Ошибка обработки периода {period}: {e}")
                        continue
            
            # Проверяем каждую минуту
            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Ошибка в monitor_schedule: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- ОБРАБОТЧИКИ БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [InlineKeyboardButton(text="⚡ Перевірити графік", callback_data="check_2_1")],
        [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="subscribe")],
        [InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe")],
        [InlineKeyboardButton(text="❓ Допомога", callback_data="help")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
    
    await message.answer(
        "👋 Привіт! Я бот моніторингу відключень світла.\n\n"
        "🔔 Я можу надсилати сповіщення за 5 хвилин до відключення та увімкнення світла!\n\n"
        "Натисни кнопку нижче:", 
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "check_2_1")
async def process_callback_check(callback_query: types.CallbackQuery):
    await callback_query.answer("Завантажую дані...")
    
    msg = await callback_query.message.answer("⏳ Отримую дані з сайту...")
    
    loop = asyncio.get_event_loop()
    # Используем кэшированную версию с force=True для принудительного обновления
    schedule_text, _ = await loop.run_in_executor(None, get_schedule_cached, "2", "1", True)
    
    kb = [
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="check_2_1")],
        [InlineKeyboardButton(text="🔔 Підписатись на сповіщення", callback_data="subscribe")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb)

    await msg.edit_text(schedule_text or "❌ Помилка отримання даних", parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data == "subscribe")
async def process_subscribe(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if user_id in user_subscriptions:
        await callback_query.answer("Ви вже підписані на сповіщення!", show_alert=True)
        return
    
    user_subscriptions[user_id] = {'queue': '2', 'subgroup': '1'}
    
    await callback_query.message.answer(
        "✅ <b>Підписка активована!</b>\n\n"
        "🔔 Ви будете отримувати сповіщення:\n"
        "• За 5 хвилин до відключення світла\n"
        "• За 5 хвилин до увімкнення світла\n\n"
        "Черга: <b>2 (підгрупа 1)</b>",
        parse_mode="HTML"
    )
    await callback_query.answer()

@dp.callback_query(F.data == "unsubscribe")
async def process_unsubscribe(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if user_id not in user_subscriptions:
        await callback_query.answer("Ви не підписані на сповіщення!", show_alert=True)
        return
    
    del user_subscriptions[user_id]
    
    await callback_query.message.answer(
        "🔕 <b>Підписка скасована</b>\n\n"
        "Ви більше не будете отримувати сповіщення.",
        parse_mode="HTML"
    )
    await callback_query.answer()

@dp.callback_query(F.data == "help")
async def process_callback_help(callback_query: types.CallbackQuery):
    help_text = (
        "ℹ️ <b>Допомога</b>\n\n"
        "🔔 <b>Як працюють сповіщення?</b>\n"
        "Бот автоматично перевіряє графік кожні 10 хвилин та надсилає вам повідомлення:\n"
        "• За 5 хвилин до відключення\n"
        "• За 5 хвилин до увімкнення\n\n"
        "📋 <b>Команди:</b>\n"
        "/start - Головне меню\n\n"
        "⚙️ Черга: 2 (підгрупа 1)\n"
        "Щоб змінити чергу, зверніться до розробника."
    )
    await callback_query.message.answer(help_text, parse_mode="HTML")
    await callback_query.answer()

# --- ЗАПУСК ---
async def main():
    print("🤖 Бот запущений та готовий до роботи!")
    print("🔓 Використовується Bright Data Web Unlocker")
    print("🔔 Фонова задача моніторингу запущена")
    
    # Запускаем фоновую задачу мониторинга
    asyncio.create_task(monitor_schedule())
    
    await bot.delete_webhook(drop_pending_updates=True) 
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот зупинено користувачем.")
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import random
from datetime import datetime, timedelta
import re

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ'  # тестовый токен, как у тебя
BASE_URL = "https://tight-poetry-a5b0.dmitryhritsun.workers.dev/cherga"

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Хранилище подписок пользователей {user_id: {'queue': '2', 'subgroup': '1'}}
user_subscriptions = {}

# Кэш расписания, чтобы не дергать сайт слишком часто
# Формат: { "2-1": {"text": str, "time_periods": list[dict], "timestamp": datetime} }
SCHEDULE_CACHE = {}
CACHE_TTL_MINUTES = 10  # запрашиваем сайт не чаще, чем раз в 10 минут


# --- ОБЩИЙ ПАРСЕР HTML ---


def parse_schedule_html(html: str, queue: str, subgroup: str):
    """Общий парсер HTML для обеих функций (requests / cloudscraper)."""
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.find('div', class_='periods_items')

    if not container:
        logging.warning("Контейнер 'periods_items' не найден")
        container = soup.find('div', {'class': lambda x: x and 'period' in x.lower()})
        if not container:
            return "✅ Графіка немає або структура сайту змінилася.", []

    items = container.find_all('span')

    if not items:
        return "✅ На сьогодні відключень не заплановано.", []

    result_lines = []
    time_periods = []

    for item in items:
        text = item.get_text(separator=" ", strip=True)
        text = text.replace("год. год.", "год.").replace("тривалість", "⏳")

        if text and len(text) > 3:
            if "З" in text and "до" in text:
                result_lines.append(f"🔴 {text}")
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

    header = (
        f"💡 <b>Графік для {queue} черги ({subgroup} підгрупи)</b>\n"
        f"📅 <i>Дані з energy-ua.info</i>\n"
        f"{'─' * 30}\n"
    )

    return header + "\n".join(result_lines), time_periods


# --- ОСНОВНОЙ ПАРСЕР ЧЕРЕЗ requests ---


def get_schedule(queue: str, subgroup: str):
    """Парсит график отключений через обычный requests (fallback)."""
    url = f"{BASE_URL}/{queue}-{subgroup}"

    user_agents = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    ]

    headers = {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Pragma': 'no-cache',
        'Cache-Control': 'no-cache',
    }

    try:
        session = requests.Session()

        # Прогреваем сессию
        try:
            session.get('https://energy-ua.info/', headers=headers, timeout=10, allow_redirects=True)
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            logging.warning(f"Не удалось прогреть сессию через requests: {e}")

        headers['Referer'] = 'https://energy-ua.info/'
        response = session.get(url, headers=headers, timeout=15, allow_redirects=True)

        if response.status_code == 403:
            logging.error(f"Все попытки неудачны (requests). Статус: {response.status_code}")
            return None, []

        if response.status_code != 200:
            logging.error(f"Все попытки неудачны (requests). Статус: {response.status_code}")
            return None, []

        return parse_schedule_html(response.text, queue, subgroup)

    except Exception as e:
        logging.error(f"Ошибка парсинга (requests): {e}", exc_info=True)
        return None, []


# --- ОСНОВНОЙ МЕТОД С CLOUDSCRAPER ---


def get_schedule_cloudscraper(queue: str, subgroup: str):
    """Основной метод с использованием cloudscraper для обхода защиты."""
    try:
        import cloudscraper
    except ImportError:
        logging.warning("cloudscraper не установлен, используем обычный requests")
        return None, []

    url = f"{BASE_URL}/{queue}-{subgroup}"

    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]

    try:
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True,
            },
            delay=10,
        )

        headers = {
            'User-Agent': random.choice(user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        }

        # Прогреваем сессию
        try:
            scraper.get('https://energy-ua.info/', headers=headers, timeout=15)
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            logging.warning(f"Не удалось прогреть сессию через cloudscraper: {e}")

        headers['Referer'] = 'https://www.google.com/'
        scraper.headers.update(headers)

        response = scraper.get(url, timeout=20)

        if response.status_code == 403:
            logging.error(f"Cloudscraper вернул 403 для {url}")
            return None, []

        if response.status_code != 200:
            logging.error(f"Cloudscraper вернул статус {response.status_code} для {url}")
            return None, []

        return parse_schedule_html(response.text, queue, subgroup)

    except Exception as e:
        logging.error(f"Cloudscraper error: {e}", exc_info=True)
        return None, []


# --- ОБЁРТКА С КЭШЕМ ---


def get_schedule_cached(queue: str, subgroup: str, force_refresh: bool = False):
    """
    Возвращает расписание из кэша, либо запрашивает с сайта.
    Кэш живёт CACHE_TTL_MINUTES, поэтому сайт дергается не чаще 1 раза в 10 минут
    для каждой очереди/подгруппы.
    """
    key = f"{queue}-{subgroup}"
    now = datetime.now()

    # Если не форсим и есть свежий кэш — возвращаем его
    if not force_refresh:
        cached = SCHEDULE_CACHE.get(key)
        if cached and now - cached["timestamp"] < timedelta(minutes=CACHE_TTL_MINUTES):
            return cached["text"], cached["time_periods"]

    # Иначе — запрашиваем с сайта
    schedule_text, time_periods = get_schedule_cloudscraper(queue, subgroup)
    if schedule_text is None:
        schedule_text, time_periods = get_schedule(queue, subgroup)

    if schedule_text is None:
        return None, []

    SCHEDULE_CACHE[key] = {
        "text": schedule_text,
        "time_periods": time_periods,
        "timestamp": now,
    }

    return schedule_text, time_periods


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
            current_time = datetime.now()
            logging.info(f"Проверка расписания в {current_time.strftime('%H:%M')}")

            for user_id, subscription in user_subscriptions.items():
                queue = subscription.get('queue', '2')
                subgroup = subscription.get('subgroup', '1')

                # Получаем расписание из кэша (сайт дергается не чаще 1 раза в 10 минут)
                schedule_text, time_periods = get_schedule_cached(queue, subgroup)
                if not time_periods:
                    continue

                # Проверяем каждый период
                for period in time_periods:
                    try:
                        # Парсим время начала отключения
                        start_time = datetime.strptime(period['start'], '%H:%M').replace(
                            year=current_time.year,
                            month=current_time.month,
                            day=current_time.day
                        )

                        # Парсим время окончания отключения
                        end_time = datetime.strptime(period['end'], '%H:%M').replace(
                            year=current_time.year,
                            month=current_time.month,
                            day=current_time.day
                        )

                        # Проверяем за 5 минут до отключения
                        time_before_start = start_time - timedelta(minutes=5)
                        if abs((current_time - time_before_start).total_seconds()) < 60:  # В пределах минуты
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

            # Проверяем каждую минуту (запросы к сайту ограничивает кэш)
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

    # Берём расписание через кэш. Даже если ты жмёшь "Оновити",
    # сайт всё равно не будет опрашиваться чаще, чем раз в 10 хвилин.
    schedule_text, _ = await asyncio.to_thread(get_schedule_cached, "2", "1", False)

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

    # Подписываем пользователя на очередь 2-1 (можно добавить выбор)
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
        "Бот автоматично перевіряє графік кожну хвилину та надсилає вам повідомлення:\n"
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
    print("💡 Для кращої роботи встановіть: pip install cloudscraper")
    print("🔔 Фонова задача моніторингу запущена")

    # Запускаем фоновую задачу мониторинга
    asyncio.create_task(monitor_schedule())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Бот зупинено користувачем.")

import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import re

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ'  # тестовый токен
ALERTS_URL = "https://alerts.org.ua/poltavska-oblast/"

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
CACHE_TTL_MINUTES = 10  # данные с сайта обновляем раз в ~10 минут для каждой групи


# --- ПАРСЕР alerts.org.ua ---

def get_schedule_alerts(queue: str, subgroup: str):
    """
    Парсим графік з alerts.org.ua для заданої групи, напр. queue='2', subgroup='1' → 'Група 2.1'.
    Повертаємо (текст для користувача, список періодів з відключеннями/включеннями).
    """
    group_code = f"{queue}.{subgroup}"  # '2.1'

    try:
        resp = requests.get(ALERTS_URL, timeout=15)
        if resp.status_code != 200:
            logging.error(f"alerts.org.ua status {resp.status_code}")
            return "❌ Помилка отримання даних з alerts.org.ua", []

        soup = BeautifulSoup(resp.text, "html.parser")

        # шукаємо потрібну групу
        target_group = None
        for group_div in soup.select("div.group"):
            name_tag = group_div.select_one("b.name")
            if not name_tag:
                continue
            name_text = name_tag.get_text(strip=True)
            if group_code in name_text:
                target_group = group_div
                break

        if target_group is None:
            return f"⚠️ Групу {group_code} не знайдено на alerts.org.ua", []

        result_lines = []
        periods = []

        # перебираємо всі div безпосередньо всередині group (рядки графіка)
        for div in target_group.find_all("div", recursive=False):
            classes = div.get("class", [])
            if "stat" in classes:
                continue  # ігноруємо статистику

            text = div.get_text(" ", strip=True)
            m = re.search(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", text)
            if not m:
                continue

            start, end = m.groups()

            # 24:00 → 23:59, щоб не падати на datetime.strptime
            if end == "24:00":
                end = "23:59"

            status_tag = div.find("b")
            status_class = (status_tag.get("class") or [""])[0] if status_tag else ""
            # статус: on / off / maybe
            if "off" in status_class:
                icon = "🔴"
                status_human = "Світла немає"
                period_type = "outage"
            elif "on" in status_class:
                icon = "🟢"
                status_human = "Світло є"
                period_type = "on"
            else:
                icon = "⚪️"
                status_human = "Можливі коливання"
                period_type = "maybe"

            result_lines.append(f"{icon} {start}–{end} • {status_human}")

            periods.append({
                "start": start,
                "end": end,
                "type": period_type,
            })

        if not periods:
            return "✅ Відключень для цієї групи не знайдено.", []

        header = (
            f"💡 <b>Графік для групи {group_code}</b>\n"
            f"📅 <i>Дані з alerts.org.ua</i>\n"
            f"{'─' * 30}\n"
        )

        return header + "\n".join(result_lines), periods

    except Exception as e:
        logging.error(f"Помилка парсингу alerts.org.ua: {e}", exc_info=True)
        return "❌ Помилка отримання даних з alerts.org.ua", []


# --- ОБЁРТКА С КЭШЕМ ---

def get_schedule_cached(queue: str, subgroup: str, force_refresh: bool = False):
    """
    Возвращает расписание из кэша, либо запрашивает с alerts.org.ua.
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
    schedule_text, time_periods = get_schedule_alerts(queue, subgroup)

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
                    # интересуют только отключения
                    if period.get("type") != "outage":
                        continue

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
        "Бот щохвилини перевіряє час відключень, але дані з сайту оновлює приблизно раз на 10 хвилин.\n"
        "Ви отримаєте повідомлення:\n"
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
    print("📡 Джерело графіка: alerts.org.ua")
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

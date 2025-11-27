import asyncio
import logging
import json
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---

# Токен бота берём из переменной окружения
API_TOKEN = os.getenv('API_TOKEN')
if not API_TOKEN:
    raise RuntimeError("API_TOKEN env var is not set")

DATA_FILE = 'schedule_data.json'

# Адрес базы данных PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is not set")

# ID админа (твой); можно задать и через ENV, но дефолтом ставим твой
ADMIN_ID = int(os.getenv("ADMIN_ID", "7034386844"))

# Таймзона Киева
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Списки смайлов
SAD_EMOJIS = ["😢", "😭", "😞", "😫", "😕", "😿", "💔", "😥", "☹️", "о_О", "🫤", "😣", "😔", "😖", "😩", "🥺", "😦", "😧", "😨", "😰"]
HAPPY_EMOJIS = ["😍", "🥰", "🥳", "😏", "😎", "😇", "🙂", "🎉", "😍", "🤩", "😁", "😀", "😃", "😄", "😆", "😉", "😊", "😋", "😌", "🙌"]

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Пул соединений с БД
db_pool: asyncpg.Pool | None = None


def now_kyiv():
    """Возвращает текущее время в таймзоне Киева"""
    return datetime.now(KYIV_TZ)


def get_random_sad():
    return random.choice(SAD_EMOJIS)


def get_random_happy():
    return random.choice(HAPPY_EMOJIS)


# --- РАБОТА С БАЗОЙ ДАННЫХ ---


async def init_db():
    """Создаём пул и таблицы, если их нет."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        # Список всех юзеров бота
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TIMESTAMPTZ DEFAULT NOW(),
                last_seen TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        # Подписчики уведомлений
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id BIGINT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
    logging.info("📦 База данных инициализирована")


async def save_user(message: types.Message):
    """Сохраняем/обновляем информацию о пользователе (таблица users)."""
    if db_pool is None:
        return
    u = message.from_user
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_seen = NOW();
            """,
            u.id,
            u.username,
            u.first_name,
            u.last_name,
        )


async def add_subscription(user_id: int):
    """Добавить юзера в список подписчиков."""
    if db_pool is None:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO subscribers (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING;
            """,
            user_id,
        )


async def remove_subscription(user_id: int):
    """Удалить юзера из списка подписчиков."""
    if db_pool is None:
        return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM subscribers WHERE user_id = $1;", user_id)


async def is_subscribed(user_id: int) -> bool:
    """Проверить, подписан ли юзер."""
    if db_pool is None:
        return False
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM subscribers WHERE user_id = $1 LIMIT 1;", user_id
        )
    return row is not None


async def get_all_subscribers() -> list[int]:
    """Вернуть список всех user_id подписчиков."""
    if db_pool is None:
        return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM subscribers;")
    return [r["user_id"] for r in rows]


# --- ЧТЕНИЕ ДАННЫХ ИЗ ФАЙЛА ---


def get_schedule_data():
    """Читает 'сырые' данные из JSON файла."""
    if not os.path.exists(DATA_FILE):
        return None, None, None

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        last_update_ts = data.get("timestamp") or data.get("updated_at", 0)
        last_update_dt = datetime.fromtimestamp(last_update_ts, KYIV_TZ)

        text_lines = data.get("text_lines", [])
        periods = data.get("periods", [])

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


def build_schedule_answer():
    """Готовый текст для /check и для кнопки обновления."""
    last_update, lines, _ = get_schedule_data()
    base_text = format_schedule_message(last_update, lines)
    current_time_str = now_kyiv().strftime("%H:%M:%S")
    final_text = f"{base_text}\n\n⏱ Запит оновлено: {current_time_str}"
    return final_text


# --- ФОНОВАЯ ЗАДАЧА УВЕДОМЛЕНИЙ ---


async def monitor_schedule_task():
    """Проверяет файл и отправляет уведомления подписчикам."""
    logging.info("🔔 Мониторинг запущен")
    await asyncio.sleep(5)

    while True:
        try:
            current_time = now_kyiv()
            _, _, time_periods = get_schedule_data()

            if time_periods:
                subscribers = await get_all_subscribers()

                for user_id in subscribers:
                    # перебираем все периоды для каждого подписчика
                    for i, period in enumerate(time_periods):
                        try:
                            start_dt = datetime.strptime(
                                period["start"], "%H:%M"
                            ).replace(
                                year=current_time.year,
                                month=current_time.month,
                                day=current_time.day,
                                tzinfo=KYIV_TZ,
                            )

                            end_dt = datetime.strptime(period["end"], "%H:%M").replace(
                                year=current_time.year,
                                month=current_time.month,
                                day=current_time.day,
                                tzinfo=KYIV_TZ,
                            )

                            # --- 1. ЛОГИКА ОТКЛЮЧЕНИЯ (СУМНИЙ СМАЙЛ) ---
                            diff_start = (start_dt - current_time).total_seconds()

                            if 240 < diff_start <= 300:  # 4-5 минут до
                                await bot.send_message(
                                    user_id,
                                    f"{get_random_sad()} Через 5 хвилин відключать світло!\n\n"
                                    f"🔴 Орієнтовно з {period['start']} до {period['end']}\n\n"
                                    f"🔕 /unsub - відписатись\n"
                                    f"👀 /check - перевірити графік",
                                )

                            # --- 2. ЛОГИКА ВКЛЮЧЕНИЯ (ВЕСЕЛИЙ СМАЙЛ) ---
                            diff_end = (end_dt - current_time).total_seconds()

                            if 240 < diff_end <= 300:  # 4-5 минут до конца
                                next_off_time = "кінця доби"

                                if i + 1 < len(time_periods):
                                    next_period = time_periods[i + 1]
                                    next_off_time = next_period["start"]

                                await bot.send_message(
                                    user_id,
                                    f"{get_random_happy()} Через 5 хвилин увімкнуть світло!\n\n"
                                    f"🟢 Орієнтовно з {period['end']} до {next_off_time}\n\n"
                                    f"🔕 /unsub - відписатись\n"
                                    f"👀 /check - перевірити графік",
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
        [
            InlineKeyboardButton(
                text="🔔 Підписатись на сповіщення", callback_data="subscribe"
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_schedule_keyboard():
    kb = [
        [InlineKeyboardButton(text="🔄 Оновити графік", callback_data="check_2_1")],
        [
            InlineKeyboardButton(text="🔔 Підписатись", callback_data="subscribe"),
            InlineKeyboardButton(text="🔕 Відписатись", callback_data="unsubscribe"),
        ],
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
    await save_user(message)
    await message.answer(
        "👋 Привіт! Я бот моніторингу відключень світла (2 черга, 1 підгрупа).\n\n"
        "Ти можеш перевірити графік або підключити сповіщення за 5 хвилин до відключення або включення світла ✅",
        reply_markup=get_start_keyboard(),
    )


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    await save_user(message)
    final_text = build_schedule_answer()
    await message.answer(final_text, reply_markup=get_schedule_keyboard())


@dp.message(Command("unsub"))
async def cmd_unsub(message: types.Message):
    await save_user(message)
    user_id = message.from_user.id
    await remove_subscription(user_id)
    await message.answer("🔕 Підписку скасовано.", reply_markup=get_sub_button())


@dp.callback_query(F.data == "check_2_1")
async def cb_check(callback: types.CallbackQuery):
    final_text = build_schedule_answer()

    try:
        await callback.message.edit_text(
            final_text,
            reply_markup=get_schedule_keyboard(),
        )
    except Exception:
        pass

    await callback.answer()


@dp.callback_query(F.data == "subscribe")
async def cb_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await add_subscription(user_id)

    await callback.message.answer(
        "✅ Ви підписалися на сповіщення (за 5 хв до відключення та включення світла)",
        reply_markup=get_unsub_button(),
    )
    await callback.answer()


@dp.callback_query(F.data == "unsubscribe")
async def cb_unsub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await remove_subscription(user_id)
    await callback.message.answer(
        "🔕 Підписку скасовано.",
        reply_markup=get_sub_button(),
    )
    await callback.answer()


# --- СКРЫТАЯ АДМИН-КОМАНДА РАССЫЛКИ ---


@dp.message(Command("adminmsg"))
async def cmd_adminmsg(message: types.Message):
    """
    Скрытая команда для рассылки всем подписчикам.
    Использование: /adminmsg любой текст (можно с HTML разметкой).
    Работает только для ADMIN_ID.
    """
    if message.from_user.id != ADMIN_ID:
        # Игнорируем, никакой ошибки пользователю
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Напиши так: /adminmsg ТЕКСТ_ПОВІДОМЛЕННЯ")
        return

    text = parts[1]

    await message.answer("🚀 Надсилаю повідомлення всім підписникам...")

    subscribers = await get_all_subscribers()
    sent = 0

    for user_id in subscribers:
        try:
            await bot.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.05)  # маленькая пауза против flood limit
        except Exception as e:
            logging.error(f"Не вдалося надіслати {user_id}: {e}")

    await message.answer(f"✅ Розсилка завершена. Надіслано: {sent}")


# --- ЗАПУСК ---


async def main():
    print("🤖 Бот запускается...")

    # Инициализируем БД и таблицы
    await init_db()

    # Запускаем фоновый монитор
    asyncio.create_task(monitor_schedule_task())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено.")

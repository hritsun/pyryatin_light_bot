import asyncio
import logging
import json
import os
import random
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

# --- КОНФИГУРАЦИЯ ---

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN env var is not set")

DATA_FILE = "schedule_data.json"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is not set")

ADMIN_ID = int(os.getenv("ADMIN_ID", "7034386844"))

KYIV_TZ = ZoneInfo("Europe/Kyiv")

SAD_EMOJIS = [
    "😢", "😭", "😞", "😫", "😕", "😿", "💔", "😥", "☹️", "о_О",
    "🫤", "😣", "😔", "😖", "😩", "🥺", "😦", "😧", "😨", "😰",
]
HAPPY_EMOJIS = [
    "😍", "🥰", "🥳", "😏", "😎", "😇", "🙂", "🎉", "😍", "🤩",
    "😁", "😀", "😃", "😄", "😆", "😉", "😊", "😋", "😌", "🙌",
]

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()

# Пул соединений с БД
db_pool: asyncpg.Pool | None = None

# Ожидающая подтверждения админ-рассылка: {admin_id: text}
pending_admin_messages: dict[int, str] = {}


def now_kyiv():
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
    if db_pool is None:
        return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM subscribers WHERE user_id = $1;", user_id)


async def is_subscribed(user_id: int) -> bool:
    if db_pool is None:
        return False
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM subscribers WHERE user_id = $1 LIMIT 1;", user_id
        )
    return row is not None


async def get_all_subscribers() -> list[int]:
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

    is_old = (now_kyiv() - last_update_dt).total_seconds() > 1800

    clean_lines = []
    for line in text_lines:
        if "🔴" in line or "год." in line:
            clean_lines.append(line)

    if not clean_lines:
        schedule_body = "✅ Відключень не знайдено."
    else:
        schedule_body = "\n".join(clean_lines)

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

                            # 1. За 5 минут до отключения
                            diff_start = (start_dt - current_time).total_seconds()
                            if 240 < diff_start <= 300:
                                await bot.send_message(
                                    user_id,
                                    f"{get_random_sad()} Через 5 хвилин відключать світло!\n\n"
                                    f"🔴 Орієнтовно з {period['start']} до {period['end']}\n\n"
                                    f"🔕 /unsub - відписатись\n"
                                    f"👀 /check - перевірити графік",
                                )

                            # 2. За 5 минут до включения
                            diff_end = (end_dt - current_time).total_seconds()
                            if 240 < diff_end <= 300:
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


# --- ОБЫЧНЫЕ КОМАНДЫ ---


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await save_user(message)
    await message.answer(
        "👋 Привіт! Я бот моніторингу відключень світла (2 черга, 1 підгрупа).\n\n"
        "Ти можеш перевірити графік або підключити сповіщення за 5 хвилин "
        "до відключення або включення світла ✅",
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

    if not await is_subscribed(user_id):
        await message.answer("Ви не були підписані.", reply_markup=get_sub_button())
        return

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

    # Уже подписан — просто алерт, без спама
    if await is_subscribed(user_id):
        await callback.answer("Ви вже підписані на сповіщення ✅", show_alert=True)
        return

    await add_subscription(user_id)

    await callback.message.answer(
        "✅ Ви підписалися на сповіщення (за 5 хв до відключення та включення світла)",
        reply_markup=get_unsub_button(),
    )
    await callback.answer()


@dp.callback_query(F.data == "unsubscribe")
async def cb_unsub(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    if not await is_subscribed(user_id):
        await callback.answer("Ви не підписані.", show_alert=True)
        return

    await remove_subscription(user_id)
    await callback.message.answer(
        "🔕 Підписку скасовано.",
        reply_markup=get_sub_button(),
    )
    await callback.answer()


# --- АДМИН /admin: чек-лист команд ---


@dp.message(Command("admin"))
async def cmd_admin_help(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    text = (
        "🛠 <b>Адмін-панель</b>\n\n"
        "<b>/admin</b> – показати цей список команд\n"
        "<b>/adminmsg ТЕКСТ</b> – створити чернетку розсилки з попереднім переглядом\n"
        "<b>/admincancel</b> – скасувати поточну чернетку розсилки\n"
        "<b>/adminstats</b> – статистика користувачів та підписників\n\n"
        "Після <b>/adminmsg</b> бот покаже кнопки:\n"
        "• ✅ <i>Надіслати всім</i> – запуск розсилки\n"
        "• ❌ <i>Скасувати розсилку</i> – видалити чернетку без відправки\n"
    )

    await message.answer(text)


# --- АДМИН: РАССЫЛКА С ПРЕВЬЮ И ОТМЕНОЙ ---


@dp.message(Command("adminmsg"))
async def cmd_adminmsg(message: types.Message):
    """
    /adminmsg ТЕКСТ
    1) Сохраняет текст как "ожидающий рассылки"
    2) Показывает превью + кнопки "Отправить / Отменить"
    """
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Напиши так: /adminmsg ТЕКСТ_ПОВІДОМЛЕННЯ")
        return

    text = parts[1]
    pending_admin_messages[ADMIN_ID] = text

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Надіслати всім", callback_data="admin_send_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Скасувати розсилку", callback_data="admin_send_cancel"
                )
            ],
        ]
    )

    await message.answer(
        "📝 <b>Попередній перегляд повідомлення:</b>\n\n"
        f"{text}\n\n"
        "Надіслати це повідомлення всім підписникам?",
        reply_markup=kb,
    )


@dp.message(Command("admincancel"))
async def cmd_admincancel(message: types.Message):
    """
    /admincancel — отмена текущей ожидающей рассылки без нажатия кнопки.
    """
    if message.from_user.id != ADMIN_ID:
        return

    if pending_admin_messages.pop(ADMIN_ID, None) is None:
        await message.answer("❌ Немає активної розсилки для скасування.")
    else:
        await message.answer("❌ Поточну розсилку скасовано.")


@dp.callback_query(F.data == "admin_send_cancel")
async def cb_admin_cancel(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Недостатньо прав.", show_alert=True)
        return

    if pending_admin_messages.pop(ADMIN_ID, None) is None:
        await callback.answer("Немає активної розсилки.", show_alert=True)
        return

    try:
        await callback.message.edit_text("❌ Розсилку скасовано адміном.")
    except Exception:
        pass

    await callback.answer("Розсилку скасовано.")


@dp.callback_query(F.data == "admin_send_confirm")
async def cb_admin_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Недостатньо прав.", show_alert=True)
        return

    text = pending_admin_messages.pop(ADMIN_ID, None)
    if not text:
        await callback.answer("Немає активної розсилки.", show_alert=True)
        return

    subscribers = await get_all_subscribers()
    total = len(subscribers)
    sent = 0

    try:
        await callback.message.edit_text("🚀 Починаю розсилку всім підписникам...")
    except Exception:
        pass

    for user_id in subscribers:
        try:
            await bot.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.05)  # анти-флуд
        except Exception as e:
            logging.error(f"Не вдалося надіслати {user_id}: {e}")

    await bot.send_message(
        ADMIN_ID,
        f"✅ Розсилка завершена.\n"
        f"👥 Підписників загалом: <b>{total}</b>\n"
        f"📨 Успішно надіслано: <b>{sent}</b>",
    )

    await callback.answer("Розсилку завершено ✅")


# --- АДМИН: СТАТИСТИКА ---


@dp.message(Command("adminstats"))
async def cmd_adminstats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if db_pool is None:
        await message.answer("База ще не ініціалізована.")
        return

    async with db_pool.acquire() as conn:
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users;")
        subs_count = await conn.fetchval("SELECT COUNT(*) FROM subscribers;")

    await message.answer(
        "📊 <b>Статистика бота</b>:\n"
        f"👥 Користувачів у таблиці <code>users</code>: <b>{users_count}</b>\n"
        f"🔔 Підписників у таблиці <code>subscribers</code>: <b>{subs_count}</b>"
    )


# --- ЗАПУСК ---


async def main():
    print("🤖 Бот запускается...")
    await init_db()
    asyncio.create_task(monitor_schedule_task())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено.")

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

# --- КОНФИГ ---
API_TOKEN = "8410212460:AAGW8aqzXbatKpXYyLq6Tog7gdNIy4UBwJQ"
ALERTS_URL = "https://alerts.org.ua/poltavska-oblast/"
KYIV_TZ = ZoneInfo("Europe/Kyiv")

def now_kyiv():
    return datetime.now(KYIV_TZ)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# user_id: {'queue': '3', 'subgroup': '2'}
user_subscriptions = {}

# group_code: cached data
SCHEDULE_CACHE = {}
CACHE_TTL_MINUTES = 10


# =======================
#  PARSER alerts.org.ua
# =======================

def get_schedule_alerts(queue: str, subgroup: str):
    group_code = f"{queue}.{subgroup}"

    try:
        r = requests.get(ALERTS_URL, timeout=15)
        if r.status_code != 200:
            return "❌ Помилка отримання даних з alerts.org.ua", []

        soup = BeautifulSoup(r.text, "html.parser")

        target = None
        for div in soup.select("div.group"):
            name = div.select_one("b.name")
            if name and group_code in name.get_text(strip=True):
                target = div
                break

        if not target:
            return f"⚠️ Групу {group_code} не знайдено.", []

        result_lines = []
        periods = []

        for div in target.find_all("div", recursive=False):
            if "stat" in (div.get("class") or []):
                continue

            txt = div.get_text(" ", strip=True)
            m = re.search(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", txt)
            if not m:
                continue

            start, end = m.groups()
            if end == "24:00":
                end = "23:59"

            st = div.find("b")
            cls = st.get("class")[0] if st and st.get("class") else ""

            if "off" in cls:
                icon, human, tp = "🔴", "Світла немає", "outage"
            elif "on" in cls:
                icon, human, tp = "🟢", "Світло є", "on"
            else:
                icon, human, tp = "⚪️", "Можливі коливання", "maybe"

            result_lines.append(f"{icon} {start}–{end} • {human}")

            periods.append({"start": start, "end": end, "type": tp})

        if not periods:
            return "Графік порожній.", []

        header = (
            f"💡 <b>Група {group_code}</b>\n"
            f"📅 <i>alerts.org.ua</i>\n"
            f"{'─' * 30}\n"
        )

        return header + "\n".join(result_lines), periods

    except Exception as e:
        return "❌ Помилка зв'язку з alerts.org.ua", []


# =======================
#  CACHE WRAPPER
# =======================

def get_schedule_cached(queue, subgroup, force=False):
    key = f"{queue}-{subgroup}"
    now = now_kyiv()

    if not force:
        c = SCHEDULE_CACHE.get(key)
        if c and now - c["timestamp"] < timedelta(minutes=CACHE_TTL_MINUTES):
            return c["text"], c["periods"]

    text, periods = get_schedule_alerts(queue, subgroup)
    SCHEDULE_CACHE[key] = {
        "text": text,
        "periods": periods,
        "timestamp": now,
    }
    return text, periods


# =======================
#  MONITOR
# =======================

async def send_notification(uid, msg):
    try:
        await bot.send_message(uid, msg, parse_mode="HTML")
    except:
        pass

async def monitor_schedule():
    await asyncio.sleep(10)

    while True:
        now = now_kyiv()
        logging.info(f"[MONITOR] {now.strftime('%H:%M')}")

        for uid, sub in user_subscriptions.items():
            q, sg = sub["queue"], sub["subgroup"]

            text, periods = get_schedule_cached(q, sg)
            if not periods:
                continue

            for p in periods:
                if p["type"] != "outage":
                    continue

                start = datetime.strptime(p["start"], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KYIV_TZ
                )
                end = datetime.strptime(p["end"], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KYIV_TZ
                )

                # 5m before OFF
                if abs((now - (start - timedelta(minutes=5))).total_seconds()) < 60:
                    await send_notification(
                        uid,
                        f"⚠️ Через 5 хвилин вимкнуть світло!\n"
                        f"⏰ <b>{p['start']}–{p['end']}</b>"
                    )

                # 5m before ON
                if abs((now - (end - timedelta(minutes=5))).total_seconds()) < 60:
                    await send_notification(
                        uid,
                        f"💡 Через 5 хвилин увімкнуть світло!\n"
                        f"⏰ <b>{p['end']}</b>"
                    )

        await asyncio.sleep(60)


# =======================
#  BUTTONS
# =======================

def groups_keyboard():
    kb = []
    for q in range(1, 5):
        row = []
        for sg in range(1, 5):
            code = f"{q}.{sg}"
            row.append(InlineKeyboardButton(text=code, callback_data=f"grp_{q}_{sg}"))
        kb.append(row)
    return InlineKeyboardMarkup(inline_keyboard=kb)


# =======================
#  HANDLERS
# =======================

@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    kb = [
        [InlineKeyboardButton(text="📋 Обрати групу", callback_data="choose")],
        [InlineKeyboardButton(text="🔔 Підписатись", callback_data="subscribe")],
        [InlineKeyboardButton(text="⚡ Перевірити графік", callback_data="check")],
        [InlineKeyboardButton(text="❓ Допомога", callback_data="help")],
    ]
    await msg.answer(
        "👋 Привіт! Обери свою групу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


@dp.callback_query(F.data == "choose")
async def choose_group(cb: types.CallbackQuery):
    await cb.message.answer("Оберіть групу:", reply_markup=groups_keyboard())
    await cb.answer()


@dp.callback_query(F.data.startswith("grp_"))
async def set_group(cb: types.CallbackQuery):
    _, q, sg = cb.data.split("_")
    user_subscriptions[cb.from_user.id] = {"queue": q, "subgroup": sg}

    await cb.message.answer(f"✅ Встановлено групу: <b>{q}.{sg}</b>", parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data == "subscribe")
async def subscribe(cb: types.CallbackQuery):
    if cb.from_user.id not in user_subscriptions:
        await cb.answer("Спочатку оберіть групу!", show_alert=True)
        return

    await cb.message.answer("🔔 Підписка активована!")
    await cb.answer()


@dp.callback_query(F.data == "check")
async def check_now(cb: types.CallbackQuery):
    if cb.from_user.id not in user_subscriptions:
        await cb.answer("Спочатку оберіть групу!", show_alert=True)
        return

    q = user_subscriptions[cb.from_user.id]["queue"]
    sg = user_subscriptions[cb.from_user.id]["subgroup"]

    text, _ = get_schedule_cached(q, sg)
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data == "help")
async def help_info(cb: types.CallbackQuery):
    await cb.message.answer(
        "ℹ️ Бот надсилає попередження за 5 хвилин до вимкнення/вмикання світла.\n"
        "Оберіть групу → Підпишіться → Отримуйте сповіщення."
    )
    await cb.answer()


# =======================
#  RUN
# =======================

async def main():
    print("Бот запущено.")
    asyncio.create_task(monitor_schedule())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

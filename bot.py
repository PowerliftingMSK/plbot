import asyncio
import logging
import os
import random
import sqlite3
import string
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────── НАСТРОЙКИ ───────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
# BotHost задаёт DATA_DIR=/app/data. Локально — текущая папка.
DATA_DIR = os.getenv("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "powerbot.db")

logging.basicConfig(level=logging.INFO)
router = Router()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# ─────────────────────── БАЗА ───────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        tz_offset INTEGER DEFAULT 3, role TEXT DEFAULT 'athlete');
    CREATE TABLE IF NOT EXISTS programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
        weeks INTEGER DEFAULT 4, created_at TEXT);
    CREATE TABLE IF NOT EXISTS program_days (
        id INTEGER PRIMARY KEY AUTOINCREMENT, program_id INTEGER,
        week INTEGER, day_order INTEGER, name TEXT);
    CREATE TABLE IF NOT EXISTS program_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day_id INTEGER, name TEXT,
        sets INTEGER, reps INTEGER, weight REAL,
        weight_type TEXT DEFAULT 'percent', is_accessory INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS diary (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT,
        session_id TEXT, exercise TEXT, weight REAL, sets INTEGER,
        reps INTEGER, rpe REAL, tonnage REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS supplements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
        dosage TEXT, times TEXT);
    CREATE TABLE IF NOT EXISTS sent_log (key TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS user_maxes (
        user_id INTEGER, exercise TEXT, weight REAL, updated_at TEXT,
        PRIMARY KEY (user_id, exercise));
    CREATE TABLE IF NOT EXISTS trainer_links (
        trainer_id INTEGER, athlete_id INTEGER,
        PRIMARY KEY (trainer_id, athlete_id));
    CREATE TABLE IF NOT EXISTS invite_codes (
        code TEXT PRIMARY KEY, trainer_id INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS user_progress (
        user_id INTEGER, program_id INTEGER,
        current_week INTEGER DEFAULT 1, current_day_order INTEGER DEFAULT 1,
        updated_at TEXT, PRIMARY KEY (user_id, program_id));
    CREATE TABLE IF NOT EXISTS body_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT,
        height REAL, weight REAL, fat_pct REAL, muscle_pct REAL, water_pct REAL);
    CREATE TABLE IF NOT EXISTS water_settings (
        user_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0,
        interval_hours REAL DEFAULT 2, start_time TEXT DEFAULT '08:00',
        end_time TEXT DEFAULT '22:00');
    """)
    conn.commit()
    conn.close()


def ensure_user(user_id, username=None):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                 (user_id, username))
    conn.commit()
    conn.close()


# ─────────────────────── ШАБЛОНЫ ПРОГРАММ ───────────────────────
BENCH_PROGRAM = {"name": "Жим лёжа (цикл)", "weeks": 7, "days": [
    (1, 1, "Неделя 1 · Жим 1", [("Жим лёжа", 5, 3, 80, "percent")]),
    (1, 2, "Неделя 1 · Жим 2", [("Жим лёжа", 5, 5, 70, "percent")]),
    (2, 1, "Неделя 2 · Жим 1", [("Жим лёжа", 5, 4, 75, "percent")]),
    (2, 2, "Неделя 2 · Жим 2", [("Жим лёжа", 4, 5, 70, "percent")]),
    (3, 1, "Неделя 3 · Жим 1", [("Жим лёжа", 3, 4, 80, "percent")]),
    (3, 2, "Неделя 3 · Жим 2", [("Жим лёжа", 4, 4, 70, "percent")]),
    (4, 1, "Неделя 4 · Жим 1", [("Жим лёжа", 3, 3, 80, "percent")]),
    (4, 2, "Неделя 4 · Жим 2", [("Жим лёжа", 5, 4, 75, "percent")]),
    (5, 1, "Неделя 5 · Жим 1", [("Жим лёжа", 5, 4, 80, "percent")]),
    (5, 2, "Неделя 5 · Жим 2", [("Жим лёжа", 4, 5, 70, "percent")]),
    (6, 1, "Неделя 6 · Жим 1", [("Жим лёжа", 2, 2, 85, "percent"),
                                ("Жим лёжа", 3, 3, 80, "percent")]),
    (6, 2, "Неделя 6 · Жим 2", [("Жим лёжа", 4, 4, 75, "percent")]),
    (7, 1, "Неделя 7 · Пик", [("Жим лёжа", 2, 5, 85, "percent"),
                               ("Жим лёжа", 2, 3, 80, "percent")]),
]}

SQUAT_PROGRAM = {"name": "Присед (цикл)", "weeks": 4, "days": [
    (1, 1, "Неделя 1 · Присед", [("Присед", 5, 5, 75, "percent")]),
    (1, 2, "Неделя 1 · Присед лёгкий", [("Присед", 4, 5, 65, "percent")]),
    (2, 1, "Неделя 2 · Присед", [("Присед", 5, 4, 80, "percent")]),
    (2, 2, "Неделя 2 · Присед лёгкий", [("Присед", 4, 5, 70, "percent")]),
    (3, 1, "Неделя 3 · Присед", [("Присед", 4, 3, 85, "percent")]),
    (3, 2, "Неделя 3 · Присед лёгкий", [("Присед", 4, 4, 70, "percent")]),
    (4, 1, "Неделя 4 · Проходка", [("Присед", 1, 1, 95, "percent")]),
    (4, 2, "Неделя 4 · Восстановление", [("Присед", 3, 5, 60, "percent")]),
]}

DEADLIFT_PROGRAM = {"name": "Становая (цикл)", "weeks": 4, "days": [
    (1, 1, "Неделя 1 · Тяга", [("Становая", 5, 5, 75, "percent")]),
    (1, 2, "Неделя 1 · Тяга лёгкая", [("Становая", 3, 5, 65, "percent")]),
    (2, 1, "Неделя 2 · Тяга", [("Становая", 5, 4, 80, "percent")]),
    (2, 2, "Неделя 2 · Тяга лёгкая", [("Становая", 3, 5, 70, "percent")]),
    (3, 1, "Неделя 3 · Тяга", [("Становая", 4, 3, 85, "percent")]),
    (3, 2, "Неделя 3 · Тяга лёгкая", [("Становая", 3, 4, 70, "percent")]),
    (4, 1, "Неделя 4 · Проходка", [("Становая", 1, 1, 95, "percent")]),
    (4, 2, "Неделя 4 · Восстановление", [("Становая", 3, 5, 60, "percent")]),
]}

BENCH_ACCESSORIES = {
    "heavy": [("Пуловер с гантелью", 3, 12), ("Разводка гантелями", 3, 12)],
    "medium": [("Жим гантелей под 45°", 3, 10), ("Разводка гантелями", 3, 12)],
    "light": [("Жим гантелей под 45°", 4, 8), ("Жим на наклонной", 4, 8),
              ("Отжимания на брусьях (с весом)", 3, 8)],
}

EXERCISE_LIBRARY = [
    ("Жим лёжа", "percent"), ("Присед", "percent"), ("Становая", "percent"),
    ("Отжимания от пола", "bodyweight"), ("Отжимания на брусьях", "bodyweight"),
    ("Отжимания на брусьях (с весом)", "absolute"),
    ("Подтягивания", "bodyweight"), ("Подтягивания (с весом)", "absolute"),
    ("Пуловер с гантелью", "absolute"), ("Жим гантелей под 45°", "absolute"),
    ("Жим на наклонной", "absolute"), ("Разводка гантелями", "absolute"),
]


# ─────────────────────── СОСТОЯНИЯ ───────────────────────
class ProgramCreate(StatesGroup):
    name = State(); day_name = State(); exercise_name = State()
    exercise_sets = State(); exercise_reps = State(); exercise_weight = State()

class DiaryLog(StatesGroup):
    exercise = State(); weight = State(); sets = State(); reps = State(); rpe = State()

class SuppAdd(StatesGroup):
    name = State(); dosage = State(); times = State()

class BodyMetrics(StatesGroup):
    height = State(); weight = State(); fat = State(); muscle = State(); water = State()


# ─────────────────────── ТАЙМЕР ───────────────────────
active_timers = {}

async def _run_timer(bot, user_id, seconds):
    try:
        await asyncio.sleep(seconds)
        await bot.send_message(user_id, "⏰ *Отдых закончен!* Пора на следующий подход 💪",
                               parse_mode="Markdown")
    except asyncio.CancelledError:
        pass
    finally:
        active_timers.pop(user_id, None)

def start_timer(bot, user_id, seconds):
    cancel_timer(user_id)
    active_timers[user_id] = asyncio.create_task(_run_timer(bot, user_id, seconds))

def cancel_timer(user_id):
    task = active_timers.pop(user_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False
# ─────────────────────── КЛАВИАТУРЫ ───────────────────────
def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🏋️ Программы"), KeyboardButton(text="📝 Дневник")],
        [KeyboardButton(text="💊 БАДы"), KeyboardButton(text="⏱️ Таймер отдыха")],
        [KeyboardButton(text="📏 Замеры"), KeyboardButton(text="💧 Вода")],
        [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="👨‍🏫 Кабинет тренера")],
    ], resize_keyboard=True)

def programs_kb(programs):
    rows = [[InlineKeyboardButton(text=p["name"], callback_data=f"prog:{p['id']}")] for p in programs]
    rows.append([InlineKeyboardButton(text="➕ Создать программу", callback_data="prog_new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def weeks_kb(pid, weeks):
    rows, chunk = [], []
    for w in range(1, weeks + 1):
        chunk.append(InlineKeyboardButton(text=f"📅 Нед. {w}", callback_data=f"wk:{pid}:{w}"))
        if len(chunk) == 4:
            rows.append(chunk); chunk = []
    if chunk: rows.append(chunk)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="prog_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def week_days_kb(pid, week, days, cur_day):
    rows = []
    for d in days:
        marker = "▶️" if d["day_order"] == cur_day else "⬜️"
        rows.append([InlineKeyboardButton(text=f"{marker} Д{d['day_order']}: {d['name']}",
                                          callback_data=f"pday:{d['id']}")])
    rows.append([InlineKeyboardButton(text="✅ Завершить неделю",
                                      callback_data=f"finish_week:{pid}:{week}")])
    rows.append([InlineKeyboardButton(text="⬅️ К неделям", callback_data=f"prog:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def day_view_kb(day_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Завершить тренировку", callback_data=f"finish_day:{day_id}")],
        [InlineKeyboardButton(text="💡 Рекомендации", callback_data=f"day_reco:{day_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_week:{day_id}")],
    ])

def day_kb(day_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить упражнение", callback_data=f"ex_new:{day_id}")],
        [InlineKeyboardButton(text="✅ Завершить", callback_data=f"day_done:{day_id}")],
    ])

def supplements_kb(items):
    rows = [[InlineKeyboardButton(text=f"🗑 {s['name']}", callback_data=f"supp_del:{s['id']}")] for s in items]
    rows.append([InlineKeyboardButton(text="➕ Добавить БАД", callback_data="supp_new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def reports_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Последние записи", callback_data="rep_last")],
        [InlineKeyboardButton(text="📈 Прогресс по упражнению", callback_data="rep_progress")],
        [InlineKeyboardButton(text="⚖️ Тоннаж по дням", callback_data="rep_tonnage")],
    ])

def exercises_choice_kb(exs):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=e, callback_data=f"rep_ex:{e}")] for e in exs])

def timer_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1:00", callback_data="timer:60"),
         InlineKeyboardButton(text="1:30", callback_data="timer:90"),
         InlineKeyboardButton(text="2:00", callback_data="timer:120")],
        [InlineKeyboardButton(text="2:30", callback_data="timer:150"),
         InlineKeyboardButton(text="3:00", callback_data="timer:180"),
         InlineKeyboardButton(text="5:00", callback_data="timer:300")],
        [InlineKeyboardButton(text="⏹️ Отменить таймер", callback_data="timer:cancel")],
    ])

def role_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Я атлет", callback_data="role:athlete")],
        [InlineKeyboardButton(text="👨‍🏫 Я тренер", callback_data="role:trainer")],
    ])

def trainer_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Создать код для атлета", callback_data="trainer:invite")],
        [InlineKeyboardButton(text="👥 Мои атлеты", callback_data="trainer:athletes")],
    ])

def body_metrics_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📏 Ввести замеры", callback_data="bm:add")],
        [InlineKeyboardButton(text="📊 Динамика", callback_data="bm:history")],
    ])

def water_kb(enabled, interval):
    status = "🟢 Вкл" if enabled else "🔴 Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{status} напоминания", callback_data="water:toggle")],
        [InlineKeyboardButton(text="1ч", callback_data="water:int:1"),
         InlineKeyboardButton(text="1.5ч", callback_data="water:int:1.5"),
         InlineKeyboardButton(text="2ч", callback_data="water:int:2"),
         InlineKeyboardButton(text="3ч", callback_data="water:int:3")],
    ])

def exercise_lib_kb():
    rows = [[InlineKeyboardButton(text=n, callback_data=f"libex:{n}")] for n, _ in EXERCISE_LIBRARY]
    rows.append([InlineKeyboardButton(text="✏️ Своё название", callback_data="libex:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────── УТИЛИТЫ ───────────────────────
def _epley(weight, reps):
    if reps <= 1: return round(weight, 1)
    return round(weight * (1 + reps / 30), 1)


def _load_program(user_id, data):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO programs (user_id, name, weeks, created_at) VALUES (?, ?, ?, ?)",
        (user_id, data["name"], data["weeks"], datetime.now().isoformat()))
    pid = cur.lastrowid
    for week, day_order, day_name, exercises in data["days"]:
        cur = conn.execute(
            "INSERT INTO program_days (program_id, week, day_order, name) VALUES (?, ?, ?, ?)",
            (pid, week, day_order, day_name))
        day_id = cur.lastrowid
        for name, sets, reps, weight, wtype in exercises:
            conn.execute(
                "INSERT INTO program_exercises (day_id, name, sets, reps, weight, weight_type) "
                "VALUES (?, ?, ?, ?, ?, ?)", (day_id, name, sets, reps, weight, wtype))
    conn.execute(
        "INSERT OR IGNORE INTO user_progress (user_id, program_id, current_week, current_day_order, updated_at) "
        "VALUES (?, ?, 1, 1, ?)", (user_id, pid, datetime.now().isoformat()))
    conn.commit(); conn.close()
    return pid


# ─────────────────────── СТАРТ ───────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Я бот для поверлифтинга 💪\n\n"
        "Быстрый старт:\n"
        "• /setmax Жим 120 — установить 1ПМ\n"
        "• /load_bench /load_squat /load_dead — загрузить программу\n"
        "• /role — выбрать роль тренер/атлет\n\n"
        "Всё остальное — в меню снизу.",
        reply_markup=main_menu())

@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())

@router.message(Command("setmax"))
async def set_max(message: Message):
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Формат: /setmax Жим 120"); return
    try:
        weight = float(parts[2].replace(",", "."))
    except ValueError:
        await message.answer("Вес — число."); return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO user_maxes (user_id, exercise, weight, updated_at) VALUES (?, ?, ?, ?)",
                 (message.from_user.id, parts[1], weight, datetime.now().isoformat()))
    conn.commit(); conn.close()
    await message.answer(f"✅ 1ПМ «{parts[1]}» = {weight} кг")

@router.message(Command("load_bench"))
async def load_bench(message: Message):
    _load_program(message.from_user.id, BENCH_PROGRAM)
    await message.answer("✅ Программа «Жим лёжа (цикл)» загружена.")
    @router.message(Command("load_squat"))
async def load_squat(message: Message):
    _load_program(message.from_user.id, SQUAT_PROGRAM)
    await message.answer("✅ Программа приседа загружена.")

@router.message(Command("load_dead"))
async def load_dead(message: Message):
    _load_program(message.from_user.id, DEADLIFT_PROGRAM)
    await message.answer("✅ Программа становой загружена.")


# ─────────────────────── ПРОГРАММЫ ───────────────────────
@router.message(F.text == "🏋️ Программы")
async def programs_menu(message: Message, state: FSMContext):
    await state.clear()
    conn = get_conn()
    programs = conn.execute("SELECT id, name FROM programs WHERE user_id=?",
                            (message.from_user.id,)).fetchall()
    conn.close()
    await message.answer("Твои программы:", reply_markup=programs_kb(programs))

@router.callback_query(F.data == "prog_back")
async def prog_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    conn = get_conn()
    programs = conn.execute("SELECT id, name FROM programs WHERE user_id=?",
                            (cb.from_user.id,)).fetchall()
    conn.close()
    await cb.message.edit_text("Твои программы:", reply_markup=programs_kb(programs))

@router.callback_query(F.data.startswith("prog:"))
async def prog_open(cb: CallbackQuery):
    pid = int(cb.data.split(":")[1])
    conn = get_conn()
    prog = conn.execute("SELECT name, weeks FROM programs WHERE id=?", (pid,)).fetchone()
    progress = conn.execute("SELECT current_week, current_day_order FROM user_progress "
                            "WHERE user_id=? AND program_id=?", (cb.from_user.id, pid)).fetchone()
    conn.close()
    if progress:
        text = (f"📋 {prog['name']}\n▶️ Текущий: Неделя {progress['current_week']}, "
                f"День {progress['current_day_order']}\n\nВыбери неделю:")
    else:
        text = f"📋 {prog['name']}\nВыбери неделю:"
    await cb.message.edit_text(text, reply_markup=weeks_kb(pid, prog["weeks"] or 4))

@router.callback_query(F.data.startswith("wk:"))
async def week_open(cb: CallbackQuery):
    _, pid, week = cb.data.split(":")
    pid, week = int(pid), int(week)
    conn = get_conn()
    days = conn.execute("SELECT id, day_order, name FROM program_days "
                        "WHERE program_id=? AND week=? ORDER BY day_order", (pid, week)).fetchall()
    progress = conn.execute("SELECT current_day_order FROM user_progress "
                            "WHERE user_id=? AND program_id=?", (cb.from_user.id, pid)).fetchone()
    cur_day = progress["current_day_order"] if progress else 1
    conn.close()
    days_data = [{"id": d["id"], "day_order": d["day_order"], "name": d["name"]} for d in days]
    await cb.message.edit_text(f"📅 Неделя {week}\n(▶️ — текущий день)",
                               reply_markup=week_days_kb(pid, week, days_data, cur_day))

@router.callback_query(F.data.startswith("pday:"))
async def pday_open(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    await _show_day(cb.message, cb.from_user.id, day_id, edit=True)
    await cb.answer()

@router.callback_query(F.data.startswith("back_week:"))
async def back_week(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    conn = get_conn()
    row = conn.execute("SELECT program_id, week FROM program_days WHERE id=?", (day_id,)).fetchone()
    conn.close()
    cb.data = f"wk:{row['program_id']}:{row['week']}"
    await week_open(cb)

async def _show_day(message, user_id, day_id, edit=False):
    conn = get_conn()
    day = conn.execute("SELECT name FROM program_days WHERE id=?", (day_id,)).fetchone()
    exs = conn.execute("SELECT name, sets, reps, weight, weight_type FROM program_exercises "
                       "WHERE day_id=?", (day_id,)).fetchall()
    maxes = {r["exercise"]: r["weight"] for r in conn.execute(
        "SELECT exercise, weight FROM user_maxes WHERE user_id=?", (user_id,)).fetchall()}
    conn.close()
    text = f"📅 {day['name']}\n\n"
    tonnage_day = 0
    for e in exs:
        if e["weight_type"] == "percent":
            base = maxes.get(e["name"])
            if base:
                w = round(base * e["weight"] / 100, 1)
                text += f"• {e['name']} — {e['sets']}×{e['reps']} @ {w} кг ({e['weight']}%)\n"
                tonnage_day += w * e["sets"] * e["reps"]
            else:
                text += f"• {e['name']} — {e['sets']}×{e['reps']} ({e['weight']}%) ⚠️ нет 1ПМ\n"
        elif e["weight_type"] == "bodyweight":
            text += f"• {e['name']} — {e['sets']}×{e['reps']} (свой вес)\n"
        else:
            text += f"• {e['name']} — {e['sets']}×{e['reps']} @ {e['weight']} кг\n"
            tonnage_day += (e["weight"] or 0) * e["sets"] * e["reps"]
    if tonnage_day:
        text += f"\n⚖️ Планируемый тоннаж: *{round(tonnage_day)} кг*"
    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=day_view_kb(day_id))
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=day_view_kb(day_id))

@router.callback_query(F.data.startswith("day_reco:"))
async def day_reco(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    conn = get_conn()
    exs = conn.execute("SELECT weight, weight_type FROM program_exercises "
                       "WHERE day_id=? AND name LIKE 'Жим%'", (day_id,)).fetchall()
    conn.close()
    if not exs:
        await cb.answer("Это не день жима.", show_alert=True); return
    max_pct = max((e["weight"] for e in exs if e["weight_type"] == "percent"), default=0)
    if max_pct >= 80:
        key, title = "heavy", "💡 Жим тяжёлый — добиваем грудные лёгкими изоляциями:"
    elif max_pct <= 70:
        key, title = "light", "💡 Жим лёгкий — можно дать объём на грудь:"
    else:
        key, title = "medium", "💡 Средний день — оптимально:"
    text = title + "\n\n" + "\n".join(f"• {n} — {s}×{r}" for n, s, r in BENCH_ACCESSORIES[key])
    await cb.message.answer(text)
    await cb.answer()

@router.callback_query(F.data.startswith("finish_day:"))
async def finish_day(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    user_id = cb.from_user.id
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    day = conn.execute("SELECT program_id, week, day_order, name FROM program_days WHERE id=?",
                       (day_id,)).fetchone()
    row = conn.execute("SELECT COALESCE(SUM(tonnage), 0) AS total FROM diary "
                       "WHERE user_id=? AND date=?", (user_id, today)).fetchone()
    exercises_today = conn.execute("SELECT exercise, weight, sets, reps, tonnage FROM diary "
                                   "WHERE user_id=? AND date=? ORDER BY id", (user_id, today)).fetchall()
    max_day = conn.execute("SELECT MAX(day_order) AS m FROM program_days "
                           "WHERE program_id=? AND week=?",
                           (day["program_id"], day["week"])).fetchone()["m"]
    if day["day_order"] >= max_day:
        new_week, new_day = day["week"] + 1, 1
    else:
        new_week, new_day = day["week"], day["day_order"] + 1
    conn.execute("INSERT INTO user_progress (user_id, program_id, current_week, current_day_order, updated_at) "
                 "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, program_id) DO UPDATE SET "
                 "current_week=excluded.current_week, current_day_order=excluded.current_day_order, "
                 "updated_at=excluded.updated_at",
                 (user_id, day["program_id"], new_week, new_day, datetime.now().isoformat()))
    conn.commit(); conn.close()
    text = f"🏁 Тренировка «{day['name']}» завершена!\n\n"
    if exercises_today:
        text += "📋 Что сделано:\n"
        for e in exercises_today:
            text += f"• {e['exercise']} — {e['weight']} кг × {e['sets']}×{e['reps']} = {round(e['tonnage'])} кг\n"
    text += f"\n⚖️ *Суммарный тоннаж: {round(row['total'])} кг*\n▶️ Следующий: Неделя {new_week}, День {new_day}"
    await cb.message.edit_text(text, parse_mode="Markdown")
    await cb.answer("Готово!")
    @router.callback_query(F.data.startswith("finish_week:"))
async def finish_week(cb: CallbackQuery):
    _, pid, week = cb.data.split(":")
    pid, week = int(pid), int(week)
    conn = get_conn()
    prog = conn.execute("SELECT weeks FROM programs WHERE id=?", (pid,)).fetchone()
    new_week = min(week + 1, prog["weeks"] or 4)
    conn.execute("INSERT INTO user_progress (user_id, program_id, current_week, current_day_order, updated_at) "
                 "VALUES (?, ?, ?, 1, ?) ON CONFLICT(user_id, program_id) DO UPDATE SET "
                 "current_week=excluded.current_week, current_day_order=1, updated_at=excluded.updated_at",
                 (cb.from_user.id, pid, new_week, datetime.now().isoformat()))
    conn.commit(); conn.close()
    await cb.message.edit_text(f"✅ Неделя {week} завершена.\n▶️ Переходим на Неделю {new_week}.")

@router.callback_query(F.data == "prog_new")
async def prog_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(ProgramCreate.name)
    await cb.message.edit_text("Введи название программы:")

@router.message(ProgramCreate.name)
async def prog_create_name(message: Message, state: FSMContext):
    conn = get_conn()
    cur = conn.execute("INSERT INTO programs (user_id, name, weeks, created_at) VALUES (?, ?, 4, ?)",
                       (message.from_user.id, message.text.strip(), datetime.now().isoformat()))
    pid = cur.lastrowid
    conn.execute("INSERT INTO user_progress (user_id, program_id, current_week, current_day_order, updated_at) "
                 "VALUES (?, ?, 1, 1, ?)", (message.from_user.id, pid, datetime.now().isoformat()))
    conn.commit(); conn.close()
    await state.update_data(program_id=pid, week=1, day_order=1)
    await state.set_state(ProgramCreate.day_name)
    await message.answer("Программа создана. Введи название первого дня:")

@router.message(ProgramCreate.day_name)
async def prog_day_name(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = get_conn()
    cur = conn.execute("INSERT INTO program_days (program_id, week, day_order, name) VALUES (?, ?, ?, ?)",
                       (data["program_id"], data["week"], data["day_order"], message.text.strip()))
    day_id = cur.lastrowid
    conn.commit(); conn.close()
    await state.update_data(day_id=day_id)
    await state.set_state(ProgramCreate.exercise_name)
    await message.answer("День добавлен.", reply_markup=day_kb(day_id))

@router.callback_query(F.data.startswith("ex_new:"))
async def ex_new(cb: CallbackQuery, state: FSMContext):
    day_id = int(cb.data.split(":")[1])
    await state.update_data(day_id=day_id)
    await state.set_state(ProgramCreate.exercise_name)
    await cb.message.edit_text("Выбери упражнение:", reply_markup=exercise_lib_kb())

@router.callback_query(ProgramCreate.exercise_name, F.data.startswith("libex:"))
async def ex_lib(cb: CallbackQuery, state: FSMContext):
    name = cb.data.split(":", 1)[1]
    if name == "custom":
        await cb.message.edit_text("Введи название упражнения:"); return
    wtype = dict(EXERCISE_LIBRARY).get(name, "absolute")
    await state.update_data(ex_name=name, ex_wtype=wtype)
    await state.set_state(ProgramCreate.exercise_sets)
    await cb.message.edit_text(f"{name}. Сколько подходов?")

@router.message(ProgramCreate.exercise_name)
async def ex_name(message: Message, state: FSMContext):
    await state.update_data(ex_name=message.text.strip(), ex_wtype="absolute")
    await state.set_state(ProgramCreate.exercise_sets)
    await message.answer("Сколько подходов?")

@router.message(ProgramCreate.exercise_sets)
async def ex_sets(message: Message, state: FSMContext):
    try: sets = int(message.text)
    except ValueError:
        await message.answer("Число."); return
    await state.update_data(ex_sets=sets)
    await state.set_state(ProgramCreate.exercise_reps)
    await message.answer("Сколько повторений?")

@router.message(ProgramCreate.exercise_reps)
async def ex_reps(message: Message, state: FSMContext):
    try: reps = int(message.text)
    except ValueError:
        await message.answer("Число."); return
    await state.update_data(ex_reps=reps)
    await state.set_state(ProgramCreate.exercise_weight)
    await message.answer("Вес в кг или % (0 — без веса):")

@router.message(ProgramCreate.exercise_weight)
async def ex_weight(message: Message, state: FSMContext):
    try: weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Число."); return
    data = await state.get_data()
    conn = get_conn()
    conn.execute("INSERT INTO program_exercises (day_id, name, sets, reps, weight, weight_type) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (data["day_id"], data["ex_name"], data["ex_sets"], data["ex_reps"],
                  weight, data.get("ex_wtype", "absolute")))
    conn.commit(); conn.close()
    await state.set_state(ProgramCreate.exercise_name)
    await message.answer(f"✅ {data['ex_name']} {data['ex_sets']}×{data['ex_reps']} @ {weight}",
                         reply_markup=day_kb(data["day_id"]))

@router.callback_query(F.data.startswith("day_done:"))
async def day_done(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    conn = get_conn()
    programs = conn.execute("SELECT id, name FROM programs WHERE user_id=?",
                            (cb.from_user.id,)).fetchall()
    conn.close()
    await cb.message.edit_text("День сохранён.")
    await cb.message.answer("Твои программы:", reply_markup=programs_kb(programs))


# ─────────────────────── ДНЕВНИК ───────────────────────
@router.message(F.text == "📝 Дневник")
async def diary_menu(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(DiaryLog.exercise)
    await message.answer("Введи упражнение (например, «Жим лёжа»):\n/cancel — отмена.")

@router.message(DiaryLog.exercise)
async def diary_ex(message: Message, state: FSMContext):
    await state.update_data(exercise=message.text.strip())
    await state.set_state(DiaryLog.weight)
    await message.answer("Рабочий вес (кг):")

@router.message(DiaryLog.weight)
async def diary_w(message: Message, state: FSMContext):
    try: w = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Число."); return
    await state.update_data(weight=w)
    await state.set_state(DiaryLog.sets)
    await message.answer("Подходов:")

@router.message(DiaryLog.sets)
async def diary_s(message: Message, state: FSMContext):
    try: s = int(message.text)
    except ValueError:
        await message.answer("Число."); return
    await state.update_data(sets=s)
    await state.set_state(DiaryLog.reps)
    await message.answer("Повторений:")

@router.message(DiaryLog.reps)
async def diary_r(message: Message, state: FSMContext):
    try: r = int(message.text)
    except ValueError:
        await message.answer("Число."); return
    await state.update_data(reps=r)
    await state.set_state(DiaryLog.rpe)
    await message.answer("RPE (1–10) или 0:")

@router.message(DiaryLog.rpe)
async def diary_rpe(message: Message, state: FSMContext):
    try: rpe = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Число."); return
    data = await state.get_data()
    weight, sets, reps = data["weight"], data["sets"], data["reps"]
    tonnage = weight * sets * reps
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    conn.execute("INSERT INTO diary (user_id, date, session_id, exercise, weight, sets, reps, rpe, tonnage) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (message.from_user.id, today, str(uuid.uuid4()), data["exercise"],
                  weight, sets, reps, rpe, tonnage))
    conn.commit()
    total_today = conn.execute("SELECT COALESCE(SUM(tonnage),0) AS s FROM diary "
                               "WHERE user_id=? AND date=?", (message.from_user.id, today)).fetchone()["s"]
    conn.close()
    await state.clear()
    await message.answer(f"✅ Записано.\n⚖️ Тоннаж упражнения: *{round(tonnage)} кг*\n"
                         f"📊 Тоннаж за сегодня: *{round(total_today)} кг*",
                         parse_mode="Markdown", reply_markup=main_menu())
    e1rm = _epley(weight, reps)
    if reps >= 3 and weight > 0:
        conn = get_conn()
        cur_max = conn.execute("SELECT weight FROM user_maxes WHERE user_id=? AND exercise=?",
                               (message.from_user.id, data["exercise"])).fetchone()
        conn.close()
        cur_val = cur_max["weight"] if cur_max else 0
        if e1rm > cur_val * 1.02:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"✅ Обновить 1ПМ до {e1rm} кг",
                                     callback_data=f"upd_max:{data['exercise']}:{e1rm}"),
                InlineKeyboardButton(text="❌ Оставить", callback_data="upd_max:no:0")]])
            await message.answer(f"🧮 По Эпли 1ПМ в «{data['exercise']}» ≈ *{e1rm} кг*.\n"
                                 f"Сейчас: {cur_val or 'не задан'} кг.",
                                 parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("upd_max:"))
async def upd_max(cb: CallbackQuery):
    _, ex, val = cb.data.split(":")
    if ex == "no":
        await cb.message.edit_text("Ок, 1ПМ не изменён."); return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO user_maxes (user_id, exercise, weight, updated_at) VALUES (?, ?, ?, ?)",
                 (cb.from_user.id, ex, float(val), datetime.now().isoformat()))
    conn.commit(); conn.close()
    await cb.message.edit_text(f"✅ 1ПМ «{ex}» = {val} кг.")


# ─────────────────────── БАДЫ ───────────────────────
@router.message(F.text == "💊 БАДы")
async def supp_menu(message: Message, state: FSMContext):
    await state.clear()
    conn = get_conn()
    items = conn.execute("SELECT id, name, dosage, times FROM supplements WHERE user_id=?",
                         (message.from_user.id,)).fetchall()
    conn.close()
    text = "Твои БАДы:\n"
    if items:
        for s in items:
            text += f"• {s['name']} — {s['dosage'] or '—'} в {s['times']}\n"
    else:
        text += "Пока пусто."
    await message.answer(text, reply_markup=supplements_kb(items))

@router.callback_query(F.data == "supp_new")
async def supp_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SuppAdd.name)
    await cb.message.edit_text("Название БАДа:")

@router.message(SuppAdd.name)
async def supp_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(SuppAdd.dosage)
    await message.answer("Дозировка (например, «5 г»):")

@router.message(SuppAdd.dosage)
async def supp_dosage(message: Message, state: FSMContext):
    await state.update_data(dosage=message.text.strip())
    await state.set_state(SuppAdd.times)
    await message.answer("Время через запятую (08:00,20:00):")

@router.message(SuppAdd.times)
async def supp_times(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = get_conn()
    conn.execute("INSERT INTO supplements (user_id, name, dosage, times) VALUES (?, ?, ?, ?)",
                 (message.from_user.id, data["name"], data["dosage"], message.text.strip()))
    conn.commit()
    items = conn.execute("SELECT id, name, dosage, times FROM supplements WHERE user_id=?",
                         (message.from_user.id,)).fetchall()
    conn.close()
    await state.clear()
    await message.answer("✅ Добавлено.", reply_markup=main_menu())
    await message.answer("Твои БАДы:", reply_markup=supplements_kb(items))

@router.callback_query(F.data.startswith("supp_del:"))
async def supp_del(cb: CallbackQuery):
    sid = int(cb.data.split(":")[1])
    conn = get_conn()
    conn.execute("DELETE FROM supplements WHERE id=? AND user_id=?", (sid, cb.from_user.id))
    conn.commit()
    items = conn.execute("SELECT id, name, dosage, times FROM supplements WHERE user_id=?",
                         (cb.from_user.id,)).fetchall()
    conn.close()
    await cb.message.edit_text("Удалено.", reply_markup=supplements_kb(items))


# ─────────────────────── ТАЙМЕР ───────────────────────
@router.message(F.text == "⏱️ Таймер отдыха")
async def timer_menu(message: Message):
    await message.answer("⏱️ Выбери длительность:", reply_markup=timer_kb())

@router.callback_query(F.data.startswith("timer:"))
async def timer_cb(cb: CallbackQuery):
    value = cb.data.split(":")[1]
    if value == "cancel":
        ok = cancel_timer(cb.from_user.id)
        await cb.message.edit_text("⏹️ Отменён." if ok else "Нет активного таймера."); return
    seconds = int(value)
    start_timer(cb.bot, cb.from_user.id, seconds)
    m, s = divmod(seconds, 60)
    await cb.message.edit_text(f"⏱️ Таймер на *{m}:{s:02d}*.", parse_mode="Markdown")


# ─────────────────────── ЗАМЕРЫ ───────────────────────
@router.message(F.text == "📏 Замеры")
async def bm_menu(message: Message):
    await message.answer("📏 Замеры тела:", reply_markup=body_metrics_kb())

@router.callback_query(F.data == "bm:add")
async def bm_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(BodyMetrics.height)
    await cb.message.edit_text("Рост в см:")

@router.message(BodyMetrics.height)
async def bm_h(message: Message, state: FSMContext):
    try: await state.update_data(height=float(message.text.replace(",", ".")))
    except ValueError:
        await message.answer("Число."); return
    await state.set_state(BodyMetrics.weight)
    await message.answer("Вес в кг:")

@router.message(BodyMetrics.weight)
async def bm_w(message: Message, state: FSMContext):
    try: await state.update_data(weight=float(message.text.replace(",", ".")))
    except ValueError:
        await message.answer("Число."); return
    await state.set_state(BodyMetrics.fat)
    await message.answer("% жира (0, если неизвестно):")

@router.message(BodyMetrics.fat)
async def bm_f(message: Message, state: FSMContext):
    try: await state.update_data(fat=float(message.text.replace(",", ".")))
    except ValueError:
        await message.answer("Число."); return
    await state.set_state(BodyMetrics.muscle)
    await message.answer("% мышц (0):")

@router.message(BodyMetrics.muscle)
async def bm_m(message: Message, state: FSMContext):
    try: await state.update_data(muscle=float(message.text.replace(",", ".")))
    except ValueError:
        await message.answer("Число."); return
    await state.set_state(BodyMetrics.water)
    await message.answer("% воды (0):")

@router.message(BodyMetrics.water)
async def bm_water(message: Message, state: FSMContext):
    try: water = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Число."); return
    data = await state.get_data()
    conn = get_conn()
    conn.execute("INSERT INTO body_metrics (user_id, date, height, weight, fat_pct, muscle_pct, water_pct) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (message.from_user.id, datetime.now().strftime("%Y-%m-%d"),
                  data["height"], data["weight"], data["fat"], data["muscle"], water))
    conn.commit(); conn.close()
    await state.clear()
    h_m = data["height"] / 100
    bmi = round(data["weight"] / (h_m ** 2), 1)
    status = "недостаток" if bmi < 18.5 else "норма" if bmi < 25 else "избыток" if bmi < 30 else "ожирение"
    await message.answer(f"✅ Сохранено.\n📊 ИМТ: *{bmi}* ({status})\n"
                         f"💪 Жир: {data['fat']}% · Мышцы: {data['muscle']}% · Вода: {water}%",
                         parse_mode="Markdown", reply_markup=main_menu())

@router.callback_query(F.data == "bm:history")
async def bm_history(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT date, height, weight, fat_pct, muscle_pct, water_pct "
                        "FROM body_metrics WHERE user_id=? ORDER BY id DESC LIMIT 10",
                        (cb.from_user.id,)).fetchall()
    conn.close()
    if not rows:
        await cb.message.edit_text("Пока нет замеров."); return
    lines = []
    for r in rows:
        h_m = (r["height"] or 0) / 100
        bmi = round(r["weight"] / (h_m ** 2), 1) if h_m else "—"
        lines.append(f"{r['date']}: {r['weight']} кг, ИМТ {bmi}, жир {r['fat_pct']}%, "
                     f"мышцы {r['muscle_pct']}%, вода {r['water_pct']}%")
    await cb.message.edit_text("📊 Последние замеры:\n\n" + "\n".join(lines))


# ─────────────────────── ВОДА ───────────────────────
@router.message(F.text == "💧 Вода")
async def water_menu(message: Message):
    conn = get_conn()
    row = conn.execute("SELECT enabled, interval_hours FROM water_settings WHERE user_id=?",
                       (message.from_user.id,)).fetchone()
    conn.close()
    enabled = row["enabled"] if row else 0
    interval = row["interval_hours"] if row else 2
    await message.answer(f"💧 Напоминания о воде.\n"
                         f"Сейчас: {'🟢 вкл' if enabled else '🔴 выкл'}, интервал {interval} ч.",
                         reply_markup=water_kb(enabled, interval))

@router.callback_query(F.data == "water:toggle")
async def water_toggle(cb: CallbackQuery):
    conn = get_conn()
    row = conn.execute("SELECT enabled, interval_hours FROM water_settings WHERE user_id=?",
                       (cb.from_user.id,)).fetchone()
    if row:
        new = 0 if row["enabled"] else 1
        conn.execute("UPDATE water_settings SET enabled=? WHERE user_id=?", (new, cb.from_user.id))
    else:
        conn.execute("INSERT INTO water_settings (user_id, enabled, interval_hours) VALUES (?, 1, 2)",
                     (cb.from_user.id,))
    conn.commit()
    row = conn.execute("SELECT enabled, interval_hours FROM water_settings WHERE user_id=?",
                       (cb.from_user.id,)).fetchone()
    conn.close()
    await cb.message.edit_reply_markup(reply_markup=water_kb(row["enabled"], row["interval_hours"]))

@router.callback_query(F.data.startswith("water:int:"))
async def water_int(cb: CallbackQuery):
    val = float(cb.data.split(":")[2])
    conn = get_conn()
    conn.execute("INSERT INTO water_settings (user_id, enabled, interval_hours) VALUES (?, 1, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET interval_hours=excluded.interval_hours",
                 (cb.from_user.id, val))
    conn.commit()
    row = conn.execute("SELECT enabled, interval_hours FROM water_settings WHERE user_id=?",
                       (cb.from_user.id,)).fetchone()
    conn.close()
    await cb.message.edit_reply_markup(reply_markup=water_kb(row["enabled"], row["interval_hours"]))


# ─────────────────────── ОТЧЁТЫ ───────────────────────
@router.message(F.text == "📊 Отчёты")
async def reports(message: Message):
    await message.answer("Что показать?", reply_markup=reports_kb())

@router.callback_query(F.data == "rep_last")
async def rep_last(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT date, exercise, weight, sets, reps, rpe, tonnage FROM diary "
                        "WHERE user_id=? ORDER BY id DESC LIMIT 15", (cb.from_user.id,)).fetchall()
    conn.close()
    if not rows:
        await cb.message.edit_text("Пока нет записей."); return
    text = "📜 Последние записи:\n\n" + "\n".join(
        f"{r['date']} — {r['exercise']} {r['weight']}кг {r['sets']}×{r['reps']} "
        f"RPE {r['rpe']} ⚖️ {round(r['tonnage'] or 0)}" for r in rows)
    await cb.message.edit_text(text)

@router.callback_query(F.data == "rep_progress")
async def rep_progress(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT exercise FROM diary WHERE user_id=? ORDER BY exercise",
                        (cb.from_user.id,)).fetchall()
    conn.close()
    if not rows:
        await cb.message.edit_text("Нет данных."); return
    await cb.message.edit_text("Выбери упражнение:",
                               reply_markup=exercises_choice_kb([r["exercise"] for r in rows]))

@router.callback_query(F.data.startswith("rep_ex:"))
async def rep_ex(cb: CallbackQuery):
    ex = cb.data.split(":", 1)[1]
    conn = get_conn()
    rows = conn.execute("SELECT date, MAX(weight) AS w FROM diary WHERE user_id=? AND exercise=? "
                        "GROUP BY date ORDER BY date", (cb.from_user.id, ex)).fetchall()
    conn.close()
    if not rows:
        await cb.message.edit_text("Нет данных."); return
    text = f"📈 Прогресс: {ex}\n\n"
    for r in rows:
        text += f"{r['date']} — {r['w']} кг\n"
    text += f"\nВсего записей: {len(rows)}"
    await cb.message.edit_text(text)
    await cb.answer()

@router.callback_query(F.data == "rep_tonnage")
async def rep_tonnage(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT date, SUM(tonnage) AS t FROM diary WHERE user_id=? "
                        "GROUP BY date ORDER BY date DESC LIMIT 15", (cb.from_user.id,)).fetchall()
    conn.close()
    if not rows:
        await cb.message.edit_text("Нет данных."); return
    text = "⚖️ Тоннаж по дням:\n\n"
    for r in rows:
        text += f"{r['date']} — {round(r['t'] or 0)} кг\n"
    await cb.message.edit_text(text)
    await cb.answer()


# ─────────────────────── РОЛИ ───────────────────────
@router.message(Command("role"))
async def role_cmd(message: Message):
    await message.answer("Кто ты?", reply_markup=role_kb())

@router.callback_query(F.data.startswith("role:"))
async def role_set(cb: CallbackQuery):
    role = cb.data.split(":")[1]
    conn = get_conn()
    conn.execute("UPDATE users SET role=? WHERE user_id=?", (role, cb.from_user.id))
    conn.commit(); conn.close()
    if role == "trainer":
        await cb.message.edit_text("👨‍🏫 Ты тренер.")
    else:
        await cb.message.edit_text("🏋️ Ты атлет. Есть код? /link КОД", parse_mode="Markdown")
    await cb.message.answer("Меню:", reply_markup=main_menu())

@router.message(F.text == "👨‍🏫 Кабинет тренера")
async def trainer_room(message: Message):
    conn = get_conn()
    row = conn.execute("SELECT role FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
    conn.close()
    if not row or row["role"] != "trainer":
        await message.answer("Сначала /role → «Я тренер»."); return
    await message.answer("👨‍🏫 Кабинет тренера:", reply_markup=trainer_menu_kb())

@router.callback_query(F.data == "trainer:invite")
async def trainer_invite(cb: CallbackQuery):
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    conn = get_conn()
    conn.execute("INSERT INTO invite_codes (code, trainer_id, created_at) VALUES (?, ?, ?)",
                 (code, cb.from_user.id, datetime.now().isoformat()))
    conn.commit(); conn.close()
    await cb.message.edit_text(f"🎫 Код:\n\n{code}\n\nАтлет вводит /link {code}",
                               parse_mode="Markdown")

@router.message(Command("link"))
async def link_code(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Формат: /link КОД"); return
    code = parts[1].strip().upper()
    conn = get_conn()
    row = conn.execute("SELECT trainer_id FROM invite_codes WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close(); await message.answer("❌ Код не найден."); return
    conn.execute("INSERT OR IGNORE INTO trainer_links (trainer_id, athlete_id) VALUES (?, ?)",
                 (row["trainer_id"], message.from_user.id))
    conn.commit(); conn.close()
    await message.answer("✅ Привязан к тренеру.")
    try:
        await message.bot.send_message(row["trainer_id"],
                                       f"👤 Новый атлет: @{message.from_user.username or message.from_user.id}")
    except Exception:
        pass

@router.callback_query(F.data == "trainer:athletes")
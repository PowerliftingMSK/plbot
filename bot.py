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
    Message, CallbackQuery, BufferedInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_DIR = os.getenv("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "powerbot.db")

logging.basicConfig(level=logging.INFO)
router = Router()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


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
    for alter in [
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'athlete'",
        "ALTER TABLE programs ADD COLUMN weeks INTEGER DEFAULT 4",
        "ALTER TABLE programs ADD COLUMN created_at TEXT",
        "ALTER TABLE diary ADD COLUMN session_id TEXT",
        "ALTER TABLE diary ADD COLUMN tonnage REAL DEFAULT 0",
        "ALTER TABLE program_exercises ADD COLUMN weight_type TEXT DEFAULT 'percent'",
    ]:
        try:
            conn.execute(alter)
        except Exception:
            pass
    conn.commit()
    conn.close()


def ensure_user(user_id, username=None):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                 (user_id, username))
    conn.commit()
    conn.close()


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


class ProgramCreate(StatesGroup):
    name = State(); day_name = State(); exercise_name = State()
    exercise_sets = State(); exercise_reps = State(); exercise_weight = State()

class DiaryLog(StatesGroup):
    exercise = State(); weight = State(); sets = State(); reps = State(); rpe = State()

class SuppAdd(StatesGroup):
    name = State(); dosage = State(); times = State()

class BodyMetrics(StatesGroup):
    height = State(); weight = State(); fat = State(); muscle = State(); water = State()


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
import asyncio, logging, os, random, sqlite3, string, time, uuid, json
from urllib.parse import quote
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_DIR = os.getenv("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "powerbot.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) 
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
    CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'athlete', reg_id TEXT, city TEXT, gym TEXT, phone TEXT, is_verified INTEGER DEFAULT 0, max_bench REAL DEFAULT 0, max_squat REAL DEFAULT 0, max_dead REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS programs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, weeks INTEGER DEFAULT 4, created_at TEXT);
    CREATE TABLE IF NOT EXISTS program_days (id INTEGER PRIMARY KEY AUTOINCREMENT, program_id INTEGER, week INTEGER, day_order INTEGER, name TEXT);
    CREATE TABLE IF NOT EXISTS program_exercises (id INTEGER PRIMARY KEY AUTOINCREMENT, day_id INTEGER, name TEXT, sets INTEGER, reps INTEGER, weight REAL, weight_type TEXT DEFAULT 'percent');
    CREATE TABLE IF NOT EXISTS diary (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, session_id TEXT, exercise TEXT, weight REAL, sets INTEGER, reps INTEGER, tonnage REAL DEFAULT 0, feeling TEXT, spotter_id INTEGER);
    CREATE TABLE IF NOT EXISTS links (user_id INTEGER, target_id INTEGER, type TEXT, PRIMARY KEY (user_id, target_id, type));
    CREATE TABLE IF NOT EXISTS invite_codes (code TEXT PRIMARY KEY, trainer_id INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS inventory (user_id INTEGER PRIMARY KEY, bar_weight REAL DEFAULT 20, collar_weight REAL DEFAULT 2.5, plates TEXT);
    CREATE TABLE IF NOT EXISTS schedule (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, weekday INTEGER, time TEXT, remind_hours INTEGER);
    CREATE TABLE IF NOT EXISTS water_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, amount INTEGER);
    CREATE TABLE IF NOT EXISTS water_settings (user_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0, interval_hours REAL DEFAULT 2);
    CREATE TABLE IF NOT EXISTS supplements (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, dosage TEXT, times TEXT);
    CREATE TABLE IF NOT EXISTS body_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, height REAL, weight REAL, fat_pct REAL, muscle_pct REAL, water_pct REAL);
    CREATE TABLE IF NOT EXISTS sleep_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, hours REAL, quality INTEGER);
    CREATE TABLE IF NOT EXISTS achivements (user_id INTEGER, name TEXT, date TEXT, PRIMARY KEY (user_id, name));
    CREATE TABLE IF NOT EXISTS sent_log (key TEXT PRIMARY KEY);
    """)
    conn.commit(); conn.close()

def ensure_user(user_id, username=None):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit(); conn.close()

def assign_id(user_id, role="athlete", is_family=False):
    conn = get_conn()
    row = conn.execute("SELECT reg_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row and row["reg_id"]: conn.close(); return row["reg_id"]
    if is_family:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role='family'").fetchone()[0]
        reg_id = f"❤️{count+1}"
    elif role == "trainer":
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role='trainer'").fetchone()[0]
        reg_id = f"🥷{count+1}"
    else:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role='athlete'").fetchone()[0]
        reg_id = str(count+1)
    conn.execute("UPDATE users SET reg_id=? WHERE user_id=?", (reg_id, user_id))
    conn.commit(); conn.close()
    return reg_id

def format_id(reg_id):
    is_premium = False
    if reg_id and reg_id.isdigit():
        num = int(reg_id)
        if num <= 100 or len(set(reg_id)) == 1: is_premium = True
    if is_premium: return f"<tg-spoiler>#{reg_id}</tg-spoiler> ⭐"
    return f"#{reg_id}" if reg_id else ""

BENCH_PROGRAM = {"name": "Жим лёжа (4 недели)", "weeks": 4, "days": [
    (1, 1, "Неделя 1 · День 1", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 5, 3, 80, "percent"), ("Жим лёжа (2)", 5, 5, 70, "percent")]),
    (1, 2, "Неделя 1 · День 2", [("Жим лёжа (1)", 1, 5, 55, "percent"), ("Жим лёжа (1)", 4, 5, 70, "percent")]),
    (2, 1, "Неделя 2 · День 1", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 5, 4, 75, "percent"), ("Жим лёжа (2)", 3, 2, 85, "percent")]),
    (2, 2, "Неделя 2 · День 2", [("Жим лёжа (1)", 1, 5, 50, "percent"), ("Жим лёжа (1)", 4, 5, 70, "percent")]),
    (2, 3, "Неделя 2 · День 3", [("Жим лёжа (1)", 2, 1, 90, "percent"), ("Жим лёжа (2)", 4, 4, 70, "percent")]),
    (3, 1, "Неделя 3 · День 1", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 3, 3, 80, "percent"), ("Жим лёжа (2)", 5, 4, 75, "percent")]),
    (3, 2, "Неделя 3 · День 2", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 5, 4, 80, "percent")]),
    (4, 1, "Неделя 4 · День 1", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 2, 2, 85, "percent"), ("Жим лёжа (2)", 2, 2, 90, "percent")]),
    (4, 2, "Неделя 4 · День 2", [("Жим лёжа (1)", 1, 4, 55, "percent"), ("Жим лёжа (1)", 4, 4, 75, "percent")]),
    (4, 3, "Неделя 4 · День 3", [("Жим лёжа (1)", 1, 8, 50, "percent"), ("Жим лёжа (1)", 5, 2, 85, "percent")])
]}

SQUAT_PROGRAM = {"name": "Присед (4 недели)", "weeks": 4, "days": [
    (1, 1, "Неделя 1 · Присед (Тяжелый)", [("Присед", 5, 5, 75, "percent")]),
    (1, 2, "Неделя 1 · Присед (Объемный)", [("Присед", 4, 12, 65, "percent")]),
    (2, 1, "Неделя 2 · Присед (Тяжелый)", [("Присед", 5, 4, 80, "percent")]),
    (2, 2, "Неделя 2 · Присед (Объемный)", [("Присед", 4, 10, 68, "percent")]),
    (3, 1, "Неделя 3 · Присед (Тяжелый)", [("Присед", 4, 3, 85, "percent")]),
    (3, 2, "Неделя 3 · Присед (Объемный)", [("Присед", 4, 10, 70, "percent")]),
    (4, 1, "Неделя 4 · Присед (Тяжелый)", [("Присед", 3, 2, 90, "percent")]),
    (4, 2, "Неделя 4 · Присед (Объемный)", [("Присед", 4, 8, 65, "percent")]),
]}

DEADLIFT_PROGRAM = {"name": "Становая (Цикл 18 трен.)", "weeks": 9, "days": [
    (1, 1, "Тренировка 1 (Нечетная)", [("Становая", 5, 5, 70, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (1, 2, "Тренировка 2 (Четная)", [("Становая", 3, 1, 81, "percent"), ("Становая", 2, 1, 85, "percent"), ("Становая", 1, 3, 88.5, "percent")]),
    (2, 1, "Тренировка 3 (Нечетная)", [("Становая", 5, 5, 72.5, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (2, 2, "Тренировка 4 (Четная)", [("Становая", 3, 1, 81, "percent"), ("Становая", 2, 1, 85, "percent"), ("Становая", 2, 3, 88.5, "percent")]),
    (3, 1, "Тренировка 5 (Нечетная)", [("Становая", 5, 5, 75, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (3, 2, "Тренировка 6 (Четная)", [("Становая", 3, 1, 82.5, "percent"), ("Становая", 2, 1, 87.5, "percent"), ("Становая", 1, 3, 94, "percent")]),
    (4, 1, "Тренировка 7 (Нечетная)", [("Становая", 4, 4, 77.5, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (4, 2, "Тренировка 8 (Четная)", [("Становая", 3, 1, 82.5, "percent"), ("Становая", 2, 1, 87.5, "percent"), ("Становая", 2, 3, 94, "percent")]),
    (5, 1, "Тренировка 9 (Нечетная)", [("Становая", 4, 4, 80, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (5, 2, "Тренировка 10 (Четная)", [("Становая", 3, 1, 84, "percent"), ("Становая", 2, 1, 94, "percent"), ("Становая", 1, 3, 100, "percent")]),
    (6, 1, "Тренировка 11 (Нечетная)", [("Становая", 4, 4, 82.5, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (6, 2, "Тренировка 12 (Четная)", [("Становая", 3, 1, 84, "percent"), ("Становая", 2, 1, 94, "percent"), ("Становая", 2, 3, 100, "percent")]),
    (7, 1, "Тренировка 13 (Нечетная)", [("Становая", 3, 3, 85, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (7, 2, "Тренировка 14 (Четная)", [("Становая", 3, 1, 85.5, "percent"), ("Становая", 2, 1, 97.5, "percent"), ("Становая", 1, 3, 105, "percent")]),
    (8, 1, "Тренировка 15 (Нечетная)", [("Становая", 3, 3, 87.5, "percent"), ("Тяга блока", 3, 10, 0, "absolute")]),
    (8, 2, "Тренировка 16 (Четная)", [("Становая", 3, 1, 85.5, "percent"), ("Становая", 2, 1, 97.5, "percent"), ("Становая", 2, 3, 105, "percent")]),
    (9, 1, "Тренировка 17 (Нечетная)", [("Становая", 2, 2, 90, "percent")]),
    (9, 2, "Тренировка 18 (Проходка)", [("Становая", 1, 1, 100, "percent")]),
]}

def _load_program(user_id, data):
    conn = get_conn()
    cur = conn.execute("INSERT INTO programs (user_id, name, weeks, created_at) VALUES (?, ?, ?, ?)", (user_id, data["name"], data["weeks"], datetime.now().isoformat()))
    pid = cur.lastrowid
    for week, day_order, day_name, exercises in data["days"]:
        cur = conn.execute("INSERT INTO program_days (program_id, week, day_order, name) VALUES (?, ?, ?, ?)", (pid, week, day_order, day_name))
        day_id = cur.lastrowid
        for name, sets, reps, weight, wtype in exercises:
            conn.execute("INSERT INTO program_exercises (day_id, name, sets, reps, weight, weight_type) VALUES (?, ?, ?, ?, ?, ?)", (day_id, name, sets, reps, weight, wtype))
    conn.commit(); conn.close()

def _epley(weight, reps): return round(weight * (1 + reps / 30), 1) if reps > 1 else weight

def get_water_goal(user_id):
    conn = get_conn()
    row = conn.execute("SELECT weight FROM body_metrics WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()
    weight = row["weight"] if row and row["weight"] else 80.0
    return int(weight * 35)

def get_water_today(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute("SELECT SUM(amount) AS s FROM water_log WHERE user_id=? AND date=?", (user_id, today)).fetchone()
    conn.close()
    return int(row["s"]) if row["s"] else 0

class Reg(StatesGroup): city = State(); gym = State(); phone = State()
class DiaryLog(StatesGroup): exercise = State(); weight = State(); sets = State(); reps = State(); feeling = State()
class ScheduleAdd(StatesGroup): weekday = State(); time = State(); remind = State()
class SuppAdd(StatesGroup): name = State(); dosage = State(); times = State()
class SleepLog(StatesGroup): hours = State(); quality = State()
class AdminStates(StatesGroup): give_trainer = State()
class JointTrain(StatesGroup): username = State()

def main_menu(role="athlete"):
    kb = [
        [KeyboardButton(text="🏋️ Программы"), KeyboardButton(text="📝 Дневник")],
        [KeyboardButton(text="🏆 Рейтинг"), KeyboardButton(text="📊 Аналитика")],
        [KeyboardButton(text="👥 Связи"), KeyboardButton(text="📅 Расписание")],
        [KeyboardButton(text="💧 Вода"), KeyboardButton(text="😴 Сон")],
        [KeyboardButton(text="📏 Замеры"), KeyboardButton(text="⚙️ Профиль")]
    ]
    if role == "admin": kb.append([KeyboardButton(text="🛡️ Панель Админа")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def timer_menu_kb(day_id, is_rest=False):
    if is_rest:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 ОТДЫХ 1:30", callback_data=f"timer:90:{day_id}"), InlineKeyboardButton(text="🔴 ОТДЫХ 3:00", callback_data=f"timer:180:{day_id}")],
            [InlineKeyboardButton(text="🟡 ПОДГОТОВКА", callback_data=f"start_prep:{day_id}")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Разминка 10м", callback_data=f"timer:600:{day_id}")],
        [InlineKeyboardButton(text="🏋️ Начать подход", callback_data=f"start_prep:{day_id}")]
    ])

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    ensure_user(message.from_user.id, message.from_user.username)
    conn = get_conn()
    user = conn.execute("SELECT role, city, is_verified FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
    conn.close()
    if user["role"] == "admin":
        await message.answer("Привет, Админ! Всё под контролем.", reply_markup=main_menu("admin")); return
    if not user["city"]:
        await state.set_state(Reg.city)
        await message.answer("Добро пожаловать в Powerlifting MSK! 💪\nВведи свой город:")
        return
    if not user["is_verified"]:
        await message.answer("⚠️ Ты еще не верифицирован. Отправь короткое видео (5-10 сек) с тренировкой.")
        return
    await message.answer("С возвращением!", reply_markup=main_menu(user["role"]))

@router.message(Reg.city)
async def reg_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text); await state.set_state(Reg.gym)
    await message.answer("В каком зале тренируешься?")

@router.message(Reg.gym)
async def reg_gym(message: Message, state: FSMContext):
    await state.update_data(gym=message.text); await state.set_state(Reg.phone)
    await message.answer("Оставь номер телефона (для восстановления):")

@router.message(Reg.phone)
async def reg_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = get_conn()
    conn.execute("UPDATE users SET city=?, gym=?, phone=? WHERE user_id=?", (data["city"], data["gym"], message.text, message.from_user.id))
    conn.commit(); conn.close()
    await state.clear()
    await message.answer("✅ Данные сохранены. Теперь отправь видео-кружок (5-10 сек) с любым подходом.")

@router.message(F.video_note | F.video)
async def handle_verify_video(message: Message, state: FSMContext):
    file_id = message.video_note.file_id if message.video_note else message.video.file_id
    await message.bot.send_video(ADMIN_ID, file_id, caption=f"🛡️ Верификация: @{message.from_user.username}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_vf:{message.from_user.id}:ok"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_vf:{message.from_user.id}:no")
    ]])
    await message.bot.send_message(ADMIN_ID, "Решение:", reply_markup=kb)
    await message.answer("Видео отправлено Админу. Ожидай!")
    await state.clear()

@router.callback_query(F.data.startswith("adm_vf:"))
async def adm_verify(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    _, uid, act = cb.data.split(":")
    uid = int(uid); status = 1 if act == "ok" else 0
    conn = get_conn()
    conn.execute("UPDATE users SET is_verified=? WHERE user_id=?", (status, uid))
    reg_id = assign_id(uid, "athlete")
    conn.commit(); conn.close()
    if act == "ok":
        await cb.message.edit_text("✅ Принято.")
        await cb.bot.send_message(uid, f"🎉 Верифицирован! Твой номер: {format_id(reg_id)}\nМеню: /start", parse_mode="HTML")
    else: await cb.message.edit_text("❌ Отклонено.")

@router.message(F.text == "🏆 Рейтинг")
async def rating_menu(message: Message):
    await message.answer("Выбери рейтинг:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Общий (Все атлеты)", callback_data="rate_global")],
        [InlineKeyboardButton(text="🏢 Мой зал", callback_data="rate_gym")],
        [InlineKeyboardButton(text="🥷 Рейтинг Тренеров", callback_data="rate_trainers")]
    ]))

@router.callback_query(F.data == "rate_global")
async def rate_global(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT reg_id, username, (max_bench + max_squat + max_dead) as total FROM users WHERE role='athlete' AND (max_bench + max_squat + max_dead) > 0 ORDER BY total DESC LIMIT 10").fetchall()
    conn.close()
    text = "🌍 *ТОП-10 Атлетов (Сумма 1ПМ):*\n\n"
    for i, r in enumerate(rows, 1): text += f"{i}. {format_id(r['reg_id'])} (@{r['username']}) — *{r['total']} кг*\n"
    if not rows: text += "Пока никто не установил свои 1ПМ."
    await cb.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data == "rate_gym")
async def rate_gym(cb: CallbackQuery):
    conn = get_conn()
    user = conn.execute("SELECT city, gym FROM users WHERE user_id=?", (cb.from_user.id,)).fetchone()
    if not user or not user["gym"]:
        conn.close(); await cb.answer("Сначала укажи свой зал.", show_alert=True); return
    rows = conn.execute("SELECT reg_id, username, (max_bench + max_squat + max_dead) as total FROM users WHERE role='athlete' AND city=? AND gym=? AND (max_bench + max_squat + max_dead) > 0 ORDER BY total DESC LIMIT 10", (user["city"], user["gym"])).fetchall()
    conn.close()
    text = f"🏢 *ТОП-10 в зале {user['gym']}:*\n\n"
    for i, r in enumerate(rows, 1): text += f"{i}. {format_id(r['reg_id'])} (@{r['username']}) — *{r['total']} кг*\n"
    if not rows: text += "В твоем зале пока нет результатов."
    await cb.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data == "rate_trainers")
async def rate_trainers(cb: CallbackQuery):
    conn = get_conn()
    rows = conn.execute("SELECT u.reg_id, u.username, COUNT(l.user_id) as athletes FROM users u JOIN links l ON u.user_id = l.user_id WHERE u.role='trainer' AND l.type='trainer' GROUP BY u.user_id ORDER BY athletes DESC LIMIT 10").fetchall()
    conn.close()
    text = "🥷 *ТОП-10 Тренеров (По ученикам):*\n\n"
    for i, r in enumerate(rows, 1): text += f"{i}. {format_id(r['reg_id'])} (@{r['username']}) — *{r['athletes']} уч.*\n"
    if not rows: text += "Пока нет данных."
    await cb.message.edit_text(text, parse_mode="HTML")

@router.message(F.text == "🏋️ Программы")
async def programs_menu(message: Message):
    await message.answer("Выбери:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Жим (4 нед)", callback_data="load_bench")],
        [InlineKeyboardButton(text="🦵 Присед (4 нед)", callback_data="load_squat")],
        [InlineKeyboardButton(text="🔥 Становая (18 трен.)", callback_data="load_deadlift")]
    ]))

@router.callback_query(F.data == "load_bench")
async def load_bench_cb(cb: CallbackQuery):
    _load_program(cb.from_user.id, BENCH_PROGRAM)
    await cb.message.edit_text("🔥 *Программа Жим загружена!*", parse_mode="Markdown")

@router.callback_query(F.data == "load_squat")
async def load_squat_cb(cb: CallbackQuery):
    _load_program(cb.from_user.id, SQUAT_PROGRAM)
    await cb.message.edit_text("🦵 *Программа Присед загружена!*", parse_mode="Markdown")

@router.callback_query(F.data == "load_deadlift")
async def load_deadlift_cb(cb: CallbackQuery):
    _load_program(cb.from_user.id, DEADLIFT_PROGRAM)
    await cb.message.edit_text("🔥 *Программа Становая загружена!*", parse_mode="Markdown")

@router.callback_query(F.data == "start_prep:")
async def start_prep(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    await cb.message.edit_text("🟡 *ПОДГОТОВКА* (6 сек)\nПрими исходное положение...", parse_mode="Markdown")
    await asyncio.sleep(6)
    await cb.message.edit_text("🟢 *ПОДХОД! ЖМИ!*", parse_mode="Markdown")
    await asyncio.sleep(3)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сделал", callback_data=f"timer:90:{day_id}")],
        [InlineKeyboardButton(text="❌ Не осилил", callback_data=f"failed_set:{day_id}")]
    ])
    await cb.message.edit_text("Как прошел подход?", reply_markup=kb)

@router.callback_query(F.data.startswith("failed_set:"))
async def failed_set(cb: CallbackQuery):
    day_id = int(cb.data.split(":")[1])
    await cb.message.edit_text("Не страшно! Снижаем вес на 5%.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔴 Начать отдых", callback_data=f"timer:120:{day_id}")]]))

@router.callback_query(F.data.startswith("timer:"))
async def timer_cb(cb: CallbackQuery):
    _, seconds, day_id = cb.data.split(":")
    seconds = int(seconds)
    await cb.message.edit_text(f"🔴 *ОТДЫХ:* {seconds//60}:{seconds%60:02d}\nЯ напишу, когда время выйдет.", parse_mode="Markdown")
    await asyncio.sleep(seconds)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Следующий подход", callback_data=f"start_prep:{day_id}")],
        [InlineKeyboardButton(text="✅ Завершить тренировку", callback_data=f"finish_day:{day_id}")]
    ])
    await cb.message.answer("🔔 *Время вышло!*", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("finish_day:"))
async def finish_day(cb: CallbackQuery):
    conn = get_conn()
    friends = conn.execute("SELECT u.user_id, u.reg_id FROM links l JOIN users u ON l.target_id=u.user_id WHERE l.user_id=? AND l.type IN ('friend', 'trainer')", (cb.from_user.id,)).fetchall()
    conn.close()
    rows = []
    if friends: rows = [[InlineKeyboardButton(text=f"🛡️ {f['reg_id']}", callback_data=f"spotter:{f['user_id']}")] for f in friends]
    rows.append([InlineKeyboardButton(text="🤝 Тренировался с другом", callback_data="joint_train")])
    rows.append([InlineKeyboardButton(text="✍️ Вписать друга вручную", callback_data="joint_manual")])
    rows.append([InlineKeyboardButton(text="🏁 Завершить тренировку", callback_data="spotter:no")])
    await cb.message.edit_text("Кто был с тобой сегодня?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("spotter:"))
async def set_spotter(cb: CallbackQuery):
    target = cb.data.split(":")[1]
    if target != "no":
        tid = int(target)
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO achivements (user_id, name, date) VALUES (?, '🛡️ Надежная страховка', ?)", (tid, datetime.now().isoformat()))
        conn.commit(); conn.close()
        await cb.bot.send_message(tid, "🛡️ Тебе присвоена ачивка «Надежная страховка»!")
    await cb.message.edit_text("🏁 Тренировка завершена!")

@router.callback_query(F.data == "joint_train")
async def joint_train_menu(cb: CallbackQuery):
    conn = get_conn()
    friends = conn.execute("SELECT u.user_id, u.reg_id FROM links l JOIN users u ON l.target_id=u.user_id WHERE l.user_id=? AND l.type='friend'", (cb.from_user.id,)).fetchall()
    conn.close()
    if not friends:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn = get_conn()
        conn.execute("INSERT INTO invite_codes (code, trainer_id, created_at) VALUES (?, ?, ?)", (code, cb.from_user.id, datetime.now().isoformat()))
        conn.commit(); conn.close()
        text = f"🤝 *Ачивку за совместную тренировку можно получить только с другом из бота!*\n\nОтправь ему код: `{code}`\nПусть он зайдет и введет: `/link {code}`\n\nА пока можешь вписать его ник вручную."
        await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Вписать друга вручную", callback_data="joint_manual")],
            [InlineKeyboardButton(text="🏁 Завершить", callback_data="spotter:no")]
        ]))
        return
    rows = [[InlineKeyboardButton(text=f"@{f['reg_id']}", callback_data=f"joint_do:{f['user_id']}")] for f in friends]
    rows.append([InlineKeyboardButton(text="✍️ Вписать друга вручную", callback_data="joint_manual")])
    await cb.message.edit_text("С кем тренировался?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("joint_do:"))
async def joint_do(cb: CallbackQuery):
    tid = int(cb.data.split(":")[1])
    today = datetime.now().isoformat()
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO achivements (user_id, name, date) VALUES (?, '🤝 Верный напарник', ?)", (cb.from_user.id, today))
    conn.execute("INSERT OR IGNORE INTO achivements (user_id, name, date) VALUES (?, '🤝 Верный напарник', ?)", (tid, today))
    conn.commit(); conn.close()
    await cb.bot.send_message(tid, "🤝 Тебе присвоена ачивка «Верный напарник»!")
    await cb.message.edit_text("🏁 Тренировка завершена
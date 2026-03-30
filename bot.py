import calendar
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


WORK_MODE_OFFICE = "office"
WORK_MODE_HOME = "home"
WORK_MODE_OFF = "off"
VALID_MODES = {WORK_MODE_OFFICE, WORK_MODE_HOME}
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass
class UserRecord:
    user_id: int
    username: str
    full_name: str
    is_active: int


class ScheduleDB:
    def __init__(self, path: str) -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    full_name TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    work_date TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('office', 'home')),
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, work_date),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS home_days (
                    user_id INTEGER NOT NULL,
                    work_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, work_date),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_home_days (
                    user_id INTEGER NOT NULL,
                    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 4),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, weekday),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
                """
            )

    def upsert_user(self, user_id: int, username: str, full_name: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, full_name, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    is_active=1,
                    updated_at=excluded.updated_at
                """,
                (user_id, username, full_name, now, now),
            )

    def get_user(self, user_id: int) -> Optional[UserRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, full_name, is_active FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            return UserRecord(
                user_id=row["user_id"],
                username=row["username"] or "",
                full_name=row["full_name"],
                is_active=row["is_active"],
            )

    def deactivate_user(self, user_id: int) -> bool:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET is_active=0, updated_at=? WHERE user_id=?",
                (now, user_id),
            )
            return cur.rowcount > 0

    def list_active_users(self) -> list[UserRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, username, full_name, is_active
                FROM users
                WHERE is_active=1
                ORDER BY full_name COLLATE NOCASE ASC
                """
            ).fetchall()
            return [
                UserRecord(
                    user_id=row["user_id"],
                    username=row["username"] or "",
                    full_name=row["full_name"],
                    is_active=row["is_active"],
                )
                for row in rows
            ]

    def set_home_day(self, user_id: int, work_date: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO home_days(user_id, work_date, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, work_date, now),
            )
            # Cleanup legacy office entries for this date.
            conn.execute(
                "DELETE FROM schedules WHERE user_id=? AND work_date=? AND mode='office'",
                (user_id, work_date),
            )

    def remove_home_day(self, user_id: int, work_date: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM home_days WHERE user_id=? AND work_date=?",
                (user_id, work_date),
            )
            # Backward compatibility for previous model.
            conn.execute(
                "DELETE FROM schedules WHERE user_id=? AND work_date=?",
                (user_id, work_date),
            )
            return cur.rowcount > 0

    def is_home_day(self, user_id: int, work_date: str) -> bool:
        with self._connect() as conn:
            explicit_row = conn.execute(
                "SELECT 1 FROM home_days WHERE user_id=? AND work_date=?",
                (user_id, work_date),
            ).fetchone()
            if explicit_row is not None:
                return True
            legacy_row = conn.execute(
                "SELECT 1 FROM schedules WHERE user_id=? AND work_date=? AND mode='home'",
                (user_id, work_date),
            ).fetchone()
            return legacy_row is not None

    def set_weekly_home_day(self, user_id: int, weekday: int) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO weekly_home_days(user_id, weekday, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, weekday, now),
            )

    def remove_weekly_home_day(self, user_id: int, weekday: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM weekly_home_days WHERE user_id=? AND weekday=?",
                (user_id, weekday),
            )
            return cur.rowcount > 0

    def is_weekly_home_day(self, user_id: int, weekday: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM weekly_home_days WHERE user_id=? AND weekday=?",
                (user_id, weekday),
            ).fetchone()
            return row is not None


def parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        ids.add(int(chunk))
    return ids


def normalize_username(username: Optional[str]) -> str:
    if not username:
        return ""
    return username.strip().lstrip("@")


def mode_label(mode: Optional[str]) -> str:
    if mode == WORK_MODE_OFFICE:
        return "Офис"
    if mode == WORK_MODE_HOME:
        return "Дом"
    if mode == WORK_MODE_OFF:
        return "Выходной"
    return "—"


def parse_date_arg(raw: Optional[str]) -> date:
    if raw is None or raw.strip() == "":
        return date.today()
    text = raw.strip().lower()
    if text in {"today", "сегодня"}:
        return date.today()
    if text in {"tomorrow", "завтра"}:
        return date.today() + timedelta(days=1)
    return datetime.strptime(text, "%Y-%m-%d").date()


def week_bounds(anchor: date) -> tuple[date, date]:
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    return start, end


def ensure_registered_user(update: Update, db: ScheduleDB) -> UserRecord:
    tg_user = update.effective_user
    if tg_user is None:
        raise RuntimeError("No telegram user in update")
    username = normalize_username(tg_user.username)
    full_name = " ".join(part for part in [tg_user.first_name, tg_user.last_name] if part).strip()
    if not full_name:
        full_name = username or f"user_{tg_user.id}"
    db.upsert_user(tg_user.id, username, full_name)
    record = db.get_user(tg_user.id)
    if record is None:
        raise RuntimeError("User registration failed")
    return record


def require_active_user(user: UserRecord) -> bool:
    return user.is_active == 1


def render_day(db: ScheduleDB, day: date) -> str:
    rows = db.list_active_users()
    lines = [f"📅 <b>{day.strftime('%d.%m.%Y (%A)')}</b>"]
    if not rows:
        lines.append("Пока нет участников.")
        return "\n".join(lines)
    for row in rows:
        username = f"@{row.username}" if row.username else f"id:{row.user_id}"
        lines.append(f"- {row.full_name} ({username}): <b>{mode_label(day_status(db, row.user_id, day))}</b>")
    return "\n".join(lines)


def iter_week_days(anchor: date) -> Iterable[date]:
    start, _ = week_bounds(anchor)
    for i in range(7):
        yield start + timedelta(days=i)


def day_status(db: ScheduleDB, user_id: int, day: date) -> str:
    if day.weekday() >= 5:
        return WORK_MODE_OFF
    if db.is_home_day(user_id, day.isoformat()):
        return WORK_MODE_HOME
    if db.is_weekly_home_day(user_id, day.weekday()):
        return WORK_MODE_HOME
    return WORK_MODE_OFFICE


def month_bounds(year: int, month: int) -> tuple[date, date]:
    first_day = date(year, month, 1)
    _, last_num = calendar.monthrange(year, month)
    return first_day, date(year, month, last_num)


def month_title(year: int, month: int) -> str:
    names = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    ]
    return f"{names[month - 1]} {year}"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def build_month_keyboard(db: ScheduleDB, user_id: int, year: int, month: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.append([InlineKeyboardButton(day, callback_data="cal:noop") for day in WEEKDAYS_RU])
    for week in calendar.monthcalendar(year, month):
        row: list[InlineKeyboardButton] = []
        for weekday, day_num in enumerate(week):
            if day_num == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
                continue
            current = date(year, month, day_num)
            status = day_status(db, user_id, current)
            suffix = "🏠" if status == WORK_MODE_HOME else ("⛔" if status == WORK_MODE_OFF else "🏢")
            row.append(
                InlineKeyboardButton(
                    f"{day_num}{suffix}",
                    callback_data=f"cal:pick:{current.isoformat()}",
                )
            )
        keyboard.append(row)
    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)
    keyboard.append(
        [
            InlineKeyboardButton("⬅️", callback_data=f"cal:nav:{prev_y:04d}-{prev_m:02d}"),
            InlineKeyboardButton("Закрыть", callback_data="cal:close"),
            InlineKeyboardButton("➡️", callback_data=f"cal:nav:{next_y:04d}-{next_m:02d}"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_day_editor_keyboard(db: ScheduleDB, user_id: int, day: date) -> InlineKeyboardMarkup:
    weekday = day.weekday()
    is_home = db.is_home_day(user_id, day.isoformat())
    is_weekly = weekday < 5 and db.is_weekly_home_day(user_id, weekday)
    home_text = "Убрать 'дом' на этот день" if is_home else "Сделать этот день 'дом'"
    weekly_text = (
        f"Каждую неделю: {'включено' if is_weekly else 'выключено'}"
        if weekday < 5
        else "Каждую неделю недоступно (выходной)"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(home_text, callback_data=f"cal:togday:{day.isoformat()}")]
    ]
    if weekday < 5:
        rows.append([InlineKeyboardButton(weekly_text, callback_data=f"cal:togweek:{day.isoformat()}")])
    else:
        rows.append([InlineKeyboardButton(weekly_text, callback_data="cal:noop")])
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад в календарь",
                callback_data=f"cal:nav:{day.year:04d}-{day.month:02d}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def resolve_user_for_removal(db: ScheduleDB, ref: str) -> Optional[UserRecord]:
    ref = ref.strip()
    if not ref:
        return None
    with db._connect() as conn:
        if ref.isdigit():
            row = conn.execute(
                "SELECT user_id, username, full_name, is_active FROM users WHERE user_id=?",
                (int(ref),),
            ).fetchone()
        else:
            username = normalize_username(ref)
            row = conn.execute(
                "SELECT user_id, username, full_name, is_active FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        return UserRecord(
            user_id=row["user_id"],
            username=row["username"] or "",
            full_name=row["full_name"],
            is_active=row["is_active"],
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    status = "активен" if user.is_active == 1 else "неактивен"
    await update.message.reply_text(
        "Привет! Ты зарегистрирован в расписании.\n"
        f"Статус: {status}\n\n"
        "Команды:\n"
        "/calendar [YYYY-MM] — отметить дни из дома в календаре\n"
        "/set YYYY-MM-DD office|home — ручной ввод (совместимость)\n"
        "/day [YYYY-MM-DD] — расписание на день\n"
        "/week [YYYY-MM-DD] — расписание на неделю\n"
        "/myday [YYYY-MM-DD] — моя запись на день\n"
        "/delete YYYY-MM-DD — удалить свою запись\n"
        "/participants — список участников\n"
        "/help — помощь"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_cmd(update, context)


async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Ты отключен администратором. Обратись к администратору.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /set YYYY-MM-DD office|home")
        return
    try:
        day = parse_date_arg(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверная дата. Используй формат YYYY-MM-DD")
        return
    mode = context.args[1].strip().lower()
    if mode not in VALID_MODES:
        await update.message.reply_text("Режим только office или home")
        return
    if day.weekday() >= 5:
        await update.message.reply_text("Суббота и воскресенье всегда выходные.")
        return
    if mode == WORK_MODE_HOME:
        db.set_home_day(user.user_id, day.isoformat())
    else:
        db.remove_home_day(user.user_id, day.isoformat())
    await update.message.reply_text(f"Записал: {day.isoformat()} — {mode_label(day_status(db, user.user_id, day))}")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /delete YYYY-MM-DD")
        return
    try:
        day = parse_date_arg(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверная дата. Используй формат YYYY-MM-DD")
        return
    deleted = db.remove_home_day(user.user_id, day.isoformat())
    if deleted:
        await update.message.reply_text(f"Удалил запись за {day.isoformat()}")
    else:
        await update.message.reply_text(f"На {day.isoformat()} записи не было")


async def myday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    try:
        day = parse_date_arg(context.args[0] if context.args else None)
    except ValueError:
        await update.message.reply_text("Неверная дата. Используй формат YYYY-MM-DD")
        return
    mode = day_status(db, user.user_id, day)
    await update.message.reply_text(
        f"{day.isoformat()}: {mode_label(mode)}"
    )


async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Ты отключен администратором. Обратись к администратору.")
        return
    if context.args:
        try:
            year_text, month_text = context.args[0].split("-")
            year = int(year_text)
            month = int(month_text)
            _ = date(year, month, 1)
        except (ValueError, IndexError):
            await update.message.reply_text("Формат: /calendar YYYY-MM")
            return
    else:
        today = date.today()
        year, month = today.year, today.month
    first_day, last_day = month_bounds(year, month)
    text = (
        f"📆 <b>{month_title(year, month)}</b>\n"
        "Выбери день, чтобы отметить 'дом'.\n"
        "Сб/Вс всегда выходные.\n\n"
        f"Период: {first_day.isoformat()} — {last_day.isoformat()}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_month_keyboard(db, user.user_id, year, month),
    )


async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await query.edit_message_text("Ты отключен администратором. Обратись к администратору.")
        return

    data = query.data
    if data == "cal:noop":
        return
    if data == "cal:close":
        await query.edit_message_text("Календарь закрыт.")
        return
    if data.startswith("cal:nav:"):
        year_text, month_text = data.split(":", 2)[2].split("-")
        year = int(year_text)
        month = int(month_text)
        first_day, last_day = month_bounds(year, month)
        text = (
            f"📆 <b>{month_title(year, month)}</b>\n"
            "Выбери день, чтобы отметить 'дом'.\n"
            "Сб/Вс всегда выходные.\n\n"
            f"Период: {first_day.isoformat()} — {last_day.isoformat()}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_month_keyboard(db, user.user_id, year, month),
        )
        return
    if data.startswith("cal:pick:"):
        picked_day = datetime.strptime(data.split(":", 2)[2], "%Y-%m-%d").date()
        text = (
            f"🛠 <b>{picked_day.strftime('%d.%m.%Y (%A)')}</b>\n"
            f"Текущий статус: <b>{mode_label(day_status(db, user.user_id, picked_day))}</b>\n"
            "Выбери действие:"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_day_editor_keyboard(db, user.user_id, picked_day),
        )
        return
    if data.startswith("cal:togday:"):
        picked_day = datetime.strptime(data.split(":", 2)[2], "%Y-%m-%d").date()
        if picked_day.weekday() >= 5:
            await query.answer("Сб/Вс всегда выходные", show_alert=True)
            return
        if db.is_home_day(user.user_id, picked_day.isoformat()):
            db.remove_home_day(user.user_id, picked_day.isoformat())
        else:
            db.set_home_day(user.user_id, picked_day.isoformat())
        text = (
            f"🛠 <b>{picked_day.strftime('%d.%m.%Y (%A)')}</b>\n"
            f"Текущий статус: <b>{mode_label(day_status(db, user.user_id, picked_day))}</b>\n"
            "Выбери действие:"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_day_editor_keyboard(db, user.user_id, picked_day),
        )
        return
    if data.startswith("cal:togweek:"):
        picked_day = datetime.strptime(data.split(":", 2)[2], "%Y-%m-%d").date()
        weekday = picked_day.weekday()
        if weekday >= 5:
            await query.answer("Для выходных недоступно", show_alert=True)
            return
        if db.is_weekly_home_day(user.user_id, weekday):
            db.remove_weekly_home_day(user.user_id, weekday)
        else:
            db.set_weekly_home_day(user.user_id, weekday)
        text = (
            f"🛠 <b>{picked_day.strftime('%d.%m.%Y (%A)')}</b>\n"
            f"Текущий статус: <b>{mode_label(day_status(db, user.user_id, picked_day))}</b>\n"
            "Выбери действие:"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_day_editor_keyboard(db, user.user_id, picked_day),
        )
        return


async def day_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    ensure_registered_user(update, db)
    try:
        day = parse_date_arg(context.args[0] if context.args else None)
    except ValueError:
        await update.message.reply_text("Неверная дата. Используй формат YYYY-MM-DD")
        return
    await update.message.reply_text(render_day(db, day), parse_mode=ParseMode.HTML)


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    ensure_registered_user(update, db)
    try:
        anchor = parse_date_arg(context.args[0] if context.args else None)
    except ValueError:
        await update.message.reply_text("Неверная дата. Используй формат YYYY-MM-DD")
        return

    start, end = week_bounds(anchor)
    parts = [f"🗓 <b>Неделя {start.isoformat()} — {end.isoformat()}</b>"]
    for day in iter_week_days(anchor):
        parts.append("")
        parts.append(render_day(db, day))
    await update.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)


async def participants_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    ensure_registered_user(update, db)
    users = db.list_active_users()
    if not users:
        await update.message.reply_text("Нет активных участников.")
        return
    lines = ["👥 Активные участники:"]
    for u in users:
        username = f"@{u.username}" if u.username else f"id:{u.user_id}"
        lines.append(f"- {u.full_name} ({username})")
    await update.message.reply_text("\n".join(lines))


async def remove_participant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    admins: set[int] = context.application.bot_data["admins"]
    actor = ensure_registered_user(update, db)
    if actor.user_id not in admins:
        await update.message.reply_text("Только админ может удалять участников.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Формат: /remove_participant @username|user_id")
        return
    target = resolve_user_for_removal(db, context.args[0])
    if target is None:
        await update.message.reply_text("Пользователь не найден.")
        return
    if target.user_id == actor.user_id:
        await update.message.reply_text("Нельзя удалить самого себя.")
        return
    if target.is_active == 0:
        await update.message.reply_text("Пользователь уже неактивен.")
        return
    db.deactivate_user(target.user_id)
    username = f"@{target.username}" if target.username else f"id:{target.user_id}"
    await update.message.reply_text(f"Участник отключен: {target.full_name} ({username})")


async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Неизвестная команда. Используй /help")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def main() -> None:
    token = require_env("BOT_TOKEN")
    db_path = os.getenv("DB_PATH", "schedule.db")
    admin_ids_raw = require_env("ADMIN_IDS")
    admins = parse_admin_ids(admin_ids_raw)
    if not admins:
        raise RuntimeError("ADMIN_IDS must contain at least one Telegram user id")

    db = ScheduleDB(db_path)
    app = Application.builder().token(token).build()
    app.bot_data["db"] = db
    app.bot_data["admins"] = admins

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("myday", myday_cmd))
    app.add_handler(CommandHandler("day", day_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("participants", participants_cmd))
    app.add_handler(CommandHandler("remove_participant", remove_participant_cmd))
    app.add_handler(CallbackQueryHandler(calendar_callback, pattern=r"^cal:"))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

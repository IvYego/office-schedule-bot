import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
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

TEAM_NAMES = (
    "Игорь",
    "Ваня",
    "Оля",
    "Карина",
    "Настя",
    "Беслан",
    "Влад",
    "Илья",
)
TEAM_NAMES_SET = frozenset(TEAM_NAMES)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    url = os.getenv("MINIAPP_URL", "").strip()
    row_a: list[KeyboardButton] = []
    if url:
        row_a.append(KeyboardButton("Приложение", web_app=WebAppInfo(url=url)))
    row_a.append(KeyboardButton("/participants"))
    return ReplyKeyboardMarkup(
        [
            row_a,
            [KeyboardButton("/name"), KeyboardButton("/help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@dataclass
class UserRecord:
    user_id: int
    username: str
    full_name: str
    display_name: Optional[str]
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
            try:
                conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            except sqlite3.OperationalError:
                pass
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

    def set_display_name(self, user_id: int, display_name: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users SET display_name=?, updated_at=? WHERE user_id=?
                """,
                (display_name, now, user_id),
            )

    def get_user(self, user_id: int) -> Optional[UserRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, full_name, display_name, is_active FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            dn = row["display_name"]
            return UserRecord(
                user_id=row["user_id"],
                username=row["username"] or "",
                full_name=row["full_name"],
                display_name=dn if dn else None,
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
                SELECT user_id, username, full_name, display_name, is_active
                FROM users
                WHERE is_active=1
                ORDER BY COALESCE(display_name, full_name) COLLATE NOCASE ASC
                """
            ).fetchall()
            return [
                UserRecord(
                    user_id=row["user_id"],
                    username=row["username"] or "",
                    full_name=row["full_name"],
                    display_name=(row["display_name"] if row["display_name"] else None),
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


def profile_complete(user: UserRecord) -> bool:
    return bool(user.display_name and str(user.display_name).strip())


def user_public_name(user: UserRecord) -> str:
    if user.display_name and str(user.display_name).strip():
        return str(user.display_name).strip()
    return user.full_name


def day_status(db: ScheduleDB, user_id: int, day: date) -> str:
    if day.weekday() >= 5:
        return WORK_MODE_OFF
    if db.is_home_day(user_id, day.isoformat()):
        return WORK_MODE_HOME
    if db.is_weekly_home_day(user_id, day.weekday()):
        return WORK_MODE_HOME
    return WORK_MODE_OFFICE


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


def build_name_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    names = list(TEAM_NAMES)
    for i in range(0, len(names), 2):
        row = [InlineKeyboardButton(names[i], callback_data=f"name:{names[i]}")]
        if i + 1 < len(names):
            row.append(InlineKeyboardButton(names[i + 1], callback_data=f"name:{names[i + 1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def welcome_after_onboarding_html(user: UserRecord) -> str:
    name = user_public_name(user)
    extra = ""
    if os.getenv("MINIAPP_URL", "").strip():
        extra = "\n\nРасписание — в кнопке <b>Приложение</b>."
    return (
        f"<b>{name}</b>\n"
        "Каждое утро бот пришлёт, кто в офисе сегодня и завтра "
        "(в субботу тишина, в воскресенье — только про понедельник)."
        f"{extra}\n\n"
        "<code>/participants</code>  <code>/name</code>  <code>/help</code>"
    )


async def prompt_choose_name_message(update: Update) -> None:
    msg = update.effective_message
    if msg is None:
        return
    await msg.reply_text("\u2060", reply_markup=ReplyKeyboardRemove())
    await msg.reply_text(
        "<b>Офис · расписание</b>\n\nКто ты?",
        parse_mode=ParseMode.HTML,
        reply_markup=build_name_keyboard(),
    )


def resolve_user_for_removal(db: ScheduleDB, ref: str) -> Optional[UserRecord]:
    ref = ref.strip()
    if not ref:
        return None
    with db._connect() as conn:
        if ref.isdigit():
            row = conn.execute(
                "SELECT user_id, username, full_name, display_name, is_active FROM users WHERE user_id=?",
                (int(ref),),
            ).fetchone()
        else:
            username = normalize_username(ref)
            row = conn.execute(
                "SELECT user_id, username, full_name, display_name, is_active FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        dn = row["display_name"]
        return UserRecord(
            user_id=row["user_id"],
            username=row["username"] or "",
            full_name=row["full_name"],
            display_name=dn if dn else None,
            is_active=row["is_active"],
        )


def join_names(names: list[str]) -> str:
    if not names:
        return "никого"
    return ", ".join(names)


def names_in_office_for_date(db: ScheduleDB, d: date) -> list[str]:
    out: list[str] = []
    for u in db.list_active_users():
        if not profile_complete(u):
            continue
        if day_status(db, u.user_id, d) == WORK_MODE_OFFICE:
            out.append(user_public_name(u))
    out.sort(key=lambda s: s.lower())
    return out


def build_digest_text(db: ScheduleDB, today: date) -> Optional[str]:
    """Сб — не слать. Вс — только «завтра» (пн). Пн–Пт — сегодня + завтра."""
    wd = today.weekday()
    if wd == 5:
        return None
    if wd == 6:
        monday = today + timedelta(days=1)
        n = names_in_office_for_date(db, monday)
        return f"Завтра в офисе: {join_names(n)}"
    t = names_in_office_for_date(db, today)
    tomorrow = today + timedelta(days=1)
    t2 = names_in_office_for_date(db, tomorrow)
    return f"Сегодня в офисе: {join_names(t)}\n\nЗавтра в офисе: {join_names(t2)}"


def list_digest_recipient_ids(db: ScheduleDB) -> list[int]:
    return [u.user_id for u in db.list_active_users() if profile_complete(u)]


async def daily_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    tz_name = os.getenv("DIGEST_TIMEZONE", "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    text = build_digest_text(db, today)
    if text is None:
        logger.debug("Digest skipped (weekend rule): %s", today.isoformat())
        return
    for uid in list_digest_recipient_ids(db):
        try:
            await context.bot.send_message(chat_id=uid, text=text)
        except Exception as e:
            logger.warning("Digest failed for %s: %s", uid, e)


def register_digest_job(app: Application) -> None:
    if os.getenv("DIGEST_ENABLED", "true").lower() in ("0", "false", "no"):
        logger.info("Daily digest disabled (DIGEST_ENABLED)")
        return
    jq = app.job_queue
    if jq is None:
        logger.warning("JobQueue unavailable — install: pip install 'python-telegram-bot[job-queue]'")
        return
    tz_name = os.getenv("DIGEST_TIMEZONE", "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    h = int(os.getenv("DIGEST_HOUR", "9"))
    mi = int(os.getenv("DIGEST_MINUTE", "0"))
    run_at = time(hour=h, minute=mi, tzinfo=tz)
    jq.run_daily(daily_digest_job, time=run_at, name="daily_digest")
    logger.info("Daily digest scheduled at %s (%s)", run_at.isoformat(), tz_name)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Доступ отключён. Напиши администратору.")
        return
    if not profile_complete(user):
        await prompt_choose_name_message(update)
        return
    user = db.get_user(user.user_id)
    if user is None:
        return
    await update.message.reply_text(
        welcome_after_onboarding_html(user),
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_keyboard(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Доступ отключён.")
        return
    if not profile_complete(user):
        await prompt_choose_name_message(update)
        return
    user = db.get_user(user.user_id)
    if user is None:
        return
    await update.message.reply_text(
        welcome_after_onboarding_html(user),
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_keyboard(),
    )


async def app_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = os.getenv("MINIAPP_URL", "").strip()
    if not url:
        await update.message.reply_text("Мини-приложение не настроено (нет MINIAPP_URL на сервере).")
        return
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Доступ отключён.")
        return
    if not profile_complete(user):
        await prompt_choose_name_message(update)
        return
    await update.message.reply_text(
        "Открой <b>Приложение</b> — календарь.\nПосле выхода: /menu — кнопки.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Приложение", web_app=WebAppInfo(url=url))]],
            resize_keyboard=True,
        ),
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not profile_complete(user):
        await prompt_choose_name_message(update)
        return
    await update.message.reply_text("Команды", reply_markup=main_reply_keyboard())


async def name_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await update.message.reply_text("Доступ отключён.")
        return
    await update.message.reply_text("\u2060", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(
        "<b>Кто ты?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_name_keyboard(),
    )


async def name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None or query.data is None:
        return
    await query.answer()
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not require_active_user(user):
        await query.edit_message_text("Доступ отключён.")
        return

    data = query.data
    if not data.startswith("name:"):
        return
    raw = data[5:]
    if raw not in TEAM_NAMES_SET:
        await query.answer("Неверный выбор", show_alert=True)
        return

    db.set_display_name(user.user_id, raw)
    refreshed = db.get_user(user.user_id)
    if refreshed is None:
        return
    await query.edit_message_text(
        f"<b>{raw}</b> · сохранено",
        parse_mode=ParseMode.HTML,
    )
    await query.message.reply_text(
        welcome_after_onboarding_html(refreshed),
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_keyboard(),
    )


async def participants_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: ScheduleDB = context.application.bot_data["db"]
    user = ensure_registered_user(update, db)
    if not profile_complete(user):
        await prompt_choose_name_message(update)
        return
    users = db.list_active_users()
    if not users:
        await update.message.reply_text("Нет активных участников.")
        return
    lines = ["<b>Участники</b>"]
    for u in users:
        username = f"@{u.username}" if u.username else f"id:{u.user_id}"
        lines.append(f"— {user_public_name(u)} · {username}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


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
    await update.message.reply_text(f"Отключён: {user_public_name(target)} ({username})")


async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Неизвестная команда. Используй /help")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Старт и имя"),
            BotCommand("app", "Мини-приложение"),
            BotCommand("name", "Сменить имя"),
            BotCommand("participants", "Участники"),
            BotCommand("menu", "Кнопки"),
            BotCommand("help", "Помощь"),
        ]
    )
    register_digest_job(app)


def main() -> None:
    token = require_env("BOT_TOKEN")
    db_path = os.getenv("DB_PATH", "schedule.db")
    admin_ids_raw = require_env("ADMIN_IDS")
    admins = parse_admin_ids(admin_ids_raw)
    if not admins:
        raise RuntimeError("ADMIN_IDS must contain at least one Telegram user id")

    db = ScheduleDB(db_path)
    app = Application.builder().token(token).post_init(post_init).build()
    app.bot_data["db"] = db
    app.bot_data["admins"] = admins

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("name", name_cmd))
    app.add_handler(CommandHandler("app", app_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("participants", participants_cmd))
    app.add_handler(CommandHandler("remove_participant", remove_participant_cmd))
    app.add_handler(CallbackQueryHandler(name_callback, pattern=r"^name:"))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

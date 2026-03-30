"""
HTTPS-only Mini App backend (run behind nginx or Caddy with TLS).
Serves SPA + JSON API validated via Telegram WebApp initData.
"""

from __future__ import annotations

import calendar
import os
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bot import (
    TEAM_NAMES,
    ScheduleDB,
    day_status,
    month_title,
    normalize_username,
    profile_complete,
    shift_month,
    user_public_name,
    WORK_MODE_HOME,
    WORK_MODE_OFF,
)
from tg_webapp import user_id_from_validated, validate_webapp_init_data

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"
STATIC_DIR = WEBAPP_DIR / "static"


def get_bot_token() -> str:
    t = os.getenv("BOT_TOKEN", "").strip()
    if not t:
        raise RuntimeError("BOT_TOKEN is required for Mini App")
    return t


_db: Optional[ScheduleDB] = None


def get_db() -> ScheduleDB:
    global _db
    if _db is None:
        _db = ScheduleDB(os.getenv("DB_PATH", "schedule.db"))
    return _db


app = FastAPI(title="Office schedule mini app")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MonthApplyBody(BaseModel):
    year: int
    month: int
    apply_home: list[str] = Field(default_factory=list)
    remove_home: list[str] = Field(default_factory=list)


def auth_user_id(init_data_header: Optional[str]) -> int:
    if not init_data_header:
        raise HTTPException(status_code=401, detail="Missing init data")
    validated = validate_webapp_init_data(init_data_header.strip(), get_bot_token())
    if not validated:
        raise HTTPException(status_code=401, detail="Invalid init data")
    uid = user_id_from_validated(validated)
    if uid is None:
        raise HTTPException(status_code=401, detail="No user in init data")

    user_obj = validated.get("user")
    if isinstance(user_obj, dict):
        un = normalize_username(user_obj.get("username"))
        fn = (user_obj.get("first_name") or "").strip()
        ln = (user_obj.get("last_name") or "").strip()
        full_name = f"{fn} {ln}".strip() or un or f"user_{uid}"
        get_db().upsert_user(uid, un, full_name)
    return uid


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    index_path = WEBAPP_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="index.html missing")
    return FileResponse(index_path, media_type="text/html; charset=utf-8")


@app.get("/api/me")
def api_me(x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data")):
    db = get_db()
    uid = auth_user_id(x_telegram_init_data)
    user = db.get_user(uid)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive")
    return {
        "user_id": uid,
        "profile_complete": profile_complete(user),
        "display_name": user_public_name(user),
        "team_names": list(TEAM_NAMES),
    }


@app.get("/api/month")
def api_month(
    year: int,
    month: int,
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
):
    db = get_db()
    uid = auth_user_id(x_telegram_init_data)
    user = db.get_user(uid)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not profile_complete(user):
        raise HTTPException(status_code=428, detail="Choose name in /start first")

    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Invalid month")

    days_out: list[dict] = []
    for week in calendar.monthcalendar(year, month):
        for weekday, day_num in enumerate(week):
            if day_num == 0:
                continue
            d = date(year, month, day_num)
            st = day_status(db, uid, d)
            if st == WORK_MODE_HOME:
                state = "home"
            elif st == WORK_MODE_OFF:
                state = "off"
            else:
                state = "office"
            days_out.append(
                {
                    "iso": d.isoformat(),
                    "day": day_num,
                    "weekday": weekday,
                    "state": state,
                }
            )

    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)
    return {
        "year": year,
        "month": month,
        "title": month_title(year, month),
        "days": days_out,
        "prev": {"year": prev_y, "month": prev_m},
        "next": {"year": next_y, "month": next_m},
    }


@app.post("/api/month/apply")
def api_month_apply(
    body: MonthApplyBody,
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
):
    db = get_db()
    uid = auth_user_id(x_telegram_init_data)
    user = db.get_user(uid)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not profile_complete(user):
        raise HTTPException(status_code=428, detail="Choose name in /start first")

    changed = 0
    for iso in body.apply_home:
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if d.year != body.year or d.month != body.month or d.weekday() >= 5:
            continue
        db.set_home_day(uid, iso)
        changed += 1
    for iso in body.remove_home:
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if d.year != body.year or d.month != body.month or d.weekday() >= 5:
            continue
        db.remove_home_day(uid, iso)
        changed += 1
    return {"ok": True, "updated": changed}


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

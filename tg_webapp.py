"""Validate Telegram WebApp initData (Mini App)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl


def validate_webapp_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> Optional[dict[str, Any]]:
    """
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    Returns parsed key-value pairs including 'user' as dict if valid, else None.
    """
    if not init_data or not bot_token:
        return None
    try:
        parsed_list = parse_qsl(init_data, strict_parsing=True, encoding="utf-8")
    except ValueError:
        return None
    parsed: dict[str, str] = dict(parsed_list)
    hash_received = parsed.pop("hash", None)
    if not hash_received:
        return None

    check_pairs = [f"{k}={parsed[k]}" for k in sorted(parsed.keys())]
    data_check_string = "\n".join(check_pairs)

    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if calculated != hash_received:
        return None

    auth_date = int(parsed.get("auth_date") or 0)
    if auth_date and (time.time() - auth_date) > max_age_seconds:
        return None

    out: dict[str, Any] = dict(parsed)
    if "user" in parsed:
        try:
            out["user"] = json.loads(parsed["user"])
        except (json.JSONDecodeError, TypeError):
            return None
    return out


def user_id_from_validated(data: dict[str, Any]) -> Optional[int]:
    user = data.get("user")
    if not isinstance(user, dict) or "id" not in user:
        return None
    try:
        return int(user["id"])
    except (TypeError, ValueError):
        return None

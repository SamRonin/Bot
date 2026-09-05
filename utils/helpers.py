"""Small shared helper functions (dates, digits, formatting)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import settings

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_num(value: object) -> str:
    """Convert Latin digits to Persian digits for user-facing texts."""
    return str(value).translate(_FA_DIGITS)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tehran_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return ZoneInfo("Asia/Tehran")


def today_start_utc_iso() -> str:
    """UTC ISO timestamp of today's 00:00 in the bot timezone (daily reset)."""
    now = datetime.now(tehran_tz())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def format_dt_fa(iso_value: str | None) -> str:
    """Format an ISO datetime for users, e.g. ۱۴۰۳/۰۶/۱۵ − ۱۴:۳۰."""
    if not iso_value:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(tehran_tz())
        return fa_num(local.strftime("%Y/%m/%d − %H:%M"))
    except Exception:
        return "—"


def pro_remaining_text(expires_iso: str | None) -> str:
    if not expires_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(expires_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        if delta.total_seconds() <= 0:
            return "منقضی شده"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"{fa_num(days)} روز و {fa_num(hours)} ساعت"
        if hours > 0:
            return f"{fa_num(hours)} ساعت"
        minutes = max(1, delta.seconds // 60)
        return f"{fa_num(minutes)} دقیقه"
    except Exception:
        return "—"


def split_text(text: str, chunk: int = 4000) -> list[str]:
    """Split a long text into Telegram-safe chunks (tries line boundaries)."""
    if len(text) <= chunk:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > chunk:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    # Fallback for a single gigantic line
    final: list[str] = []
    for p in parts:
        while len(p) > chunk:
            final.append(p[:chunk])
            p = p[chunk:]
        if p:
            final.append(p)
    return final or [text[:chunk]]


def safe_format(template: str, **kwargs: object) -> str:
    """str.format that never crashes on unknown placeholders."""
    try:
        return template.format(**kwargs)
    except Exception:
        return template

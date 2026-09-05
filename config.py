"""Central configuration loaded from environment variables.

All secrets and tunables live here. In production (Railway) these come from
the service Variables panel; locally they can live in a `.env` file.
See `.env.example` for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # no-op if there is no .env file (e.g. on Railway)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default


def _get_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


@dataclass
class Settings:
    """All bot settings with safe defaults for Railway's free tier."""

    bot_token: str = ""
    admin_ids: set[int] = field(default_factory=set)

    # Paths
    db_path: str = "data/bot.db"
    temp_dir: str = "temp"

    # External AI API (key-less)
    prexzy_base: str = "https://prexzyapis.com"

    # Conversion limits
    free_daily_limit: int = 3
    pro_daily_limit: int = 50
    free_max_mb: int = 20
    pro_max_mb: int = 50
    max_input_seconds: int = 300  # reject videos longer than this (CPU guard)
    note_max_seconds: int = 60    # Telegram video-note max length (we trim)
    ffmpeg_timeout: int = 240     # seconds per conversion

    # Watermark removal for video-note -> video. New Telegram clients (2025+)
    # burn an animated brand logo into the note file itself. This is the
    # delogo rectangle in PERCENTS of the frame: "x,y,w,h" (0-100).
    # Set NOTE_DELOGO="" to disable removal entirely.
    note_delogo: str = "68,72,30,26"
    
    # AI limits
    ai_free_daily: int = 15
    ai_pro_daily: int = 100

    # Pro / referral system
    invites_for_pro: int = 2
    pro_days: int = 15

    # Daily counters reset at midnight in this timezone
    timezone: str = "Asia/Tehran"

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            bot_token=_get_str("BOT_TOKEN", ""),
            admin_ids=_get_admin_ids(),
            db_path=_get_str("DB_PATH", "data/bot.db"),
            temp_dir=_get_str("TEMP_DIR", "temp"),
            prexzy_base=_get_str("PREXZY_API_BASE", "https://prexzyapis.com").rstrip("/"),
            free_daily_limit=_get_int("FREE_DAILY_LIMIT", 3),
            pro_daily_limit=_get_int("PRO_DAILY_LIMIT", 50),
            free_max_mb=_get_int("FREE_MAX_MB", 20),
            pro_max_mb=_get_int("PRO_MAX_MB", 50),
            max_input_seconds=_get_int("MAX_INPUT_SECONDS", 300),
            ffmpeg_timeout=_get_int("FFMPEG_TIMEOUT", 240),
        note_delogo=_get_str("NOTE_DELOGO", "68,72,30,26"),
            ai_free_daily=_get_int("AI_FREE_DAILY", 15),
            ai_pro_daily=_get_int("AI_PRO_DAILY", 100),
            invites_for_pro=_get_int("INVITES_FOR_PRO", 2),
            pro_days=_get_int("PRO_DAYS", 15),
            timezone=_get_str("TIMEZONE", "Asia/Tehran"),
        )

    def is_admin(self, user_id: int | None) -> bool:
        return bool(user_id) and user_id in self.admin_ids


settings = Settings.load()

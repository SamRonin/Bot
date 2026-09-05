"""In-memory runtime stores (locks, pending sends, cooldowns).

These are intentionally ephemeral: they only guard in-flight operations.
Persistent state always lives in SQLite.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from config import settings

# Bot username (without the leading @), filled at startup from get_me();
# used to build referral deep-links.
#
# IMPORTANT: always read this through `get_bot_username()` (or as
# `store.bot_username`). Never do `from utils.store import bot_username` —
# that copies the empty startup value into the importing module and freezes
# it there, which produced links like `https://t.me/?start=ref_123`.
bot_username: str = ""


def set_bot_username(value: str | None) -> str:
    """Store the bot username, normalised (no '@', no surrounding spaces)."""
    global bot_username
    bot_username = (value or "").strip().lstrip("@").strip()
    return bot_username


def get_bot_username() -> str:
    """Current bot username, falling back to the BOT_USERNAME env setting.

    Resolved at call time, so it always reflects what startup discovered.
    """
    return bot_username or settings.bot_username


async def ensure_bot_username(bot) -> str:
    """Return the bot username, asking Telegram once if it isn't known yet.

    Normally `bot.py` fills this at startup; this is the safety net so a
    referral link can never be built without a username.
    """
    known = get_bot_username()
    if known:
        return known
    try:
        me = await bot.me()  # cached by aiogram after the first call
        return set_bot_username(me.username)
    except Exception:  # network hiccup — caller decides what to do
        return ""

# One conversion at a time per user (protects free-tier CPU/RAM).
user_locks: dict[int, asyncio.Lock] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    lock = user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        user_locks[user_id] = lock
    return lock


@dataclass
class PendingSend:
    """Converted file waiting for the 'send to channel/group?' answer."""

    file_id: str
    kind: str  # "note" | "video"


# user_id -> last converted file
pending_sends: dict[int, PendingSend] = {}


@dataclass
class BroadcastState:
    running: bool = False


broadcast = BroadcastState()

# user_id -> last AI request timestamp (simple anti-spam cooldown)
ai_cooldowns: dict[int, float] = {}

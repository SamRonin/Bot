"""In-memory runtime stores (locks, pending sends, cooldowns).

These are intentionally ephemeral: they only guard in-flight operations.
Persistent state always lives in SQLite.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

# Bot username (without @), filled at startup; used for referral links.
bot_username: str = ""

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

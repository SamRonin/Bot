"""Async SQLite database layer (aiosqlite).

Design notes:
- One short-lived connection per operation with `timeout=30`, so concurrent
  handlers never block each other for long and WAL mode keeps readers fast.
- All timestamps are stored as UTC ISO strings; "today" is computed from the
  bot timezone (Asia/Tehran) so daily limits reset at Tehran midnight.
- Runtime-tunable values (limits, texts) live in the `settings` table so the
  admin panel can change them without redeploying.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from config import settings
from utils.helpers import today_start_utc_iso, utcnow_iso

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    joined_at       TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    is_banned       INTEGER NOT NULL DEFAULT 0,
    pro_expires_at  TEXT,
    pending_invites INTEGER NOT NULL DEFAULT 0,
    total_invites   INTEGER NOT NULL DEFAULT 0,
    pro_cycles      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS referrals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER NOT NULL,
    referred_id INTEGER NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    file_size  INTEGER NOT NULL DEFAULT 0,
    duration   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_date ON conversions (user_id, created_at);
CREATE TABLE IF NOT EXISTS ai_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_user_date ON ai_usage (user_id, created_at);
CREATE TABLE IF NOT EXISTS destinations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    chat_id    TEXT NOT NULL,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'channel',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, chat_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_TEXTS = {
    "start_text": (
        "👋 سلام {name} عزیز!\n\n"
        "من ربات تبدیل ویدیو هستم 🎬\n\n"
        "🎥 یه <b>ویدیو معمولی</b> بفرست تا به <b>ویدیو مسیج گرد</b> تبدیلش کنم\n"
        "⭕️ یه <b>ویدیو مسیج گرد</b> بفرست تا به <b>ویدیو معمولی</b> تبدیلش کنم\n\n"
        "بعد از تبدیل می‌تونی با یه کلیک بفرستیش توی کانال یا گروهت 📢\n\n"
        "🎁 سهمیه رایگان: <b>{limit} تبدیل در روز</b>\n"
        "⭐️ با دعوت {need} نفر، {days} روز حساب <b>پرو</b> بگیر!"
    ),
    "help_text": (
        "ℹ️ <b>راهنمای ربات</b>\n\n"
        "🎬 <b>تبدیل ویدیو:</b>\n"
        "• ویدیوی معمولی → ویدیو مسیج گرد (حداکثر ۶۰ ثانیه)\n"
        "• ویدیو مسیج گرد → ویدیوی معمولی\n\n"
        "📢 <b>ارسال به کانال/گروه:</b>\n"
        "بعد از هر تبدیل ازت می‌پرسم بفرستمش جایی یا نه.\n"
        "اول ربات رو توی کانال/گروهت <b>ادمین</b> کن، بعد از بخش «📢 مقصدها» اضافه‌ش کن.\n\n"
        "⭐️ <b>حساب پرو:</b>\n"
        "با دعوت {need} نفر جدید، {days} روز پرو می‌گیری و محدودیت روزانه‌ت خیلی بیشتر می‌شه. این چرخه تا ابد ادامه داره!\n\n"
        "💬 <b>پشتیبانی هوشمند:</b>\n"
        "هر سوالی درباره ربات داری بپرس؛ خلاصه‌سازی و ترجمه هم بلدم.\n\n"
        "📩 دستورات: /start ، /help ، /pro ، /destinations ، /ai"
    ),
}


class Database:
    def __init__(self, path: str):
        self.path = path

    # ---------- low-level helpers ----------

    def _connect(self):
        return aiosqlite.connect(self.path, timeout=30)

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.executescript(SCHEMA)
            # Seed default settings (only if missing, so admin edits survive).
            defaults = {
                "free_daily_limit": str(settings.free_daily_limit),
                "pro_daily_limit": str(settings.pro_daily_limit),
                "ai_free_daily": str(settings.ai_free_daily),
                "ai_pro_daily": str(settings.ai_pro_daily),
                "free_max_mb": str(settings.free_max_mb),
                "pro_max_mb": str(settings.pro_max_mb),
                "invites_for_pro": str(settings.invites_for_pro),
                "pro_days": str(settings.pro_days),
                **DEFAULT_TEXTS,
            }
            for key, value in defaults.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            await db.commit()
        log.info("Database ready at %s", self.path)

    # ---------- settings ----------

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._connect() as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else default

    async def get_int_setting(self, key: str, default: int) -> int:
        try:
            return int(await self.get_setting(key, str(default)))
        except ValueError:
            return default

    async def set_setting(self, key: str, value: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await db.commit()

    # ---------- users ----------

    async def get_or_create_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> tuple[dict, bool]:
        """Return (user_dict, is_new). Updates username/name on every call."""
        now = utcnow_iso()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO users (user_id, username, first_name, joined_at, "
                    "last_active_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, first_name, now, now),
                )
                await db.commit()
                return {
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "joined_at": now,
                    "last_active_at": now,
                    "is_banned": 0,
                    "pro_expires_at": None,
                    "pending_invites": 0,
                    "total_invites": 0,
                    "pro_cycles": 0,
                }, True
            await db.execute(
                "UPDATE users SET username = ?, first_name = ?, "
                "last_active_at = ? WHERE user_id = ?",
                (username, first_name, now, user_id),
            )
            await db.commit()
            user = dict(row)
            user.update(
                {"username": username, "first_name": first_name,
                 "last_active_at": now}
            )
            return user, False

    async def get_user(self, user_id: int) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def touch(self, user_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET last_active_at = ? WHERE user_id = ?",
                (utcnow_iso(), user_id),
            )
            await db.commit()

    async def set_ban(self, user_id: int, banned: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET is_banned = ? WHERE user_id = ?",
                (1 if banned else 0, user_id),
            )
            await db.commit()

    async def find_users(self, query: str, limit: int = 5) -> list[dict]:
        query = query.strip().lstrip("@")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if query.isdigit():
                async with db.execute(
                    "SELECT * FROM users WHERE user_id = ? OR username = ? "
                    "LIMIT ?",
                    (int(query), query, limit),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM users WHERE username LIKE ? OR first_name "
                    "LIKE ? LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ) as cur:
                    rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------- pro / referrals ----------

    @staticmethod
    def _is_pro_row(user: dict | None) -> bool:
        if not user or not user.get("pro_expires_at"):
            return False
        try:
            exp = datetime.fromisoformat(user["pro_expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp > datetime.now(timezone.utc)
        except Exception:
            return False

    async def is_pro(self, user_id: int) -> bool:
        return self._is_pro_row(await self.get_user(user_id))

    async def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Record a referral. Returns False if this user was already referred
        (safety net — callers only pass brand-new users)."""
        async with self._connect() as db:
            try:
                await db.execute(
                    "INSERT INTO referrals (referrer_id, referred_id, "
                    "created_at) VALUES (?, ?, ?)",
                    (referrer_id, referred_id, utcnow_iso()),
                )
                await db.execute(
                    "UPDATE users SET pending_invites = pending_invites + 1, "
                    "total_invites = total_invites + 1 WHERE user_id = ?",
                    (referrer_id,),
                )
                await db.commit()
                return True
            except Exception:  # UNIQUE violation etc.
                return False

    async def check_and_grant_pro(self, referrer_id: int) -> dict:
        """Grant 15 (configurable) days of Pro for every N new invites.

        Infinite cycle: after each grant, `pending_invites` resets to 0 so the
        next N *new* users grant Pro again, forever.
        """
        needed = await self.get_int_setting(
            "invites_for_pro", settings.invites_for_pro
        )
        days = await self.get_int_setting("pro_days", settings.pro_days)
        user = await self.get_user(referrer_id)
        if not user:
            return {"granted": False, "pending": 0, "needed": needed}
        pending = int(user.get("pending_invites") or 0)
        if pending < needed:
            return {"granted": False, "pending": pending, "needed": needed}

        now = datetime.now(timezone.utc)
        try:
            current_exp = datetime.fromisoformat(user["pro_expires_at"])
            if current_exp.tzinfo is None:
                current_exp = current_exp.replace(tzinfo=timezone.utc)
        except Exception:
            current_exp = now
        base = max(now, current_exp)
        new_exp = (base + timedelta(days=days)).isoformat()

        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET pro_expires_at = ?, pending_invites = 0, "
                "pro_cycles = pro_cycles + 1 WHERE user_id = ?",
                (new_exp, referrer_id),
            )
            await db.commit()
        return {
            "granted": True,
            "pending": 0,
            "needed": needed,
            "expires": new_exp,
            "days": days,
        }

    async def add_pro_days(self, user_id: int, days: int) -> str | None:
        user = await self.get_user(user_id)
        if not user:
            return None
        now = datetime.now(timezone.utc)
        try:
            current = datetime.fromisoformat(user["pro_expires_at"])
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
        except Exception:
            current = now
        new_exp = (max(now, current) + timedelta(days=days)).isoformat()
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET pro_expires_at = ? WHERE user_id = ?",
                (new_exp, user_id),
            )
            await db.commit()
        return new_exp

    async def revoke_pro(self, user_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE users SET pro_expires_at = NULL WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()

    # ---------- usage / limits ----------

    async def _count_since(self, table: str, user_id: int, since_iso: str) -> int:
        async with self._connect() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND "
                "created_at >= ?",
                (user_id, since_iso),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def today_conversions(self, user_id: int) -> int:
        return await self._count_since(
            "conversions", user_id, today_start_utc_iso()
        )

    async def today_ai(self, user_id: int) -> int:
        return await self._count_since("ai_usage", user_id, today_start_utc_iso())

    async def log_conversion(
        self, user_id: int, kind: str, file_size: int = 0, duration: int = 0
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO conversions (user_id, kind, file_size, duration, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, kind, file_size, duration, utcnow_iso()),
            )
            await db.commit()

    async def log_ai(self, user_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO ai_usage (user_id, created_at) VALUES (?, ?)",
                (user_id, utcnow_iso()),
            )
            await db.commit()

    # ---------- destinations ----------

    async def add_destination(
        self, user_id: int, chat_id: str, title: str, kind: str
    ) -> tuple[bool, str]:
        """Add (or refresh) a destination. First one becomes default."""
        existing = await self.list_destinations(user_id)
        is_default = 1 if not existing else 0
        async with self._connect() as db:
            try:
                await db.execute(
                    "INSERT INTO destinations (user_id, chat_id, title, kind, "
                    "is_default, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id, chat_id) DO UPDATE SET title = "
                    "excluded.title, kind = excluded.kind",
                    (user_id, chat_id, title, kind, is_default,
                     utcnow_iso()),
                )
                await db.commit()
                return True, ""
            except Exception as exc:
                log.exception("add_destination failed")
                return False, str(exc)

    async def list_destinations(self, user_id: int) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM destinations WHERE user_id = ? ORDER BY "
                "is_default DESC, id ASC",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_destination(self, dest_id: int) -> dict | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM destinations WHERE id = ?", (dest_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_destination(self, dest_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            async with db.execute(
                "DELETE FROM destinations WHERE id = ? AND user_id = ?",
                (dest_id, user_id),
            ) as cur:
                deleted = cur.rowcount
            # If the default was deleted, promote the oldest remaining one.
            async with db.execute(
                "SELECT COUNT(*) FROM destinations WHERE user_id = ? AND "
                "is_default = 1",
                (user_id,),
            ) as cur:
                has_default = (await cur.fetchone())[0]
            if not has_default:
                await db.execute(
                    "UPDATE destinations SET is_default = 1 WHERE id = "
                    "(SELECT id FROM destinations WHERE user_id = ? ORDER BY "
                    "id ASC LIMIT 1)",
                    (user_id,),
                )
            await db.commit()
        return bool(deleted)

    async def admin_delete_destination(self, dest_id: int) -> bool:
        async with self._connect() as db:
            async with db.execute(
                "DELETE FROM destinations WHERE id = ?", (dest_id,)
            ) as cur:
                deleted = cur.rowcount
            await db.commit()
        return bool(deleted)

    async def set_default_destination(self, dest_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            async with db.execute(
                "SELECT id FROM destinations WHERE id = ? AND user_id = ?",
                (dest_id, user_id),
            ) as cur:
                if not await cur.fetchone():
                    return False
            await db.execute(
                "UPDATE destinations SET is_default = 0 WHERE user_id = ?",
                (user_id,),
            )
            await db.execute(
                "UPDATE destinations SET is_default = 1 WHERE id = ?",
                (dest_id,),
            )
            await db.commit()
        return True

    async def recent_destinations_global(self, limit: int = 20) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM destinations ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------- stats / logs ----------

    async def stats(self) -> dict:
        today = today_start_utc_iso()
        week_ago = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat()
        now_iso = utcnow_iso()
        async with self._connect() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE joined_at >= ?", (today,)
            ) as cur:
                today_users = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE last_active_at >= ?",
                (week_ago,),
            ) as cur:
                active_7d = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE pro_expires_at IS NOT NULL "
                "AND pro_expires_at > ?",
                (now_iso,),
            ) as cur:
                pro_count = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE is_banned = 1"
            ) as cur:
                banned = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM conversions WHERE created_at >= ?",
                (today,),
            ) as cur:
                conv_today = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM conversions") as cur:
                conv_total = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM ai_usage WHERE created_at >= ?", (today,)
            ) as cur:
                ai_today = (await cur.fetchone())[0]
        return {
            "total": total,
            "today": today_users,
            "active_7d": active_7d,
            "pro": pro_count,
            "banned": banned,
            "conv_today": conv_today,
            "conv_total": conv_total,
            "ai_today": ai_today,
        }

    async def all_user_ids(self) -> list[int]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE is_banned = 0"
            ) as cur:
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def recent_conversions(self, limit: int = 10) -> list[dict]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT c.*, u.username, u.first_name FROM conversions c "
                "LEFT JOIN users u ON u.user_id = c.user_id "
                "ORDER BY c.id DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]


db = Database(settings.db_path)

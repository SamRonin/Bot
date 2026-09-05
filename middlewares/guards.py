"""Global middlewares: user provisioning, ban check, activity tracking."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from database import db

log = logging.getLogger(__name__)

BANNED_TEXT = "🚫 دسترسی شما به ربات مسدود شده. اگه فکر می‌کنی اشتباهیه، با پشتیبانی در میون بذار."


class UserMiddleware(BaseMiddleware):
    """Ensures a DB row for every user, blocks banned users.

    Puts `db_user` (dict) and `is_new_user` (bool) into handler `data`.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)

        db_user, is_new = await db.get_or_create_user(
            user.id, user.username, user.first_name
        )
        if db_user.get("is_banned"):
            if isinstance(event, Message):
                await event.answer(BANNED_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer(BANNED_TEXT, show_alert=True)
            return None

        data["db_user"] = db_user
        data["is_new_user"] = is_new
        return await handler(event, data)

"""Post-conversion promo flow (configurable from the admin panel).

Two steps, right after a successful conversion:

1. Under the «می‌خوای برات بفرستمش توی کانال یا گروه؟» prompt the bot sends
   `promo_text` — a plain text message with one inline button
   («📖 دریافت راهنما»).
2. When the user taps that button, the bot sends `promo_photo` with
   `promo_caption` as the photo's caption (text-only when no photo is set).

All three values live in the `settings` table and are edited from the admin
panel:

- promo_text    → step-1 message text (empty = step 1 disabled)
- promo_photo   → photo file_id for step 2 (empty = send text only)
- promo_caption → caption under the photo in step 2
"""
from __future__ import annotations

import logging

from aiogram import Bot

from database import db
from database.db import PROMO_DEFAULTS
from keyboards.main import promo_keyboard

log = logging.getLogger(__name__)


async def get_promo() -> tuple[str, str, str]:
    """Return (text, photo_file_id, caption); any of them may be empty."""
    text = await db.get_setting("promo_text", PROMO_DEFAULTS["promo_text"])
    photo = (await db.get_setting("promo_photo", "")).strip()
    caption = await db.get_setting(
        "promo_caption", PROMO_DEFAULTS["promo_caption"]
    )
    return text, photo, caption


async def send_promo(bot: Bot, chat_id: int) -> bool:
    """Step 1: send promo_text + «📖 دریافت راهنما» button.

    Returns True on success, False when disabled or on failure.
    Never raises — a broken promo must not break the conversion flow.
    """
    text, _, _ = await get_promo()
    text = text.strip()
    if not text:
        return False
    try:
        await bot.send_message(chat_id, text, reply_markup=promo_keyboard())
        return True
    except Exception:  # noqa: BLE001  (bad HTML, blocked bot, ...)
        log.warning("promo text send failed", exc_info=True)
        return False


async def send_promo_guide(bot: Bot, chat_id: int) -> bool:
    """Step 2: answer the «دریافت راهنما» tap with photo + caption.

    Falls back to caption-only when no photo is set. Returns True on
    success, False when nothing is configured or sending fails. Never raises.
    """
    _, photo, caption = await get_promo()
    caption = caption.strip()
    if not photo and not caption:
        return False
    try:
        if photo:
            await bot.send_photo(
                chat_id, photo=photo, caption=caption or None
            )
        else:
            await bot.send_message(chat_id, caption)
        return True
    except Exception:  # noqa: BLE001  (invalid file_id, bad HTML, ...)
        log.warning("promo guide send failed", exc_info=True)
        return False

"""Post-conversion promo message (photo + caption + «دریافت راهنما» button).

Right after a successful conversion — under the «می‌خوای برات بفرستمش توی
کانال یا گروه؟» prompt — the bot sends a promo message: an optional photo
with a caption and one inline button («📖 دریافت راهنما») that answers with
the bot's help text. Both the photo and the caption are editable from the
admin panel and stored in the `settings` table:

- promo_photo   → Telegram file_id of the photo (empty = send text only)
- promo_caption → caption text, raw HTML (empty = photo/button only)

If BOTH are empty the promo is effectively disabled and nothing is sent.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from database import db
from database.db import PROMO_DEFAULTS
from keyboards.main import promo_keyboard

log = logging.getLogger(__name__)


async def get_promo() -> tuple[str, str]:
    """Return (photo_file_id, caption). Either may be an empty string."""
    photo = (await db.get_setting("promo_photo", "")).strip()
    caption = await db.get_setting(
        "promo_caption", PROMO_DEFAULTS["promo_caption"]
    )
    return photo, caption


async def send_promo(bot: Bot, chat_id: int) -> bool:
    """Send the promo message. Returns True on success, False when promo is
    disabled or sending fails. Never raises — a broken promo must not break
    the conversion flow."""
    photo, caption = await get_promo()
    caption = caption.strip()
    if not photo and not caption:
        return False
    try:
        if photo:
            await bot.send_photo(
                chat_id,
                photo=photo,
                caption=caption or None,
                reply_markup=promo_keyboard(),
            )
        else:
            await bot.send_message(
                chat_id, caption, reply_markup=promo_keyboard()
            )
        return True
    except Exception:  # noqa: BLE001  (invalid file_id, bad HTML, ...)
        log.warning("promo send failed", exc_info=True)
        return False

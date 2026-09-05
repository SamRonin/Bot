"""/start with referral (deep-link) handling + welcome message."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from database import db
from keyboards.main import main_menu
from utils.helpers import fa_num, safe_format
from utils.store import ensure_bot_username, get_bot_username

log = logging.getLogger(__name__)
router = Router()


def referral_link_for(user_id: int) -> str:
    """Build the deep-link that credits `user_id` as the referrer.

    The username is looked up at call time via `get_bot_username()`. Importing
    the `bot_username` value directly would bind the empty string present at
    import time and yield a broken `https://t.me/?start=ref_...` link.
    """
    username = get_bot_username()
    if not username:
        log.warning(
            "bot_username is empty while building a referral link for %s — "
            "check the startup get_me() call or set BOT_USERNAME.",
            user_id,
        )
    return f"https://t.me/{username}?start=ref_{user_id}"


async def referral_link_for_async(bot, user_id: int) -> str:
    """Like `referral_link_for`, but resolves the username if it's unknown.

    Preferred inside handlers: it guarantees a usable link even if the
    startup lookup was skipped or failed.
    """
    await ensure_bot_username(bot)
    return referral_link_for(user_id)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    db_user: dict,
    is_new_user: bool,
    state: FSMContext,
) -> None:
    await state.clear()
    user_id = message.from_user.id
    name = message.from_user.first_name or "دوست عزیز"

    # ---- referral: only counts if the joiner is BRAND NEW ----
    args = (command.args or "").strip()
    if args.startswith("ref_") and is_new_user:
        try:
            referrer_id = int(args[4:])
        except ValueError:
            referrer_id = 0
        if referrer_id and referrer_id != user_id:
            referrer = await db.get_user(referrer_id)
            if referrer and not referrer.get("is_banned"):
                recorded = await db.add_referral(referrer_id, user_id)
                if recorded:
                    result = await db.check_and_grant_pro(referrer_id)
                    try:
                        if result["granted"]:
                            await message.bot.send_message(
                                referrer_id,
                                "🎉 تبریک! با دعوت ۲ نفر جدید، "
                                f"<b>{fa_num(result['days'])} روز حساب پرو</b> "
                                "فعال شد! ⭐️\n\n"
                                "با ۲ نفر جدید دیگه، دوباره تمدیدش کن؛ "
                                "این چرخه تا ابد ادامه داره ♾️",
                            )
                        else:
                            await message.bot.send_message(
                                referrer_id,
                                "👥 یه نفر با لینک تو عضو شد! "
                                f"({fa_num(result['pending'])}/{fa_num(result['needed'])})\n"
                                "با دعوت ۲ نفر جدید، ۱۵ روز پرو می‌گیری ⭐️",
                            )
                    except Exception:
                        pass  # referrer blocked the bot etc. — not fatal

    free_limit = await db.get_int_setting(
        "free_daily_limit", settings.free_daily_limit
    )
    need = await db.get_int_setting("invites_for_pro", settings.invites_for_pro)
    days = await db.get_int_setting("pro_days", settings.pro_days)
    template = await db.get_setting("start_text", "👋 سلام {name}!")
    text = safe_format(
        template, name=name, limit=fa_num(free_limit),
        need=fa_num(need), days=fa_num(days),
    )
    await message.answer(
        text, reply_markup=main_menu(settings.is_admin(user_id))
    )

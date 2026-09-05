"""/pro — Pro status + referral link."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from database import db
from handlers.start import referral_link_for_async
from keyboards.main import share_referral_keyboard
from utils.helpers import fa_num, format_dt_fa, pro_remaining_text

router = Router()


async def show_pro(message: Message) -> None:
    user_id = message.from_user.id
    ref_link = await referral_link_for_async(message.bot, user_id)
    user = await db.get_user(user_id)
    need = await db.get_int_setting("invites_for_pro", settings.invites_for_pro)
    days = await db.get_int_setting("pro_days", settings.pro_days)
    is_pro = await db.is_pro(user_id)

    pending = int((user or {}).get("pending_invites") or 0)
    total = int((user or {}).get("total_invites") or 0)

    if is_pro:
        status = (
            "⭐️ <b>حساب تو پروئه!</b>\n"
            f"⏳ مونده: <b>{pro_remaining_text(user['pro_expires_at'])}</b>\n"
            f"📅 انقضا: {format_dt_fa(user['pro_expires_at'])}\n"
        )
    else:
        status = "🆓 <b>حساب تو رایگانه.</b>\n"

    free_limit = await db.get_int_setting(
        "free_daily_limit", settings.free_daily_limit
    )
    pro_limit = await db.get_int_setting(
        "pro_daily_limit", settings.pro_daily_limit
    )

    text = (
        f"{status}\n"
        f"🎁 با دعوت <b>{fa_num(need)} نفر جدید</b>، "
        f"<b>{fa_num(days)} روز پرو</b> بگیر!\n"
        f"👥 پیشرفت: <b>{fa_num(pending)}/{fa_num(need)}</b> "
        f"(کل دعوت‌ها: {fa_num(total)})\n\n"
        "⚠️ فقط کسایی حساب می‌شن که <b>برای اولین بار</b> ربات رو استارت بزنن.\n"
        f"♾️ بعد از هر {fa_num(days)} روز، با {fa_num(need)} نفر جدید دیگه "
        "تمدیدش کن — تا ابد!\n\n"
        f"💡 پرو یعنی روزانه <b>{fa_num(pro_limit)}</b> تبدیل به‌جای "
        f"<b>{fa_num(free_limit)}</b> + سقف حجم بالاتر.\n\n"
        "🔗 <b>لینک دعوت اختصاصی تو:</b>\n"
        f"<code>{ref_link}</code>"
    )
    await message.answer(
        text, reply_markup=share_referral_keyboard(ref_link)
    )


@router.message(Command("pro"))
async def cmd_pro(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_pro(message)

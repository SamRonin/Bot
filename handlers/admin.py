"""Full admin panel: stats, broadcast, users, limits, logs, channels, texts."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import settings
from database import db
from keyboards.main import (
    ALL_BUTTONS,
    BTN_CANCEL,
    admin_menu_keyboard,
    back_admin_keyboard,
    broadcast_confirm_keyboard,
    broadcast_mode_keyboard,
    limits_keyboard,
    texts_keyboard,
    user_actions_keyboard,
)
from utils.helpers import fa_num, format_dt_fa, pro_remaining_text, safe_format
from utils.store import broadcast as broadcast_state

log = logging.getLogger(__name__)
router = Router()

LIMIT_META = {  # key -> (label, min, max)
    "free_daily_limit": ("سقف روزانه رایگان", 1, 1000),
    "pro_daily_limit": ("سقف روزانه پرو", 1, 10000),
    "ai_free_daily": ("سقف هوش مصنوعی رایگان", 1, 1000),
    "ai_pro_daily": ("سقف هوش مصنوعی پرو", 1, 10000),
    "free_max_mb": ("حداکثر حجم رایگان (مگ)", 1, 20),   # Bot API cloud cap = 20MB
    "pro_max_mb": ("حداکثر حجم پرو (مگ)", 1, 20),
    "invites_for_pro": ("دعوت لازم برای پرو", 1, 100),
    "pro_days": ("روزهای پرو", 1, 365),
}

TEXT_META = {
    "start_text": "متن استارت",
    "help_text": "متن راهنما",
}


class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_user_query = State()
    waiting_limit_value = State()
    waiting_text_value = State()


def _is_admin(user_id: int | None) -> bool:
    return settings.is_admin(user_id or 0)


async def _deny(callback: CallbackQuery) -> None:
    await callback.answer("⛔️ فقط ادمین!", show_alert=True)


async def _menu_escape(message: Message, state: FSMContext) -> bool:
    """Let menu buttons/commands escape admin input states. Returns True if
    the message was intercepted and the state handler should stop."""
    text = (message.text or "").strip()
    if not text or (text not in ALL_BUTTONS and not text.startswith("/")):
        return False
    await state.clear()
    if text == BTN_CANCEL:
        await message.answer(
            "باشه، لغو شد ✅", reply_markup=admin_menu_keyboard()
        )
    else:
        await message.answer(
            "از حالت ورودی ادمین خارج شدی؛ دوباره بزن 👆",
            reply_markup=admin_menu_keyboard(),
        )
    return True


# ------------------------------------------------------------------ entry ---

async def show_admin_menu(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔️ این بخش فقط برای ادمینه!")
        return
    await message.answer("🛠 <b>پنل ادمین</b>\nیکی رو انتخاب کن 👇",
                         reply_markup=admin_menu_keyboard())


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_admin_menu(message)


@router.callback_query(F.data == "adm:menu")
async def adm_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "🛠 <b>پنل ادمین</b>\nیکی رو انتخاب کن 👇",
            reply_markup=admin_menu_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "adm:close")
async def adm_close(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


# ------------------------------------------------------------------ stats ---

@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await callback.answer()
    s = await db.stats()
    text = (
        "📊 <b>آمار کلی</b>\n\n"
        f"👥 کل کاربران: <b>{fa_num(s['total'])}</b>\n"
        f"🆕 کاربران امروز: <b>{fa_num(s['today'])}</b>\n"
        f"🟢 فعال ۷ روز اخیر: <b>{fa_num(s['active_7d'])}</b>\n"
        f"⭐️ کاربران پرو: <b>{fa_num(s['pro'])}</b>\n"
        f"🚫 بن‌شده‌ها: <b>{fa_num(s['banned'])}</b>\n\n"
        f"🎬 تبدیل‌های امروز: <b>{fa_num(s['conv_today'])}</b>\n"
        f"🎬 کل تبدیل‌ها: <b>{fa_num(s['conv_total'])}</b>\n"
        f"💬 درخواست هوش مصنوعی امروز: <b>{fa_num(s['ai_today'])}</b>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=back_admin_keyboard())
    except TelegramBadRequest:
        pass


# -------------------------------------------------------------- broadcast ---

@router.callback_query(F.data == "adm:broadcast")
async def adm_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    if broadcast_state.running:
        await callback.answer("یه پیام همگانی در حال ارساله!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📣 <b>پیام همگانی</b>\n\n"
            "پیامی که می‌خوای به همه ارسال بشه رو بفرست "
            "(متن، عکس، ویدیو... هر چی).\n\n"
            "برای انصراف /admin رو بزن.",
            reply_markup=back_admin_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.message(AdminStates.waiting_broadcast)
async def broadcast_receive(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    if await _menu_escape(message, state):
        return
    await state.update_data(
        bc_chat_id=message.chat.id, bc_message_id=message.message_id
    )
    await state.set_state(None)
    total = len(await db.all_user_ids())
    await message.answer(
        f"📣 این پیام به <b>{fa_num(total)}</b> کاربر ارسال می‌شه.\n"
        "به چه شکل بفرستم؟",
        reply_markup=broadcast_mode_keyboard(),
    )


@router.callback_query(F.data.startswith("bc:mode:"))
async def broadcast_mode(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    mode = callback.data.split(":")[2]
    await state.update_data(bc_mode=mode)
    await callback.answer()
    label = "کپی 📋" if mode == "copy" else "فوروارد ↪️"
    try:
        await callback.message.edit_text(
            f"حالت ارسال: <b>{label}</b>\nمطمئنی؟",
            reply_markup=broadcast_confirm_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "bc:cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await state.clear()
    await callback.answer("لغو شد ✅")
    try:
        await callback.message.edit_text(
            "🛠 <b>پنل ادمین</b>", reply_markup=admin_menu_keyboard()
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "bc:send")
async def broadcast_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    data = await state.get_data()
    await state.clear()
    chat_id = data.get("bc_chat_id")
    message_id = data.get("bc_message_id")
    mode = data.get("bc_mode", "copy")
    if not chat_id or not message_id:
        await callback.answer("پیام پیدا نشد؛ از اول شروع کن!", show_alert=True)
        return
    if broadcast_state.running:
        await callback.answer("در حال اجراست!", show_alert=True)
        return

    broadcast_state.running = True
    await callback.answer("⏳ شروع شد...")
    user_ids = await db.all_user_ids()
    sent, failed = 0, 0
    try:
        await callback.message.edit_text(
            f"⏳ در حال ارسال... (۰/{fa_num(len(user_ids))})"
        )
    except TelegramBadRequest:
        pass

    for i, uid in enumerate(user_ids, 1):
        try:
            if mode == "forward":
                await callback.bot.forward_message(
                    chat_id=uid, from_chat_id=chat_id, message_id=message_id
                )
            else:
                await callback.bot.copy_message(
                    chat_id=uid, from_chat_id=chat_id, message_id=message_id
                )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:  # one retry
                if mode == "forward":
                    await callback.bot.forward_message(
                        chat_id=uid, from_chat_id=chat_id, message_id=message_id
                    )
                else:
                    await callback.bot.copy_message(
                        chat_id=uid, from_chat_id=chat_id, message_id=message_id
                    )
                sent += 1
            except Exception:
                failed += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1  # blocked / deleted / deactivated
        except Exception:
            failed += 1
            log.exception("broadcast failed for %s", uid)

        await asyncio.sleep(0.05)  # stay under flood limits
        if i % 15 == 0 or i == len(user_ids):
            try:
                await callback.message.edit_text(
                    f"⏳ در حال ارسال... ({fa_num(i)}/{fa_num(len(user_ids))})"
                )
            except TelegramBadRequest:
                pass

    broadcast_state.running = False
    try:
        await callback.message.edit_text(
            "✅ <b>پیام همگانی تموم شد!</b>\n\n"
            f"📤 موفق: <b>{fa_num(sent)}</b>\n"
            f"❌ ناموفق (بلاک/حذف): <b>{fa_num(failed)}</b>",
            reply_markup=back_admin_keyboard(),
        )
    except TelegramBadRequest:
        pass


# ------------------------------------------------------------------ users ---

@router.callback_query(F.data == "adm:users")
async def adm_users(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await state.set_state(AdminStates.waiting_user_query)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "👥 <b>مدیریت کاربران</b>\n\n"
            "آیدی عددی یا یوزرنیم کاربر رو بفرست:",
            reply_markup=back_admin_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.message(AdminStates.waiting_user_query)
async def user_search(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    if await _menu_escape(message, state):
        return
    users = await db.find_users(message.text or "")
    if not users:
        await message.answer("❌ کاربری پیدا نشد!")
        return
    await state.set_state(None)
    for u in users[:3]:
        await message.answer(
            await _user_card(u["user_id"]),
            reply_markup=user_actions_keyboard(
                u["user_id"], bool(u.get("is_banned")),
                await db.is_pro(u["user_id"]),
            ),
        )


async def _user_card(user_id: int) -> str:
    u = await db.get_user(user_id)
    if not u:
        return "❌ کاربر پیدا نشد!"
    is_pro = await db.is_pro(user_id)
    used = await db.today_conversions(user_id)
    uname = f"@{u['username']}" if u.get("username") else "—"
    status = (
        f"⭐️ پرو (مونده: {pro_remaining_text(u['pro_expires_at'])})"
        if is_pro else "🆓 رایگان"
    )
    return (
        "👤 <b>مشخصات کاربر</b>\n\n"
        f"🆔 آیدی: <code>{u['user_id']}</code>\n"
        f"👤 نام: {u.get('first_name') or '—'}\n"
        f"📎 یوزرنیم: {uname}\n"
        f"💎 وضعیت: {status}\n"
        f"📅 انقضای پرو: {format_dt_fa(u.get('pro_expires_at'))}\n"
        f"👥 دعوت‌های این دوره: {fa_num(u.get('pending_invites') or 0)}\n"
        f"👥 کل دعوت‌ها: {fa_num(u.get('total_invites') or 0)}\n"
        f"🔁 دوره‌های پرو: {fa_num(u.get('pro_cycles') or 0)}\n"
        f"🎬 تبدیل امروز: {fa_num(used)}\n"
        f"📅 عضویت: {format_dt_fa(u.get('joined_at'))}\n"
        f"🕒 آخرین فعالیت: {format_dt_fa(u.get('last_active_at'))}\n"
        f"🚫 وضعیت بن: {'بن شده' if u.get('is_banned') else 'سالم'}"
    )


async def _refresh_user_card(callback: CallbackQuery, user_id: int) -> None:
    u = await db.get_user(user_id)
    if not u:
        await callback.answer("کاربر پیدا نشد!", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            await _user_card(user_id),
            reply_markup=user_actions_keyboard(
                user_id, bool(u.get("is_banned")), await db.is_pro(user_id)
            ),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("usr:pro:"))
async def user_grant_pro(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    uid = int(callback.data.split(":")[2])
    days = await db.get_int_setting("pro_days", settings.pro_days)
    await db.add_pro_days(uid, days)
    await callback.answer(f"✅ {days} روز پرو فعال شد!")
    await _refresh_user_card(callback, uid)


@router.callback_query(F.data.startswith("usr:unpro:"))
async def user_revoke_pro(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    uid = int(callback.data.split(":")[2])
    await db.revoke_pro(uid)
    await callback.answer("➖ پرو لغو شد!")
    await _refresh_user_card(callback, uid)


@router.callback_query(F.data.startswith("usr:ban:"))
async def user_ban(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    uid = int(callback.data.split(":")[2])
    if settings.is_admin(uid):
        await callback.answer("نمی‌تونی ادمین رو بن کنی! 😅", show_alert=True)
        return
    await db.set_ban(uid, True)
    await callback.answer("🚫 بن شد!")
    await _refresh_user_card(callback, uid)


@router.callback_query(F.data.startswith("usr:unban:"))
async def user_unban(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    uid = int(callback.data.split(":")[2])
    await db.set_ban(uid, False)
    await callback.answer("✅ رفع بن شد!")
    await _refresh_user_card(callback, uid)


# ----------------------------------------------------------------- limits ---

async def _limits_dict() -> dict:
    return {
        "free_daily_limit": await db.get_int_setting(
            "free_daily_limit", settings.free_daily_limit),
        "pro_daily_limit": await db.get_int_setting(
            "pro_daily_limit", settings.pro_daily_limit),
        "ai_free_daily": await db.get_int_setting(
            "ai_free_daily", settings.ai_free_daily),
        "ai_pro_daily": await db.get_int_setting(
            "ai_pro_daily", settings.ai_pro_daily),
        "free_max_mb": await db.get_int_setting(
            "free_max_mb", settings.free_max_mb),
        "pro_max_mb": await db.get_int_setting(
            "pro_max_mb", settings.pro_max_mb),
        "invites_for_pro": await db.get_int_setting(
            "invites_for_pro", settings.invites_for_pro),
        "pro_days": await db.get_int_setting("pro_days", settings.pro_days),
    }


@router.callback_query(F.data == "adm:limits")
async def adm_limits(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "⚙️ <b>مدیریت محدودیت‌ها</b>\nروی هر کدوم بزن تا عوضش کنی 👇",
            reply_markup=limits_keyboard(await _limits_dict()),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("lim:set:"))
async def limit_ask(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    key = callback.data.split(":")[2]
    if key not in LIMIT_META:
        await callback.answer("نامعتبر!", show_alert=True)
        return
    label, minimum, maximum = LIMIT_META[key]
    await state.set_state(AdminStates.waiting_limit_value)
    await state.update_data(limit_key=key)
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"⚙️ <b>{label}</b>\n\nعدد جدید رو بفرست (بین {fa_num(minimum)} تا {fa_num(maximum)}):",
            reply_markup=back_admin_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.message(AdminStates.waiting_limit_value)
async def limit_save(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    if await _menu_escape(message, state):
        return
    data = await state.get_data()
    key = data.get("limit_key")
    if key not in LIMIT_META:
        await state.clear()
        return
    label, minimum, maximum = LIMIT_META[key]
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ فقط عدد بفرست!")
        return
    if not (minimum <= value <= maximum):
        await message.answer(
            f"❌ باید بین {fa_num(minimum)} تا {fa_num(maximum)} باشه!"
        )
        return
    await db.set_setting(key, str(value))
    await state.clear()
    await message.answer(
        f"✅ <b>{label}</b> شد: <b>{fa_num(value)}</b>",
        reply_markup=limits_keyboard(await _limits_dict()),
    )


# ------------------------------------------------------------------- logs ---

@router.callback_query(F.data == "adm:logs")
async def adm_logs(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await callback.answer()
    rows = await db.recent_conversions(10)
    if not rows:
        text = "📝 هنوز تبدیلی ثبت نشده."
    else:
        lines = ["📝 <b>۱۰ تبدیل اخیر:</b>\n"]
        for r in rows:
            kind = "🎥→⭕️" if r["kind"] == "to_note" else "⭕️→🎥"
            who = f"@{r['username']}" if r.get("username") else f"🆔{r['user_id']}"
            mb = (r.get("file_size") or 0) / 1024 / 1024
            lines.append(
                f"{kind} {who} | {fa_num(f'{mb:.1f}')}MB | "
                f"{format_dt_fa(r['created_at'])}"
            )
        text = "\n".join(lines)
    try:
        await callback.message.edit_text(text, reply_markup=back_admin_keyboard())
    except TelegramBadRequest:
        pass


# --------------------------------------------------------------- channels ---

@router.callback_query(F.data == "adm:channels")
async def adm_channels(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await callback.answer()
    dests = await db.recent_destinations_global(15)
    if not dests:
        text = "📢 هنوز مقصدی ثبت نشده."
    else:
        lines = ["📢 <b>آخرین مقصدهای ثبت‌شده:</b>\n"]
        for d in dests:
            lines.append(
                f"• <b>{d['title']}</b> | 👤<code>{d['user_id']}</code> | 🆔<code>{d['id']}</code>"
            )
        lines.append("\nبرای حذف، روی 🗑 بزن:")
        text = "\n".join(lines)
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    for d in dests:
        builder.button(
            text=f"🗑 {d['id']}: {(d['title'] or '?')[:25]}",
            callback_data=f"chdel:{d['id']}",
        )
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    builder.adjust(1)
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("chdel:"))
async def adm_channel_delete(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    try:
        dest_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("نامعتبر!", show_alert=True)
        return
    ok = await db.admin_delete_destination(dest_id)
    await callback.answer("🗑 حذف شد!" if ok else "پیداش نکردم!")
    await adm_channels(callback)


# ------------------------------------------------------------------ texts ---

@router.callback_query(F.data == "adm:texts")
async def adm_texts(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "💬 <b>متن‌های ربات</b>\nکدوم رو عوض کنم؟",
            reply_markup=texts_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("txt:edit:"))
async def text_ask(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return await _deny(callback)
    key = callback.data.split(":")[2]
    if key not in TEXT_META:
        await callback.answer("نامعتبر!", show_alert=True)
        return
    current = await db.get_setting(key, "")
    await state.set_state(AdminStates.waiting_text_value)
    await state.update_data(text_key=key)
    await callback.answer()
    hint = ""
    if key == "start_text":
        hint = "\n\n💡 می‌تونی از <code>{name}</code> (اسم کاربر)، <code>{limit}</code>، <code>{need}</code> و <code>{days}</code> استفاده کنی."
    elif key == "help_text":
        hint = "\n\n💡 می‌تونی از <code>{need}</code> و <code>{days}</code> استفاده کنی."
    try:
        await callback.message.edit_text(
            f"💬 <b>{TEXT_META[key]}</b>\n\nمتن فعلی:\n{current}\n\nمتن جدید رو بفرست:{hint}",
            reply_markup=back_admin_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.message(AdminStates.waiting_text_value)
async def text_save(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    if await _menu_escape(message, state):
        return
    data = await state.get_data()
    key = data.get("text_key")
    if key not in TEXT_META or not message.text:
        await message.answer("❌ متن نامعتبره!")
        return
    # Validate placeholders render fine
    safe_format(
        message.text, name="تست", limit="۳", need="۲", days="۱۵"
    )
    await db.set_setting(key, message.text)
    await state.clear()
    await message.answer(
        f"✅ <b>{TEXT_META[key]}</b> ذخیره شد!",
        reply_markup=texts_keyboard(),
    )

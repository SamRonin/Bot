"""Video <-> video-note conversion + 'send to channel/group?' flow."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
)

from config import settings
from database import db
from keyboards.main import ask_send_keyboard, destinations_pick_keyboard
from services.converter import (
    ConvertError,
    ffprobe_available,
    probe,
    to_normal_video,
    to_video_note,
)
from services.promo import send_promo, send_promo_guide
from utils.helpers import fa_num, safe_format
from utils.store import get_user_lock, pending_sends, PendingSend

log = logging.getLogger(__name__)
router = Router()

# Telegram Bot API (cloud) refuses bot downloads above 20MB. Higher values
# only work with a self-hosted Bot API server, so we clamp to 20.
TELEGRAM_BOT_MAX_MB = 20

KIND_LABEL = {"note": "ویدیو مسیج گرد ⭕️", "video": "ویدیو معمولی 🎥"}


# ---------------------------------------------------------------------------
# entry points: user sends a video or a video-note
# ---------------------------------------------------------------------------

@router.message(F.video)
async def handle_video(message: Message, db_user: dict, state: FSMContext) -> None:
    await state.clear()  # a video always means "convert", exit any AI/etc mode
    await _convert(
        message, db_user,
        file_id=message.video.file_id,
        file_size=message.video.file_size or 0,
        duration=message.video.duration or 0,
        to_kind="note",
    )


@router.message(F.video_note)
async def handle_video_note(message: Message, db_user: dict, state: FSMContext) -> None:
    await state.clear()
    await _convert(
        message, db_user,
        file_id=message.video_note.file_id,
        file_size=message.video_note.file_size or 0,
        duration=message.video_note.duration or 0,
        to_kind="video",
    )


async def _convert(
    message: Message, db_user: dict, *, file_id: str, file_size: int,
    duration: int, to_kind: str,
) -> None:
    user_id = message.from_user.id

    is_pro = await db.is_pro(user_id)
    if is_pro:
        limit = await db.get_int_setting("pro_daily_limit", settings.pro_daily_limit)
        max_mb = await db.get_int_setting("pro_max_mb", settings.pro_max_mb)
    else:
        limit = await db.get_int_setting("free_daily_limit", settings.free_daily_limit)
        max_mb = await db.get_int_setting("free_max_mb", settings.free_max_mb)

    max_mb = min(max_mb, TELEGRAM_BOT_MAX_MB)

    used = await db.today_conversions(user_id)
    if used >= limit:
        from handlers.start import referral_link_for

        text = (
            "😔 <b>سهمیه امروزت تموم شد!</b>\n\n"
            f"تو امروز {fa_num(used)} تبدیل انجام دادی (سقف: {fa_num(limit)}).\n"
            "فردا دوباره می‌تونی استفاده کنی 🔄\n\n"
            "⭐️ می‌خوای نامحدود بشه؟ با دعوت ۲ نفر، ۱۵ روز پرو بگیر:\n"
            f"{referral_link_for(user_id)}"
        )
        if is_pro:
            text = (
                "😔 سقف امروز حسابت تموم شد!\n"
                f"({fa_num(used)}/{fa_num(limit)}) — فردا برمی‌گرده 🔄"
            )
        await message.answer(text)
        return

    if file_size and file_size > max_mb * 1024 * 1024:
        await message.answer(
            "📦 حجم این ویدیو زیاده!\n"
            f"سقف حجم برای تو: <b>{fa_num(max_mb)} مگابایت</b>.\n"
            + ("⭐️ با حساب پرو سقف حجمت بیشتر می‌شه!" if not is_pro else "")
        )
        return

    if duration and duration > settings.max_input_seconds:
        await message.answer(
            "⏱ ویدیو طولانیه! حداکثر طول ورودی "
            f"<b>{fa_num(settings.max_input_seconds // 60)} دقیقه</b> باشه."
        )
        return

    lock = get_user_lock(user_id)
    if lock.locked():
        await message.answer("⏳ یه تبدیل دیگه‌ت در حال انجامه؛ صبر کن تموم بشه!")
        return

    async with lock:
        status = await message.answer("⏳ دارم دانلود و تبدیلش می‌کنم... صبر کن 🙏")
        tmp_dir = Path(settings.temp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:12]
        src = str(tmp_dir / f"{user_id}_{tag}_in.mp4")
        dst = str(tmp_dir / f"{user_id}_{tag}_out.mp4")
        try:
            # 1) download from Telegram
            tg_file = await message.bot.get_file(file_id)
            await message.bot.download_file(tg_file.file_path, destination=src)
            if not os.path.exists(src) or os.path.getsize(src) == 0:
                raise ConvertError("❌ دانلود ویدیو ناموفق بود؛ دوباره بفرست.")

            # 2) validate (duration/size after download — metadata can lie)
            info = await probe(src) if ffprobe_available() else {
                "duration": duration, "size": os.path.getsize(src)}
            if info["size"] > max_mb * 1024 * 1024:
                await status.edit_text(
                    "📦 حجم این ویدیو از سقف مجاز بیشتره "
                    f"(<b>{fa_num(max_mb)} مگ</b>)."
                )
                return
            if info["duration"] > settings.max_input_seconds:
                await status.edit_text(
                    "⏱ ویدیو طولانیه! حداکثر طول ورودی "
                    f"<b>{fa_num(settings.max_input_seconds // 60)} دقیقه</b> باشه."
                )
                return
            trimmed = to_kind == "note" and info["duration"] > settings.note_max_seconds

            # 3) convert
            if to_kind == "note":
                out_info = await to_video_note(src, dst)
            else:
                out_info = await to_normal_video(src, dst)

            caption = f"✅ تمومه! اینم {KIND_LABEL[to_kind]} شما 🎉"
            if trimmed:
                caption += (
                    f"\n⚠️ چون ویدیو مسیج حداکثر {fa_num(settings.note_max_seconds)} "
                    "ثانیه‌ست، اضافه‌ش برش خورد."
                )
            remaining = max(0, limit - used - 1)
            caption += f"\n\n🔋 سهمیه امروز: {fa_num(remaining)} تبدیل مونده"

            if to_kind == "note":
                # answer_video_note takes `video_note=` and supports no caption
                sent = await message.answer_video_note(video_note=FSInputFile(dst))
                await message.answer(caption)
            else:
                sent = await message.answer_video(
                    video=FSInputFile(dst), caption=caption
                )
            new_file_id = (
                sent.video_note.file_id if to_kind == "note"
                else sent.video.file_id
            )
            pending_sends[user_id] = PendingSend(file_id=new_file_id, kind=to_kind)

            await db.log_conversion(
                user_id,
                "to_note" if to_kind == "note" else "to_video",
                file_size=int(out_info.get("size") or 0),
                duration=int(out_info.get("duration") or 0),
            )
            await status.delete()
            await message.answer(
                "می‌خوای برات بفرستمش توی کانال یا گروه؟ 📢",
                reply_markup=ask_send_keyboard(),
            )
            # Admin-configurable promo: text + «📖 دریافت راهنما» button.
            # Silently skipped when disabled; failures never break the flow.
            await send_promo(message.bot, message.chat.id)
        except ConvertError as exc:
            log.warning("convert failed for %s: %s", user_id, exc)
            try:
                await status.edit_text(str(exc))
            except TelegramBadRequest:
                await message.answer(str(exc))
        except Exception:  # noqa: BLE001
            log.exception("unexpected convert error for %s", user_id)
            try:
                await status.edit_text(
                    "❌ یه خطای غیرمنتظره پیش اومد؛ دوباره تلاش کن."
                )
            except TelegramBadRequest:
                pass
        finally:
            for p in (src, dst):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# promo message: «📖 دریافت راهنما» button
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "promo:help")
async def promo_help(callback: CallbackQuery) -> None:
    """Answer the promo's inline button with the admin-editable photo+caption."""
    sent = await send_promo_guide(callback.bot, callback.message.chat.id)
    if sent:
        await callback.answer()
    else:
        await callback.answer(
            "متأسفانه هنوز راهنمایی تنظیم نشده 🙏", show_alert=True
        )


# ---------------------------------------------------------------------------
# "send to channel/group?" answers
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "conv:no")
async def conv_no(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text("باشه عزیز! هر وقت ویدیوی دیگه‌ای داشتی بفرست 🎬💜")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "conv:yes")
async def conv_yes(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    pending = pending_sends.get(user_id)
    if not pending:
        await callback.answer("فایلت منقضی شده؛ دوباره تبدیل کن 🙂", show_alert=True)
        return
    dests = await db.list_destinations(user_id)
    if not dests:
        await callback.answer()
        try:
            await callback.message.edit_text(
                "📢 هنوز هیچ مقصدی ثبت نکردی!\n\n"
                "قدم‌ها:\n"
                "1️⃣ ربات رو توی کانال/گروهت <b>ادمین</b> کن\n"
                "2️⃣ از بخش «📢 مقصدها» اضافش کن\n\n"
                "بعدش برگرد اینجا و دوباره «آره، بفرست» رو بزن ✅"
            )
        except TelegramBadRequest:
            pass
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            "کجا بفرستمش؟ 👇\n(⭐ = پیش‌فرض)",
            reply_markup=destinations_pick_keyboard(dests),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("dest:pick:"))
async def dest_pick(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    try:
        dest_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("مقصد نامعتبره!", show_alert=True)
        return

    pending = pending_sends.get(user_id)
    if not pending:
        await callback.answer("فایلت منقضی شده؛ دوباره تبدیل کن 🙂", show_alert=True)
        return

    dest = await db.get_destination(dest_id)
    if not dest or int(dest["user_id"]) != user_id:
        await callback.answer("این مقصد مال تو نیست!", show_alert=True)
        return

    await callback.answer("⏳ دارم می‌فرستم...")
    chat_id = dest["chat_id"]
    caption = f"🎬 {KIND_LABEL[pending.kind]}"
    try:
        if pending.kind == "note":
            await callback.bot.send_video_note(
                chat_id=int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id,
                video_note=pending.file_id,
            )
        else:
            await callback.bot.send_video(
                chat_id=int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id,
                video=pending.file_id,
                caption=caption,
            )
    except TelegramForbiddenError:
        try:
            await callback.message.edit_text(
                "❌ نتونستم بفرستم! به احتمال زیاد ربات از کانال/گروه حذف شده "
                "یا دسترسی نداره.\n\nربات رو اونجا <b>ادمین</b> کن و دوباره تلاش کن."
            )
        except TelegramBadRequest:
            pass
        return
    except TelegramBadRequest as exc:
        log.warning("send to dest failed: %s", exc)
        try:
            await callback.message.edit_text(
                "❌ ارسال ناموفق بود. مطمئن شو ربات توی اون کانال/گروه عضوه "
                "و دسترسی ارسال پیام داره، بعد دوباره تلاش کن."
            )
        except TelegramBadRequest:
            pass
        return
    except Exception:  # noqa: BLE001
        log.exception("unexpected send-to-dest error")
        try:
            await callback.message.edit_text("❌ خطای غیرمنتظره؛ دوباره تلاش کن.")
        except TelegramBadRequest:
            pass
        return

    try:
        await callback.message.edit_text(
            f"✅ فرستادمش توی «{dest['title']}»! 🎉"
        )
    except TelegramBadRequest:
        pass

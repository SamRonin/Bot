"""Destination (channel/group) management + add-destination FSM flow."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import settings
from database import db
from keyboards.main import (
    ALL_BUTTONS,
    BTN_CANCEL,
    cancel_keyboard,
    destinations_manage_keyboard,
    main_menu,
)

log = logging.getLogger(__name__)
router = Router()


class DestStates(StatesGroup):
    waiting_target = State()


ADD_HELP = (
    "➕ <b>افزودن مقصد جدید</b>\n\n"
    "اول مطمئن شو ربات توی کانال/گروهت <b>ادمین</b> شده ✅\n"
    "(برای کانال، دسترسی «ارسال پیام» لازمه)\n\n"
    "بعد یکی از اینا رو بفرست:\n"
    "1️⃣ یه پیام از اون کانال/گروه <b>فوروارد</b> کن اینجا\n"
    "2️⃣ آیدی عددی (مثل <code>-100123...</code>) یا یوزرنیم (مثل <code>@mychannel</code>) رو بفرست\n\n"
    "برای انصراف «❌ انصراف» رو بزن."
)


async def show_destinations(message: Message) -> None:
    dests = await db.list_destinations(message.from_user.id)
    if not dests:
        await message.answer(
            "📢 هنوز هیچ مقصدی ثبت نکردی.\n\n"
            "ربات رو توی کانال/گروهت ادمین کن، بعد با دکمه زیر اضافش کن 👇",
            reply_markup=destinations_manage_keyboard([]),
        )
        return
    lines = ["📢 <b>مقصدهای تو:</b>\n"]
    for d in dests:
        star = "⭐ پیش‌فرض" if d.get("is_default") else "•"
        kind = {"channel": "کانال", "group": "گروه", "supergroup": "سوپرگروه"}.get(
            d.get("kind") or "", d.get("kind") or "")
        lines.append(f"{star} <b>{d['title']}</b> ({kind})")
    lines.append("\nبا دکمه‌ها پیش‌فرض رو عوض یا حذف کن 👇")
    await message.answer(
        "\n".join(lines),
        reply_markup=destinations_manage_keyboard(dests),
    )


@router.message(Command("destinations"))
async def cmd_destinations(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_destinations(message)


@router.callback_query(F.data == "dest:add")
async def dest_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(DestStates.waiting_target)
    try:
        await callback.message.edit_text(ADD_HELP)
    except TelegramBadRequest:
        pass
    await callback.message.answer("👇 منتظرم...", reply_markup=cancel_keyboard())


@router.callback_query(F.data == "dest:close")
async def dest_close(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("dest:del:"))
async def dest_delete(callback: CallbackQuery) -> None:
    try:
        dest_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("نامعتبر!", show_alert=True)
        return
    ok = await db.delete_destination(dest_id, callback.from_user.id)
    await callback.answer("🗑 حذف شد!" if ok else "پیداش نکردم!")
    dests = await db.list_destinations(callback.from_user.id)
    text = "📢 مقصدها:\n(⭐ = پیش‌فرض)" if dests else "📢 هیچ مقصدی نمونده."
    try:
        await callback.message.edit_text(
            text, reply_markup=destinations_manage_keyboard(dests)
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("dest:def:"))
async def dest_default(callback: CallbackQuery) -> None:
    try:
        dest_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("نامعتبر!", show_alert=True)
        return
    ok = await db.set_default_destination(dest_id, callback.from_user.id)
    await callback.answer("⭐ شد پیش‌فرض!" if ok else "پیداش نکردم!")
    dests = await db.list_destinations(callback.from_user.id)
    try:
        await callback.message.edit_text(
            "📢 مقصدها:\n(⭐ = پیش‌فرض)",
            reply_markup=destinations_manage_keyboard(dests),
        )
    except TelegramBadRequest:
        pass


def _extract_forwarded_chat(message: Message) -> tuple[str, str, str] | None:
    """Return (chat_id, title, kind) if the message was forwarded from a
    channel/group, supporting both old and new Telegram forward fields."""
    chat = getattr(message, "forward_from_chat", None)
    if chat is not None:
        return str(chat.id), chat.title or chat.username or str(chat.id), chat.type
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "type", "") == "channel":
        inner = getattr(origin, "chat", None)
        if inner is not None:
            return str(inner.id), inner.title or str(inner.id), inner.type
    return None


@router.message(DestStates.waiting_target)
async def dest_add_receive(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    # Let menu buttons / commands escape this state instead of being eaten.
    if message.text and (
        message.text.strip() in ALL_BUTTONS or message.text.startswith("/")
    ):
        await state.clear()
        if message.text.strip() == BTN_CANCEL:
            await message.answer(
                "باشه، لغو شد ✅",
                reply_markup=main_menu(settings.is_admin(user_id)),
            )
        else:
            await message.answer(
                "از حالت افزودن مقصد خارج شدی؛ دوباره بزن 👆",
                reply_markup=main_menu(settings.is_admin(user_id)),
            )
        return
    target: str | None = None
    title_hint: str | None = None

    forwarded = _extract_forwarded_chat(message)
    if forwarded:
        target, title_hint, _ = forwarded
    elif message.text:
        target = message.text.strip()
        if target.startswith("https://t.me/"):
            target = "@" + target.split("https://t.me/")[1].split("/")[0]
    else:
        await message.answer("🤔 یه پیام فوروارد کن یا آیدی/یوزرنیم بفرست.")
        return

    # 1) resolve the chat
    try:
        chat_id_arg: int | str = (
            int(target) if target.lstrip("-").isdigit() else target
        )
        chat = await message.bot.get_chat(chat_id_arg)
    except Exception:
        await message.answer(
            "❌ پیداش نکردم! مطمئن شو آیدی/یوزرنیم درسته و ربات توش عضوه."
        )
        return

    if chat.type not in ("channel", "group", "supergroup"):
        await message.answer("❌ فقط کانال و گروه قابل قبوله!")
        return

    # 2) verify the bot is inside (and can post in channels)
    try:
        me = await message.bot.get_me()
        member = await message.bot.get_chat_member(chat.id, me.id)
    except Exception:
        await message.answer(
            "❌ ربات توی اون کانال/گروه نیست! اول <b>ادمینش</b> کن بعد دوباره تلاش کن."
        )
        return

    if chat.type == "channel" and member.status not in ("administrator", "creator"):
        await message.answer(
            "❌ ربات باید توی کانال <b>ادمین</b> باشه (با دسترسی ارسال پیام)!"
        )
        return
    if member.status in ("left", "kicked"):
        await message.answer("❌ ربات از اونجا حذف/بن شده!")
        return

    title = chat.title or title_hint or str(chat.id)
    ok, err = await db.add_destination(user_id, str(chat.id), title, chat.type)
    await state.clear()
    if ok:
        await message.answer(
            f"✅ «{title}» اضافه شد!\n\n"
            "از این به بعد بعد از هر تبدیل می‌تونی با یه کلیک بفرستیش اونجا 📢",
            reply_markup=main_menu(settings.is_admin(user_id)),
        )
    else:
        log.error("add destination failed: %s", err)
        await message.answer(
            "❌ خطا در ذخیره مقصد؛ دوباره تلاش کن.",
            reply_markup=main_menu(settings.is_admin(user_id)),
        )

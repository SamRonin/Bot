"""Help, menu buttons, cancel, and fallback handlers."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from database import db
from keyboards.main import (
    BTN_ADMIN,
    BTN_AI,
    BTN_CANCEL,
    BTN_CONVERT,
    BTN_DESTS,
    BTN_HELP,
    BTN_PRO,
    ai_menu_keyboard,
    main_menu,
)
from utils.helpers import fa_num, safe_format

router = Router()
fallback_router = Router()  # registered LAST in bot.py


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    need = await db.get_int_setting("invites_for_pro", settings.invites_for_pro)
    days = await db.get_int_setting("pro_days", settings.pro_days)
    template = await db.get_setting("help_text", "ℹ️ راهنما")
    await message.answer(
        safe_format(template, need=fa_num(need), days=fa_num(days))
    )


@router.message(F.text == BTN_CONVERT)
async def btn_convert(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🎬 فقط کافیه ویدیوت رو همین‌جا بفرستی:\n\n"
        "🎥 <b>ویدیو معمولی</b> → تبدیل به <b>ویدیو مسیج گرد</b> ⭕️\n"
        "⭕️ <b>ویدیو مسیج گرد</b> → تبدیل به <b>ویدیو معمولی</b> 🎥\n\n"
        "بعدش می‌تونی نتیجه رو بفرستی توی کانال یا گروهت 📢"
    )


@router.message(F.text == BTN_CANCEL)
async def btn_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "باشه، لغو شد ✅",
        reply_markup=main_menu(settings.is_admin(message.from_user.id)),
    )


@router.message(F.text == BTN_AI)
async def btn_ai(message: Message, state: FSMContext) -> None:
    await state.clear()
    from handlers.ai_support import AI_MODE_DESCRIPTIONS  # local import, no cycle

    await message.answer(AI_MODE_DESCRIPTIONS, reply_markup=ai_menu_keyboard())


@router.message(F.text == BTN_DESTS)
async def btn_dests(message: Message, state: FSMContext) -> None:
    await state.clear()
    from handlers.destinations import show_destinations

    await show_destinations(message)


@router.message(F.text == BTN_PRO)
async def btn_pro(message: Message, state: FSMContext) -> None:
    await state.clear()
    from handlers.pro import show_pro

    await show_pro(message)


@router.message(F.text == BTN_ADMIN)
async def btn_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    from handlers.admin import show_admin_menu

    await show_admin_menu(message)


# NOTE: this lives on `fallback_router`, registered LAST in bot.py, so every
# specific router (including FSM states) gets first shot at the message.
@fallback_router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return  # another FSM handler owns this message; stay silent
    await message.answer(
        "🤔 متوجه نشدم! از دکمه‌های زیر استفاده کن یا /help رو بزن."
    )

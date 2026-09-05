"""AI support chat (Prexzy Gemini) + extras: summary, translation, general QA."""
from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import settings
from database import db
from keyboards.main import (
    ALL_BUTTONS,
    BTN_CANCEL,
    ai_exit_keyboard,
    ai_menu_keyboard,
    main_menu,
)
from services.ai_client import (
    AIError,
    ask_ai,
    summary_prompt,
    support_prompt,
    translate_prompt,
)
from utils.helpers import fa_num, split_text
from utils.store import ai_cooldowns

log = logging.getLogger(__name__)
router = Router()

AI_MODE_DESCRIPTIONS = (
    "💬 <b>پشتیبان هوشمند</b>\n\n"
    "یکی رو انتخاب کن، بعد سوالت یا متنت رو بفرست:\n"
    "💬 <b>چت پشتیبانی</b> — هر سوالی درباره ربات داری بپرس\n"
    "✨ <b>خلاصه‌سازی</b> — متنت رو خلاصه می‌کنم\n"
    "🌍 <b>ترجمه</b> — فارسی ⇄ انگلیسی"
)

MODE_NAMES = {
    "chat": "💬 چت پشتیبانی",
    "summary": "✨ خلاصه‌سازی",
    "trfa": "🌍 ترجمه به فارسی",
    "tren": "🌍 ترجمه به انگلیسی",
}

MODE_HINTS = {
    "chat": "💬 حالت <b>چت پشتیبانی</b> فعال شد!\nهر سوالی درباره ربات (یا هر چیز دیگه) داری بپرس 👇",
    "summary": "✨ حالت <b>خلاصه‌سازی</b> فعال شد!\nمتنی که می‌خوای خلاصه بشه رو بفرست 👇",
    "trfa": "🌍 حالت <b>ترجمه به فارسی</b> فعال شد!\nمتن انگلیسیت رو بفرست 👇",
    "tren": "🌍 حالت <b>ترجمه به انگلیسی</b> فعال شد!\nمتن فارسیت رو بفرست 👇",
}

AI_COOLDOWN_SECONDS = 3


class AiStates(StatesGroup):
    chatting = State()


@router.message(Command("ai"))
@router.message(Command("ask"))
async def cmd_ai(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(AI_MODE_DESCRIPTIONS, reply_markup=ai_menu_keyboard())


@router.callback_query(F.data.startswith("ai:mode:"))
async def ai_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":")[2]
    if mode not in MODE_NAMES:
        await callback.answer("نامعتبر!", show_alert=True)
        return
    await state.set_state(AiStates.chatting)
    await state.update_data(mode=mode)
    await callback.answer()
    try:
        await callback.message.edit_text(
            MODE_HINTS[mode], reply_markup=ai_exit_keyboard()
        )
    except TelegramBadRequest:
        await callback.message.answer(
            MODE_HINTS[mode], reply_markup=ai_exit_keyboard()
        )


@router.callback_query(F.data == "ai:exit")
async def ai_exit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("خارج شدی ✅")
    try:
        await callback.message.edit_text(
            "باشه! هر وقت کاری داشتی، «💬 پشتیبانی هوشمند» در خدمته 😊"
        )
    except TelegramBadRequest:
        pass


@router.message(StateFilter(AiStates.chatting), F.text)
async def ai_chat(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    # Let menu buttons / commands escape AI mode instead of being eaten.
    if message.text.strip() in ALL_BUTTONS or message.text.startswith("/"):
        await state.clear()
        if message.text.strip() == BTN_CANCEL:
            await message.answer(
                "از گفتگو خارج شدی ✅",
                reply_markup=main_menu(settings.is_admin(user_id)),
            )
        else:
            await message.answer(
                "از حالت هوش مصنوعی خارج شدی؛ دوباره بزن 👆",
                reply_markup=main_menu(settings.is_admin(user_id)),
            )
        return
    data = await state.get_data()
    mode = data.get("mode", "chat")

    # anti-spam cooldown
    now = time.monotonic()
    last = ai_cooldowns.get(user_id, 0)
    if now - last < AI_COOLDOWN_SECONDS:
        await message.answer("⏳ یه لحظه صبر کن بعد بفرست!")
        return
    ai_cooldowns[user_id] = now

    # daily AI quota
    is_pro = await db.is_pro(user_id)
    if is_pro:
        quota = await db.get_int_setting("ai_pro_daily", settings.ai_pro_daily)
    else:
        quota = await db.get_int_setting("ai_free_daily", settings.ai_free_daily)
    used = await db.today_ai(user_id)
    if used >= quota:
        await message.answer(
            "😔 سهمیه هوش مصنوعی امروزت تموم شد!\n"
            f"({fa_num(used)}/{fa_num(quota)}) — فردا برمی‌گرده 🔄\n"
            + ("⭐️ با پرو سهمیه‌ت خیلی بیشتر می‌شه!" if not is_pro else "")
        )
        return

    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("یه کم بیشتر بنویس 😊")
        return

    if mode == "summary":
        prompt = summary_prompt(text)
    elif mode == "trfa":
        prompt = translate_prompt(text, "fa")
    elif mode == "tren":
        prompt = translate_prompt(text, "en")
    else:
        prompt = support_prompt(text)

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        answer = await ask_ai(prompt, user_id=user_id, _tag=mode)
    except AIError as exc:
        await message.answer(str(exc))
        return
    except Exception:  # noqa: BLE001
        log.exception("AI handler error")
        await message.answer("❌ خطای غیرمنتظره؛ دوباره تلاش کن.")
        return

    await db.log_ai(user_id)
    left = max(0, quota - used - 1)
    footer = f"\n\n🔋 سهمیه امروز: {fa_num(left)} مونده"
    for i, chunk in enumerate(split_text(answer)):
        if i == 0:
            await message.answer(chunk + (footer if len(split_text(answer)) == 1 else ""))
        else:
            await message.answer(chunk)
    if len(split_text(answer)) > 1:
        await message.answer(f"🔋 سهمیه امروز: {fa_num(left)} مونده")


# Videos fall through to the converter even mid-chat (convert router is next).
@router.message(StateFilter(AiStates.chatting), ~F.video, ~F.video_note)
async def ai_chat_non_text(message: Message) -> None:
    await message.answer("📝 لطفاً فقط متن بفرست (ویدیو رو بفرست تا تبدیلش کنم 🎬).")

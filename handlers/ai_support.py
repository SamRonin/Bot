"""AI support chat (Prexzy Gemini) + extras: summary, translation, general QA."""
from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, F, Router
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

# Sent the moment the AI starts working, so the user never thinks the bot froze.
THINKING_TEXT = "⏳ دارم فکر می‌کنم و جوابت رو می‌نویسم… یه لحظه صبر کن 🤖"


class AiStates(StatesGroup):
    chatting = State()


async def _typing_loop(bot: Bot, chat_id: int, stop: asyncio.Event) -> None:
    """Keep Telegram's «typing…» indicator alive while the AI is working."""
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001 - the indicator is cosmetic
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
        except asyncio.TimeoutError:
            continue


async def _safe_edit(msg: Message, text: str) -> bool:
    """edit_text that tolerates «message not found / not modified»."""
    try:
        await msg.edit_text(text)
        return True
    except TelegramBadRequest:
        return False
    except Exception:  # noqa: BLE001
        log.warning("Could not edit the thinking message", exc_info=True)
        return False


async def _safe_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass


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

    # Tell the user we're working — the answer can take a while.
    thinking = await message.answer(THINKING_TEXT)

    stop = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_loop(message.bot, message.chat.id, stop)
    )
    try:
        try:
            answer = await ask_ai(prompt, user_id=user_id, _tag=mode)
        except AIError as exc:
            if not await _safe_edit(thinking, str(exc)):
                await message.answer(str(exc))
            return
        except Exception:  # noqa: BLE001
            log.exception("AI handler error")
            if not await _safe_edit(thinking, "❌ خطای غیرمنتظره؛ دوباره تلاش کن."):
                await message.answer("❌ خطای غیرمنتظره؛ دوباره تلاش کن.")
            return

        await db.log_ai(user_id)
        left = max(0, quota - used - 1)
        footer = f"\n\n🔋 سهمیه امروز: {fa_num(left)} مونده"
        # The bot speaks HTML — neutralise markup coming from the model so a
        # stray "<" can never make Telegram reject the whole message.
        # (3800 keeps escaped chunks safely under Telegram's 4096 limit.)
        chunks = split_text(html.escape(answer), chunk=3800)
        if len(chunks) == 1:
            # Reuse the «thinking» bubble as the answer (less spam).
            if not await _safe_edit(thinking, chunks[0] + footer):
                await message.answer(chunks[0] + footer)
        else:
            last_idx = len(chunks) - 1
            for i, chunk in enumerate(chunks):
                await message.answer(chunk + (footer if i == last_idx else ""))
            await _safe_delete(thinking)
    finally:
        stop.set()
        typing_task.cancel()


# Videos fall through to the converter even mid-chat (convert router is next).
@router.message(StateFilter(AiStates.chatting), ~F.video, ~F.video_note)
async def ai_chat_non_text(message: Message) -> None:
    await message.answer("📝 لطفاً فقط متن بفرست (ویدیو رو بفرست تا تبدیلش کنم 🎬).")

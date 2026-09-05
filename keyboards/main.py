"""Reply + inline keyboards (all Persian labels)."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ---- reply button captions (also used for F.text routing) ----
BTN_CONVERT = "🎬 تبدیل ویدیو"
BTN_DESTS = "📢 مقصدها"
BTN_PRO = "⭐ حساب پرو"
BTN_AI = "💬 پشتیبانی هوشمند"
BTN_HELP = "ℹ️ راهنما"
BTN_ADMIN = "🛠 پنل ادمین"
BTN_CANCEL = "❌ انصراف"

ALL_BUTTONS = {
    BTN_CONVERT, BTN_DESTS, BTN_PRO, BTN_AI, BTN_HELP, BTN_ADMIN, BTN_CANCEL,
}


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_CONVERT)
    builder.button(text=BTN_DESTS)
    builder.button(text=BTN_PRO)
    builder.button(text=BTN_AI)
    builder.button(text=BTN_HELP)
    if is_admin:
        builder.button(text=BTN_ADMIN)
    builder.adjust(2, 2, 1 if not is_admin else 2)
    return builder.as_markup(resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_CANCEL)
    return builder.as_markup(resize_keyboard=True)


def ask_send_keyboard() -> InlineKeyboardMarkup:
    """«می‌خوای برات بفرستمش توی کانال یا گروه؟»"""
    builder = InlineKeyboardBuilder()
    builder.button(text="آره، بفرست ✅", callback_data="conv:yes")
    builder.button(text="نه، ممنون ❌", callback_data="conv:no")
    builder.adjust(2)
    return builder.as_markup()


def destinations_pick_keyboard(dests: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for d in dests:
        star = "⭐ " if d.get("is_default") else ""
        label = f"{star}{d.get('title', '?')}"[:50]
        builder.button(text=label, callback_data=f"dest:pick:{d['id']}")
    builder.button(text="➕ افزودن مقصد جدید", callback_data="dest:add")
    builder.button(text="❌ بستن", callback_data="dest:close")
    builder.adjust(1)
    return builder.as_markup()


def destinations_manage_keyboard(dests: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for d in dests:
        star = "⭐ " if d.get("is_default") else ""
        base = f"{star}{d.get('title', '?')}"[:40]
        builder.button(text=f"⭐ پیش‌فرض: {base}", callback_data=f"dest:def:{d['id']}")
        builder.button(text=f"🗑 حذف: {base}", callback_data=f"dest:del:{d['id']}")
    builder.button(text="➕ افزودن مقصد", callback_data="dest:add")
    builder.button(text="❌ بستن", callback_data="dest:close")
    builder.adjust(1)
    return builder.as_markup()


def ai_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 چت با پشتیبان هوشمند", callback_data="ai:mode:chat")
    builder.button(text="✨ خلاصه‌سازی متن", callback_data="ai:mode:summary")
    builder.button(text="🌍 ترجمه به فارسی", callback_data="ai:mode:trfa")
    builder.button(text="🌍 ترجمه به انگلیسی", callback_data="ai:mode:tren")
    builder.button(text="❌ خروج", callback_data="ai:exit")
    builder.adjust(1)
    return builder.as_markup()


def ai_exit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ پایان گفتگو", callback_data="ai:exit")
    return builder.as_markup()


def promo_keyboard() -> InlineKeyboardMarkup:
    """Glass button under the post-conversion promo message (user side)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 دریافت راهنما", callback_data="promo:help")
    return builder.as_markup()


def promo_admin_keyboard(has_photo: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 تغییر متن پیام (بعد از تبدیل)", callback_data="promo:set:text")
    builder.button(text="🖼 افزودن/تغییر عکس راهنما", callback_data="promo:set:photo")
    builder.button(text="✏️ تغییر کپشن عکس راهنما", callback_data="promo:set:caption")
    if has_photo:
        builder.button(text="🗑 حذف عکس", callback_data="promo:del:photo")
    builder.button(text="👀 پیش‌نمایش", callback_data="promo:preview")
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 آمار کلی", callback_data="adm:stats")
    builder.button(text="📣 پیام همگانی", callback_data="adm:broadcast")
    builder.button(text="👥 مدیریت کاربران", callback_data="adm:users")
    builder.button(text="⚙️ محدودیت‌ها", callback_data="adm:limits")
    builder.button(text="📝 لاگ تبدیل‌ها", callback_data="adm:logs")
    builder.button(text="📢 کانال‌های متصل", callback_data="adm:channels")
    builder.button(text="💬 متن‌های ربات", callback_data="adm:texts")
    builder.button(text="🖼 پیام تبلیغ (عکس و دکمه)", callback_data="adm:promo")
    builder.button(text="❌ بستن", callback_data="adm:close")
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def back_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    return builder.as_markup()


def broadcast_mode_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 کپی (بدون فوروارد)", callback_data="bc:mode:copy")
    builder.button(text="↪️ فوروارد (با بنر فوروارد)", callback_data="bc:mode:forward")
    builder.button(text="❌ انصراف", callback_data="bc:cancel")
    builder.adjust(1)
    return builder.as_markup()


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ بله، ارسال کن", callback_data="bc:send")
    builder.button(text="❌ انصراف", callback_data="bc:cancel")
    builder.adjust(2)
    return builder.as_markup()


def limits_keyboard(current: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🎬 سقف روزانه رایگان: {current['free_daily_limit']}",
        callback_data="lim:set:free_daily_limit",
    )
    builder.button(
        text=f"⭐ سقف روزانه پرو: {current['pro_daily_limit']}",
        callback_data="lim:set:pro_daily_limit",
    )
    builder.button(
        text=f"💬 سقف هوش مصنوعی رایگان: {current['ai_free_daily']}",
        callback_data="lim:set:ai_free_daily",
    )
    builder.button(
        text=f"🤖 سقف هوش مصنوعی پرو: {current['ai_pro_daily']}",
        callback_data="lim:set:ai_pro_daily",
    )
    builder.button(
        text=f"📦 حداکثر حجم رایگان: {current['free_max_mb']}MB",
        callback_data="lim:set:free_max_mb",
    )
    builder.button(
        text=f"📦 حداکثر حجم پرو: {current['pro_max_mb']}MB",
        callback_data="lim:set:pro_max_mb",
    )
    builder.button(
        text=f"👥 دعوت لازم برای پرو: {current['invites_for_pro']}",
        callback_data="lim:set:invites_for_pro",
    )
    builder.button(
        text=f"📅 روزهای پرو: {current['pro_days']}",
        callback_data="lim:set:pro_days",
    )
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def user_actions_keyboard(user_id: int, is_banned: bool, is_pro: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ ۱۵ روز پرو", callback_data=f"usr:pro:{user_id}")
    if is_pro:
        builder.button(text="➖ لغو پرو", callback_data=f"usr:unpro:{user_id}")
    if is_banned:
        builder.button(text="✅ رفع بن", callback_data=f"usr:unban:{user_id}")
    else:
        builder.button(text="🚫 بن", callback_data=f"usr:ban:{user_id}")
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    builder.adjust(2)
    return builder.as_markup()


def texts_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👋 متن استارت", callback_data="txt:edit:start_text")
    builder.button(text="ℹ️ متن راهنما", callback_data="txt:edit:help_text")
    builder.button(text="🔙 بازگشت به پنل", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def share_referral_keyboard(ref_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📤 اشتراک‌گذاری لینک دعوت",
        url=f"https://t.me/share/url?url={ref_link}&text="
            "🎬 با این ربات ویدیو رو به ویدیو مسیج گرد تبدیل کن!",
    )
    return builder.as_markup()

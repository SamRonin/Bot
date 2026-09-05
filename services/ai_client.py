"""Client for the free key-less Prexzy AI API.

Primary endpoint:  GET/POST {base}/ai/gemini?prompt=...&session_id=...
Docs: https://docs.prexzyapis.com/  (section: Artificial Intelligence)

Success shape:
    {"status": true, "response": "<answer>", "session_id": "...", ...}

If Gemini is down we transparently fall back to Qwen, then to ChatEx.
"""
from __future__ import annotations

import logging
import uuid

import aiohttp

from config import settings

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "TelegramVideoConverterBot/1.0"}


class AIError(Exception):
    """User-safe AI failure."""


def _extract_text(data: object) -> str | None:
    """Best-effort answer extraction across the various Prexzy AI shapes."""
    if not isinstance(data, dict):
        return None
    for key in ("response", "result", "answer", "text", "reply", "output"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            # Skip generic status messages some endpoints return as "message".
            return value.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_text(nested)
    return None


async def _call(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: int,
) -> dict:
    async with session.request(
        method, url, params=params, json=payload,
        headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype:
            body = (await resp.text())[:300]
            raise AIError(f"پاسخ نامعتبر از سرویس هوش مصنوعی ({resp.status}). {body}")
        data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise AIError("پاسخ نامعتبر از سرویس هوش مصنوعی.")
        return data


async def _ask_gemini(
    session: aiohttp.ClientSession, prompt: str, session_id: str, timeout: int
) -> str:
    url = f"{settings.prexzy_base}/ai/gemini"
    # Long prompts go via POST body to avoid URL-length limits.
    if len(prompt.encode("utf-8")) > 1500:
        data = await _call(
            session, "POST", url,
            payload={"prompt": prompt, "session_id": session_id},
            timeout=timeout,
        )
    else:
        data = await _call(
            session, "GET", url,
            params={"prompt": prompt, "session_id": session_id},
            timeout=timeout,
        )
    if data.get("status") is True:
        text = _extract_text(data)
        if text:
            return text
    raise AIError(str(data.get("error") or "سرویس Gemini جواب نداد."))


async def _ask_qwen(
    session: aiohttp.ClientSession, prompt: str, timeout: int
) -> str:
    url = f"{settings.prexzy_base}/ai/qwen"
    if len(prompt.encode("utf-8")) > 1500:
        data = await _call(
            session, "POST", url, payload={"prompt": prompt}, timeout=timeout
        )
    else:
        data = await _call(
            session, "GET", url, params={"prompt": prompt}, timeout=timeout
        )
    text = _extract_text(data)
    if text:
        return text
    raise AIError(str(data.get("error") or "سرویس جایگزین جواب نداد."))


async def ask_ai(
    prompt: str, user_id: int = 0, timeout: int = 60,
    _tag: str = "",
) -> str:
    """Ask the AI; tries Gemini first, then fallbacks. Returns answer text."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise AIError("متن خالیه؛ یه چیزی بنویس 😊")
    if len(prompt) > 4000:  # keep requests light for the free API
        prompt = prompt[:4000]
    session_id = f"tg{user_id}" if user_id else f"sess-{uuid.uuid4().hex[:8]}"
    errors: list[str] = []
    async with aiohttp.ClientSession() as http:
        for name, coro in (
            ("gemini", _ask_gemini(http, prompt, session_id, timeout)),
            ("qwen", _ask_qwen(http, prompt, timeout)),
        ):
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 - fall through to next
                log.warning("AI backend %s failed%s: %s", name,
                            f" [{_tag}]" if _tag else "", exc)
                errors.append(f"{name}: {exc}")
    raise AIError(
        "😔 الان نمی‌تونم به هوش مصنوعی وصل بشم. چند دقیقه دیگه دوباره تلاش کن."
    )


# ---------------------------------------------------------------------------
# Prompt builders (Persian-first UX)
# ---------------------------------------------------------------------------

SUPPORT_SYSTEM = (
    "تو «پشتیبان هوشمند» یک ربات تلگرام فارسی به نام «ربات تبدیل ویدیو» هستی. "
    "قابلیت‌های ربات: ۱) تبدیل ویدیوی معمولی به ویدیو مسیج گرد تلگرام (حداکثر ۶۰ ثانیه، بقیه‌اش برش می‌خوره) "
    "۲) تبدیل ویدیو مسیج گرد به ویدیوی معمولی "
    "۳) بعد از هر تبدیل، کاربر می‌تونه نتیجه رو با یک کلیک بفرسته توی کانال یا گروه خودش (اول باید ربات رو اونجا ادمین کنه و از بخش «مقصدها» اضافش کنه) "
    "۴) سهمیه رایگان: ۳ تبدیل در روز (قابل تغییر توسط مدیر) "
    "۵) حساب پرو: با دعوت ۲ نفر کاملاً جدید (که قبلاً ربات رو استارت نکردن) از طریق لینک دعوت، ۱۵ روز پرو می‌گیره؛ بعدش با ۲ نفر جدید دیگه تمدید می‌شه و این چرخه تا ابد ادامه داره؛ در حالت پرو محدودیت روزانه خیلی بیشتر و سقف حجم ویدیو بالاتر می‌ره "
    "۶) همین‌جا پشتیبانی هوشمند، خلاصه‌سازی متن و ترجمه هم داری. "
    "قوانین جواب دادن: همیشه فارسی، صمیمی و کوتاه جواب بده (حداکثر چند خط مگر اینکه لازم باشه)؛ "
    "از Markdown پیچیده استفاده نکن؛ اگه سوال درباره خود رباته دقیق راهنمایی کن؛ "
    "اگه سوال عمومی یا متفرقه‌ست هم مفید و کوتاه کمک کن.\n"
    "سوال کاربر: "
)


def support_prompt(user_text: str) -> str:
    return SUPPORT_SYSTEM + user_text.strip()


def summary_prompt(user_text: str) -> str:
    return (
        "متن زیر رو به فارسی، روان و خلاصه کن. فقط نکات مهم رو در چند خط "
        "(با بولت •) بیار، بدون مقدمه‌چینی اضافه:\n\n" + user_text.strip()
    )


def translate_prompt(user_text: str, target: str) -> str:
    if target == "en":
        return (
            "متن زیر رو به انگلیسی روان و طبیعی ترجمه کن. فقط خود ترجمه رو "
            "بنویس، بدون توضیح اضافه:\n\n" + user_text.strip()
        )
    return (
        "متن زیر رو به فارسی روان و طبیعی ترجمه کن. فقط خود ترجمه رو بنویس، "
        "بدون توضیح اضافه:\n\n" + user_text.strip()
    )

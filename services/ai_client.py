"""Client for the free key-less Prexzy AI API.

Primary endpoint:  GET/POST {base}/ai/gemini?prompt=...&session_id=...
Docs: https://docs.prexzyapis.com/  (section: Artificial Intelligence)

Success shape:
    {"status": true, "response": "<answer>", "session_id": "...", ...}

If Gemini is down we transparently fall back to Qwen, then to ChatEx.
"""
from __future__ import annotations

import logging
import re
import uuid

import aiohttp

from config import settings

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "TelegramVideoConverterBot/1.0"}


class AIError(Exception):
    """User-safe AI failure."""


# ---------------------------------------------------------------------------
# Answer cleanup — fixes the literal-escape bug
# ---------------------------------------------------------------------------
# Some models (Gemini through the free API above) occasionally write the
# Persian half-space (نیم‌فاصله, U+200C) — and other code points like bullets,
# quotes or line breaks — as *literal* escape text: "\u200c", "/u200c", "\n" …
# instead of the real characters. We repair that before the user reads it.
_U_ESCAPE_RE = re.compile(
    r"\\u([0-9a-fA-F]{4})"
    r"|(?<![A-Za-z0-9./])/u([0-9a-fA-F]{4})"
)


def _u_sub(match: re.Match[str]) -> str:
    code = int(match.group(1) or match.group(2), 16)
    if code in (0x09, 0x0A, 0x0D):
        return chr(code)
    if 0x20 <= code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
        return chr(code)
    return match.group(0)  # keep control chars / surrogates as-is


def clean_ai_text(text: str) -> str:
    """Turn literal \\uXXXX / /uXXXX escapes back into real characters."""
    text = text or ""
    had_escapes = bool(_U_ESCAPE_RE.search(text))
    # Two passes also fix doubly-escaped sequences (e.g. \\u200c).
    text = _U_ESCAPE_RE.sub(_u_sub, _U_ESCAPE_RE.sub(_u_sub, text))
    if had_escapes:
        # The model escaped the whole string, so its "\n"/"\t" are real too.
        text = text.replace("\\n", "\n").replace("\\t", "\t")
    # Drop stray C0 control characters that break Telegram's UTF-8 text.
    return "".join(ch for ch in text if ch in "\n\t\r" or ord(ch) >= 0x20)


# ---------------------------------------------------------------------------
# HTTP session reuse — keeps the AI call fast
# ---------------------------------------------------------------------------
# One shared ClientSession means the TCP/TLS connection to the AI API is
# reused across calls instead of re-handshaking for every question.
_http: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(headers=HEADERS)
    return _http


async def close_ai_http() -> None:
    """Close the shared session at bot shutdown (called from bot.py)."""
    global _http
    if _http is not None and not _http.closed:
        await _http.close()
    _http = None


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
    # sock_connect=8 → if the API host is unreachable we fail over to the
    # next backend in seconds instead of waiting out the whole timeout.
    async with session.request(
        method, url, params=params, json=payload,
        timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=8),
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
    http = _get_session()
    for name, coro in (
        ("gemini", _ask_gemini(http, prompt, session_id, timeout)),
        ("qwen", _ask_qwen(http, prompt, timeout)),
    ):
        try:
            return clean_ai_text(await coro)
        except Exception as exc:  # noqa: BLE001 - fall through to next
            log.warning("AI backend %s failed%s: %s", name,
                        f" [{_tag}]" if _tag else "", exc)
            errors.append(f"{name}: {exc}")
    log.error("All AI backends failed: %s", " | ".join(errors))
    raise AIError(
        "😔 الان نمی‌تونم به هوش مصنوعی وصل بشم. چند دقیقه دیگه دوباره تلاش کن."
    )


# ---------------------------------------------------------------------------
# Prompt builders (Persian-first UX)
# ---------------------------------------------------------------------------

# Written out explicitly so the model stops "cheating" with code escapes.
STYLE_RULES = (
    "قانون نگارش: متن رو کامل، روان و با کاراکترهای واقعی بنویس؛ نوشتن کد "
    "یونیکد یا فرمت فرار مثل \\u200c ، /u200c یا \\n کاملاً ممنوعه؛ برای "
    "نیم‌فاصله کاراکتر واقعی رو بزن (مثل: می‌نویسم) و از Markdown پیچیده "
    "استفاده نکن. "
)

SUPPORT_SYSTEM = (
    "تو «پشتیبان هوشمند» یک ربات تلگرام فارسی به نام «ربات تبدیل ویدیو» هستی. "
    "قابلیت‌های ربات: ۱) تبدیل ویدیوی معمولی به ویدیو مسیج گرد تلگرام (حداکثر ۶۰ ثانیه، بقیه‌اش برش می‌خوره) "
    "۲) تبدیل ویدیو مسیج گرد به ویدیوی معمولی "
    "۳) بعد از هر تبدیل، کاربر می‌تونه نتیجه رو با یک کلیک بفرسته توی کانال یا گروه خودش (اول باید ربات رو اونجا ادمین کنه و از بخش «مقصدها» اضافش کنه) "
    "۴) سهمیه رایگان: ۳ تبدیل در روز (قابل تغییر توسط مدیر) "
    "۵) حساب پرو: با دعوت ۲ نفر کاملاً جدید (که قبلاً ربات رو استارت نکردن) از طریق لینک دعوت، ۱۵ روز پرو می‌گیره؛ بعدش با ۲ نفر جدید دیگه تمدید می‌شه و این چرخه تا ابد ادامه داره؛ در حالت پرو محدودیت روزانه خیلی بیشتر و سقف حجم ویدیو بالاتر می‌ره "
    "۶) همین‌جا پشتیبانی هوشمند، خلاصه‌سازی متن و ترجمه هم داری. "
    "قوانین جواب دادن: همیشه فارسی، صمیمی و کوتاه جواب بده (حداکثر چند خط مگر اینکه لازم باشه)؛ "
    "اگه سوال درباره خود رباته دقیق راهنمایی کن؛ "
    "اگه سوال عمومی یا متفرقه‌ست هم مفید و کوتاه کمک کن.\n"
)


def support_prompt(user_text: str) -> str:
    return SUPPORT_SYSTEM + STYLE_RULES + "سوال کاربر: " + user_text.strip()


def summary_prompt(user_text: str) -> str:
    return (
        "متن زیر رو به فارسی، روان و خلاصه کن. فقط نکات مهم رو در چند خط "
        "(با بولت •) بیار، بدون مقدمه‌چینی اضافه.\n"
        + STYLE_RULES + "\n" + user_text.strip()
    )


def translate_prompt(user_text: str, target: str) -> str:
    if target == "en":
        return (
            "متن زیر رو به انگلیسی روان و طبیعی ترجمه کن. فقط خود ترجمه رو "
            "بنویس، بدون توضیح اضافه.\n"
            + STYLE_RULES + "\n" + user_text.strip()
        )
    return (
        "متن زیر رو به فارسی روان و طبیعی ترجمه کن. فقط خود ترجمه رو بنویس، "
        "بدون توضیح اضافه.\n"
        + STYLE_RULES + "\n" + user_text.strip()
    )

"""Regression tests for the referral deep-link.

The original bug: `handlers/start.py` did `from utils.store import bot_username`,
which copies the empty startup value into the handler module. When `bot.py`
later assigned `store.bot_username = me.username`, the handler kept its frozen
copy, so every referral link came out as `https://t.me/?start=ref_<id>` — with
no bot username, so the link did not open anything.

Run with:  python -m pytest tests/ -q      (or plain: python tests/test_referral_link.py)
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("BOT_TOKEN", "123:FAKE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.pop("BOT_USERNAME", None)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import settings  # noqa: E402
from handlers.start import (  # noqa: E402
    referral_link_for,
    referral_link_for_async,
)
from keyboards.main import share_referral_keyboard  # noqa: E402
from utils import store  # noqa: E402

USER_ID = 6087114819


def setup_function(_func=None) -> None:
    """Reset the shared runtime state between tests."""
    store.set_bot_username("")
    settings.bot_username = ""


def test_link_uses_username_set_at_startup() -> None:
    """The exact reported scenario: startup fills the store, handler reads it."""
    store.set_bot_username("MyCoolBot")
    assert referral_link_for(USER_ID) == f"https://t.me/MyCoolBot?start=ref_{USER_ID}"


def test_link_is_never_the_broken_usernameless_shape() -> None:
    """Guard against the precise regression that was reported."""
    store.set_bot_username("MyCoolBot")
    assert referral_link_for(USER_ID) != f"https://t.me/?start=ref_{USER_ID}"
    assert "t.me/?start=" not in referral_link_for(USER_ID)


def test_username_is_normalised() -> None:
    """A username stored with '@' or spaces must not corrupt the URL."""
    store.set_bot_username("  @MyCoolBot ")
    assert referral_link_for(USER_ID) == f"https://t.me/MyCoolBot?start=ref_{USER_ID}"


def test_env_fallback_when_startup_lookup_missing() -> None:
    """BOT_USERNAME covers the case where get_me() never populated the store."""
    settings.bot_username = "EnvBot"
    assert referral_link_for(USER_ID) == f"https://t.me/EnvBot?start=ref_{USER_ID}"


def test_async_helper_resolves_username_lazily() -> None:
    """If the username is unknown, the async helper asks the bot once."""

    class _FakeUser:
        username = "LazyBot"

    class _FakeBot:
        def __init__(self) -> None:
            self.calls = 0

        async def me(self) -> _FakeUser:
            self.calls += 1
            return _FakeUser()

    fake = _FakeBot()
    link = asyncio.run(referral_link_for_async(fake, USER_ID))

    assert link == f"https://t.me/LazyBot?start=ref_{USER_ID}"
    assert store.get_bot_username() == "LazyBot"  # cached for next time

    # Already known now -> no second network call.
    asyncio.run(referral_link_for_async(fake, USER_ID))
    assert fake.calls == 1


def test_share_button_percent_encodes_the_link() -> None:
    """'?' and '&' inside the link must not break the share URL's query."""
    store.set_bot_username("MyCoolBot")
    ref_link = referral_link_for(USER_ID)
    button = share_referral_keyboard(ref_link).inline_keyboard[0][0]

    inner = parse_qs(urlparse(button.url).query)["url"][0]
    assert inner == ref_link  # survives the round-trip intact

    payload = parse_qs(urlparse(inner).query)["start"][0]
    assert payload == f"ref_{USER_ID}"
    assert int(payload[4:]) == USER_ID  # what cmd_start parses back out


def test_no_module_reimports_the_frozen_value() -> None:
    """The import style that caused the bug must not come back."""
    found = subprocess.run(
        [
            "grep", "-rn", "from utils.store import bot_username",
            "--include=*.py", "--exclude-dir=tests", ".",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout.strip().splitlines()
    offenders = [
        line for line in found
        if not line.split(":", 2)[-1].lstrip().startswith("#")
    ]
    assert not offenders, f"frozen-value import reintroduced: {offenders}"


if __name__ == "__main__":  # allow running without pytest installed
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            setup_function()
            try:
                func()
                print(f"PASS | {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL | {name}: {exc}")
    print("\n" + ("ALL TESTS PASSED" if not failures else f"{failures} TEST(S) FAILED"))
    sys.exit(1 if failures else 0)

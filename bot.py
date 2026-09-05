"""Entrypoint: logging, DB init, router wiring, polling.

Run:  python bot.py   (BOT_TOKEN + ADMIN_IDS must be set, see .env.example)
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import settings
from database import db
from handlers import admin, ai_support, common, convert, destinations, pro, start
from middlewares import UserMiddleware
from services.converter import ffmpeg_available, ffprobe_available
from utils import store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)
# aiogram event logs are very noisy at INFO; keep warnings only.
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="شروع / لینک دعوت"),
    BotCommand(command="help", description="راهنما"),
    BotCommand(command="pro", description="حساب پرو و دعوت دوستان"),
    BotCommand(command="destinations", description="مدیریت کانال/گروه‌ها"),
    BotCommand(command="ai", description="پشتیبانی هوشمند"),
    BotCommand(command="admin", description="پنل ادمین"),
]


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set! Put it in .env (locally) or in Railway "
            "Variables (production). See .env.example"
        )
    if not settings.admin_ids:
        log.warning("ADMIN_IDS is empty — /admin will be inaccessible!")
    if not ffmpeg_available() or not ffprobe_available():
        log.warning("ffmpeg/ffprobe NOT found — conversions will fail!")

    await db.init()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    store.bot_username = me.username or ""
    log.info("Logged in as @%s (id=%s)", me.username, me.id)

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())

    # Order matters: specific routers first, generic text fallback last.
    dp.include_routers(
        start.router,
        pro.router,
        destinations.router,
        admin.router,
        common.router,
        ai_support.router,
        convert.router,
        common.fallback_router,
    )

    await bot.set_my_commands(COMMANDS)
    # Drop stale updates queued while offline (avoids surprise bursts).
    await bot.delete_webhook(drop_pending_updates=True)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id, "✅ ربات روشن شد و آماده‌ست! 🤖"
            )
        except Exception:
            pass  # admin blocked the bot — nothing to do

    log.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

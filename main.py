"""
🤖 رائد التداول الذكي — نقطة الدخول
مبني على NexusTrader (MIT) — Quantweb3
تطوير: فريق رائد التداول الذكي
"""

import sys
import os
from loguru import logger
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

from core.config import BOT_TOKEN, ADMIN_ID, RAED_VERSION
from core.database import db
from handlers.commands import (
    cmd_start, cmd_help, cmd_price, cmd_wallet, cmd_virtual,
    cmd_report, cmd_about, cmd_upgrade, cmd_security, cmd_admin,
    handle_callback, handle_text, unknown_command,
)

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="INFO", colorize=True)

async def on_startup(app: Application):
    await db.connect()
    logger.info("═" * 50)
    logger.info(f"  🤖 رائد التداول الذكي v{RAED_VERSION}")
    logger.info(f"  مبني على NexusTrader — MIT — Quantweb3")
    logger.info(f"  تطوير: فريق رائد التداول الذكي")
    logger.info("═" * 50)
    if ADMIN_ID:
        try:
            import datetime
            await app.bot.send_message(
                ADMIN_ID,
                f"🤖 رائد بدأ التشغيل!\n"
                f"الإصدار: {RAED_VERSION}\n"
                f"الوقت: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception:
            pass

async def on_shutdown(app: Application):
    await db.disconnect()
    logger.info("👋 رائد أوقف التشغيل")

def main():
    os.makedirs("logs", exist_ok=True)
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN غير موجود!")
        sys.exit(1)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("price",    cmd_price))
    app.add_handler(CommandHandler("wallet",   cmd_wallet))
    app.add_handler(CommandHandler("virtual",  cmd_virtual))
    app.add_handler(CommandHandler("report",   cmd_report))
    app.add_handler(CommandHandler("about",    cmd_about))
    app.add_handler(CommandHandler("upgrade",  cmd_upgrade))
    app.add_handler(CommandHandler("security", cmd_security))
    app.add_handler(CommandHandler("admin",    cmd_admin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info(f"🚀 رائد يبدأ التشغيل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

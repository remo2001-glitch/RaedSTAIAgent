"""
🤖 رائد التداول الذكي — نقطة الدخول الرئيسية
مبني على NexusTrader (MIT) — Quantweb3
تطوير: فريق رائد التداول الذكي
الإصدار: 1.0.0
"""

import asyncio
import sys
from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from core.config import BOT_TOKEN, ADMIN_ID, RAED_NAME, RAED_VERSION
from core.database import db
from handlers.commands import (
    cmd_start, cmd_help, cmd_price, cmd_wallet, cmd_virtual,
    cmd_report, cmd_about, cmd_upgrade, cmd_security, cmd_admin,
    handle_callback, handle_text, unknown_command,
)


# ── إعداد السجلات ────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="INFO",
    colorize=True,
)
logger.add(
    "logs/raed_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG",
)


async def on_startup(app: Application):
    """يُنفَّذ عند بدء تشغيل رائد"""
    await db.connect()
    logger.info(f"{'═' * 50}")
    logger.info(f"  {RAED_NAME} v{RAED_VERSION}")
    logger.info(f"  مبني على NexusTrader — MIT License — Quantweb3")
    logger.info(f"  تطوير: فريق رائد التداول الذكي")
    logger.info(f"{'═' * 50}")

    # إرسال إشعار للمشرف
    if ADMIN_ID:
        try:
            await app.bot.send_message(
                ADMIN_ID,
                f"🤖 رائد بدأ التشغيل!\n"
                f"الإصدار: {RAED_VERSION}\n"
                f"الوقت: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception:
            pass


async def on_shutdown(app: Application):
    """يُنفَّذ عند إيقاف رائد"""
    await db.disconnect()
    logger.info("👋 رائد أوقف التشغيل")


def build_app() -> Application:
    """بناء تطبيق تيليجرام مع جميع الـ handlers"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN غير موجود في متغيرات البيئة!")
        sys.exit(1)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # ── الأوامر الأساسية ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("price",    cmd_price))
    app.add_handler(CommandHandler("wallet",   cmd_wallet))
    app.add_handler(CommandHandler("virtual",  cmd_virtual))
    app.add_handler(CommandHandler("report",   cmd_report))
    app.add_handler(CommandHandler("about",    cmd_about))
    app.add_handler(CommandHandler("upgrade",  cmd_upgrade))
    app.add_handler(CommandHandler("security", cmd_security))

    # ── أوامر المشرف ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("admin",    cmd_admin))

    # ── Callback من الأزرار ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── الرسائل النصية ───────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # ── أوامر غير معروفة ─────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info(f"✅ تم تسجيل {len(app.handlers[0])} handler")
    return app


def main():
    import os
    os.makedirs("logs", exist_ok=True)

    logger.info("🚀 جاري تشغيل رائد التداول الذكي...")
    app = build_app()
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

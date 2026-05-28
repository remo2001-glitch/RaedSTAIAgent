"""
🤖 رائد — Institutional Balanced Crypto AI Trading Agent
main.py v2 — نقطة الدخول الرئيسية

الإصلاحات:
- تحقق من OWNER_CHAT_ID صحيح (رقمي وغير فارغ)
- معالجة أخطاء error_handler مع إرسال رسالة للمستخدم
- تقليل رسائل /start من ٣ إلى رسالة موحَّدة
- إضافة جميع الأوامر في build_app بشكل صحيح
- safety_monitor: إرسال تنبيه إذا اكتشف Kill Switch
"""

import asyncio
import logging
import os
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler
)
from telegram.constants import ParseMode

import handlers.analysis as analysis_handlers
import handlers.plan     as plan_handlers
import handlers.trading  as trading_handlers

from core.raed_engine import RaedEngine

# ── Logging ────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/raed.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# ── متغيرات البيئة ─────────────────────────────────────────────
BOT_CONFIG = {
    "BOT_TOKEN":           os.getenv("BOT_TOKEN", ""),
    "GROQ_API_KEY":        os.getenv("GROQ_API_KEY", ""),
    "EXCHANGE":            os.getenv("EXCHANGE", "bybit"),
    "EXCHANGE_API_KEY":    os.getenv("EXCHANGE_API_KEY", ""),
    "EXCHANGE_API_SECRET": os.getenv("EXCHANGE_API_SECRET", ""),
    "EXCHANGE_TESTNET":    os.getenv("EXCHANGE_TESTNET", "false").lower() == "true",
    "CRYPTOPANIC_KEY":     os.getenv("CRYPTOPANIC_KEY", ""),
    "ETHERSCAN_KEY":       os.getenv("ETHERSCAN_KEY", ""),
    "OWNER_CHAT_ID":       os.getenv("OWNER_CHAT_ID", ""),
    "PORTFOLIO_SIZE":      float(os.getenv("PORTFOLIO_SIZE", "10000")),
    "MAX_RISK_PER_TRADE":  float(os.getenv("MAX_RISK_PER_TRADE", "0.02")),
    "MAX_DAILY_LOSS":      float(os.getenv("MAX_DAILY_LOSS", "0.05")),
    "MAX_DRAWDOWN":        float(os.getenv("MAX_DRAWDOWN", "0.15")),
    "MAX_OPEN_POSITIONS":  int(os.getenv("MAX_OPEN_POSITIONS", "5")),
    "MAX_SINGLE_EXPOSURE": float(os.getenv("MAX_SINGLE_EXPOSURE", "0.10")),
    "MIN_CONFIDENCE":      float(os.getenv("MIN_CONFIDENCE", "0.65")),
}


# ════════════════════════════════════════════════════════════════
# أوامر رئيسية
# ════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state_manager import state_manager as _sm, TIERS
    user_id   = update.effective_user.id
    tier      = _sm.get_tier(user_id)
    tier_info = TIERS[tier]
    tier_name = tier_info["name"]
    coins     = tier_info["coins"]
    days_left = _sm.get_free_autotrade_days(user_id) if tier == "free" else 0

    # رسالة موحَّدة (بدلاً من ٣ رسائل منفصلة)
    cmds_text = _build_commands_text(user_id, tier, tier_name)

    scan_info = (
        "\n\n⏰ *المسح التلقائي — كل ٤ ساعات*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🌏 01:00 | 🌅 05:00 | 🌍 09:00\n"
        "📈 13:00 | 🗽 17:00 | 🌙 21:00 KSA\n"
        "• تحليل أفضل العملات تلقائياً\n"
        f"• تنبيه فوري عند فرصة ≥ {int(BOT_CONFIG['MIN_CONFIDENCE']*100)}%"
    )

    days_note = ""
    if tier == "free" and days_left > 0:
        days_note = f"\n⏰ تداول آلي مجاني: {days_left} يوم متبقٍ"

    welcome = (
        f"🤖 *مرحباً بك في رائد*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"باقتك: {tier_name} | حد العملات: {coins}"
        f"{days_note}\n\n"
        f"رائد وكيل تداول ذكي بالذكاء الاصطناعي.\n"
        f"يُحلل الأسواق ويُقدم إشارات مؤسسية.\n"
        f"{scan_info}"
    )

    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(cmds_text, parse_mode=ParseMode.MARKDOWN)


def _build_commands_text(user_id: int, tier: str, tier_name: str) -> str:
    from core.state_manager import state_manager as _sm

    sections = []

    basic = [
        "/quicksignal — تحليل أولي سريع مع نقاط الدخول والخروج",
        "/portfolio — استعراض المحفظة",
        "/trades — الصفقات السابقة والقائمة",
        "/live — التداول الحقيقي (ربط منصة)",
        "/autotrade — تداول تلقائي (تشغيل/إيقاف)",
        "/setportfolio — ضبط حجم المحفظة",
        "/upgrade — جدول الباقات والأسعار",
        "/about — عن رائد والدعم الفني",
    ]
    sections.append(("⚙️ *أوامر أساسية — للجميع*", basic))

    if _sm.can_use_command(user_id, "signal"):
        analysis = [
            "/signal — إشارة تداول شاملة ٥ مصادر",
            "/news — تحليل الأخبار بالذكاء الاصطناعي",
            "/regime — حالة السوق",
            "/backtest — اختبار تاريخي ٣ سنوات",
            "/events — الأحداث الماكرو القادمة",
        ]
        sections.append(("📊 *التحليل — فضي+*", analysis))

    if _sm.can_use_command(user_id, "analyze"):
        deep = [
            "/analyze — تحليل عميق بالذكاء الاصطناعي",
            "/liquidity — تحليل السيولة المتقدم",
            "/onchain — تحليل On-Chain",
            "/planweek — خطة أسبوعية",
            "/planmonth — خطة شهرية",
            "/drift — مراقبة دقة النموذج",
        ]
        sections.append(("🔬 *التحليل المتقدم — ذهبي+*", deep))

    if _sm.can_use_command(user_id, "chart"):
        premium = ["/chart — تحليل شارت بصري (أرفع صورة)"]
        sections.append(("💎 *ماسي حصراً*", premium))

    if tier == "admin":
        admin_cmds = [
            "/killswitch — إيقاف طارئ",
            "/stats — إحصائيات النظام",
            "/risk — Risk Engine",
            "/premium — إدارة الباقات",
        ]
        sections.append(("👑 *المدير*", admin_cmds))

    lines = [f"📋 *أوامرك — {tier_name}*", ""]
    for title, cmds in sections:
        lines.append(title)
        lines += cmds
        lines.append("")

    if tier == "free":
        lines += [
            "━━━━━━━━━━━━━━━━━━",
            "🔒 *أوامر إضافية بالترقية:*",
            "🥈 فضي — signal/news/regime/backtest",
            "🥇 ذهبي — تحليل عميق/تخطيط/سيولة",
            "💎 ماسي — شارت بصري/٣٠٠ عملة",
            "",
            "📊 /upgrade — لعرض الباقات والأسعار",
        ]

    return "\n".join(lines)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state_manager import state_manager as _sm
    user_id = update.effective_user.id
    tier    = _sm.get_tier(user_id)

    if tier == "admin":
        lines = [
            "🤖 *رائد — Institutional Balanced Crypto AI*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "🏗️ *المعمارية — ١٠ طبقات*",
            "١. Data Layer — CoinGecko · Binance · DeFiLlama",
            "٢. Data Validator",
            "٣. Regime Detector — Market Regime",
            "٤. Signal Layer + Strategy Router (٦ مدارس)",
            "٥. Risk Engine — Kelly + VaR",
            "٦. Human Override",
            "٧. Execution Quality — Microstructure",
            "٨. Kill Switch + Audit Logger",
            "٩. Monitoring + Drift",
            "١٠. Capital Allocation Engine",
            "",
            "📦 *المنصات*",
            "OKX · Binance · Bybit · Bitget · MEXC",
            "",
            "🤖 *الذكاء الاصطناعي*",
            "Groq/Llama 3.3 70B — تحليل الأخبار",
            "Groq Vision — تحليل الشارت",
            "",
            "⚙️ *الإعدادات*",
            "Railway Hobby | Python 3.11 | PTB v21",
        ]
    else:
        lines = [
            "🤖 *رائد — وكيل التداول الذكي*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "رائد هو وكيل تداول ذكي يُحلل أسواق الكريبتو",
            "ويُقدم إشارات تداول مؤسسية بالذكاء الاصطناعي.",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "📞 *الدعم الفني وخدمة العملاء*",
            "للاستفسار والشكاوي والاشتراكات:",
            "• قريباً",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "⚠️ *إخلاء المسؤولية*",
            "جميع التحليلات والإشارات استرشادية فقط.",
            "لا تُعدّ نصيحة مالية أو استثمارية.",
            "القرار النهائي يعود للمستخدم.",
            "التداول ينطوي على مخاطر — استثمر بحكمة.",
        ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ════════════════════════════════════════════════════════════════
# معالج الأخطاء
# ════════════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram error: {context.error}", exc_info=context.error)
    # إرسال رسالة للمستخدم إذا كان هناك update
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع. يرجى المحاولة مجدداً.\n"
                "إذا استمر الخطأ، تواصل مع الدعم الفني."
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
# post_init
# ════════════════════════════════════════════════════════════════
async def post_init(app: Application):
    engine = RaedEngine(BOT_CONFIG)

    # التحقق من OWNER_CHAT_ID صحيح
    owner_id_str = BOT_CONFIG.get("OWNER_CHAT_ID", "")
    owner_id     = None
    if owner_id_str:
        try:
            owner_id = int(owner_id_str)
        except ValueError:
            logger.warning(f"OWNER_CHAT_ID غير صحيح: '{owner_id_str}' — تجاهل")

    async def send_to_owner(message: str):
        if owner_id:
            try:
                # تحديد parse_mode بناءً على وجود Markdown
                pm = ParseMode.MARKDOWN if "*" in message or "_" in message else None
                await app.bot.send_message(
                    chat_id=owner_id,
                    text=message,
                    parse_mode=pm,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"send_to_owner error: {e}")

    from core.state_manager import state_manager as _sm_init
    _sm_init.initialize_owner()

    await engine.start(send_fn=send_to_owner)
    app.bot_data["raed_engine"] = engine

    # قائمة الأوامر لـ Telegram
    await app.bot.set_my_commands([
        BotCommand("start",        "بدء التشغيل"),
        BotCommand("quicksignal",  "تحليل أولي سريع"),
        BotCommand("signal",       "إشارة تداول شاملة"),
        BotCommand("news",         "تحليل الأخبار"),
        BotCommand("regime",       "حالة السوق"),
        BotCommand("onchain",      "تحليل On-Chain"),
        BotCommand("liquidity",    "تحليل السيولة"),
        BotCommand("backtest",     "اختبار تاريخي"),
        BotCommand("analyze",      "تحليل عميق"),
        BotCommand("planmonth",    "خطة شهرية"),
        BotCommand("planweek",     "خطة أسبوعية"),
        BotCommand("portfolio",    "توزيع المحفظة"),
        BotCommand("autotrade",    "تداول تلقائي on/off"),
        BotCommand("execute",      "تنفيذ فوري"),
        BotCommand("stats",        "إحصائيات فورية"),
        BotCommand("risk",         "حالة المخاطر"),
        BotCommand("events",       "الأحداث القادمة"),
        BotCommand("drift",        "حالة النموذج"),
        BotCommand("killswitch",   "Kill Switch"),
        BotCommand("about",        "عن رائد"),
        BotCommand("setportfolio", "ضبط حجم محفظتك"),
        BotCommand("live",         "التداول الحقيقي"),
        BotCommand("trades",       "صفقاتي الحقيقية"),
        BotCommand("premium",      "إدارة الباقة"),
        BotCommand("upgrade",      "الباقات والأسعار"),
        BotCommand("chart",        "تحليل شارت بصري"),
    ])
    logger.info("✅ RaedEngine تم تهيئته وربطه بـ Telegram")


async def post_shutdown(app: Application):
    engine = app.bot_data.get("raed_engine")
    if engine:
        await engine.stop()


# ════════════════════════════════════════════════════════════════
# Safety Monitor — كل ٥ دقائق
# ════════════════════════════════════════════════════════════════
async def safety_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    engine = context.bot_data.get("raed_engine")
    if not engine:
        return
    try:
        was_active = engine.kill_switch.is_active
        await engine.run_safety_checks()
        # إذا فُعِّل Kill Switch الآن → أرسل تنبيهاً للمالك
        if engine.kill_switch.is_active and not was_active:
            owner_id_str = BOT_CONFIG.get("OWNER_CHAT_ID", "")
            if owner_id_str:
                try:
                    owner_id = int(owner_id_str)
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=(
                            "🔴 *Kill Switch فُعِّل تلقائياً*\n\n"
                            "اكتشف رائد وضعاً خطيراً:\n"
                            f"{getattr(engine.kill_switch, 'reason', 'سبب غير معروف')}\n\n"
                            "التداول الآلي مُوقَف. راجع /risk للتفاصيل."
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Kill Switch notification error: {e}")
    except Exception as e:
        logger.error(f"Safety monitor error: {e}")


# ════════════════════════════════════════════════════════════════
# بناء التطبيق
# ════════════════════════════════════════════════════════════════
def build_app() -> Application:
    token = BOT_CONFIG["BOT_TOKEN"]
    if not token:
        raise ValueError("BOT_TOKEN غير موجود في متغيرات البيئة")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── الأوامر الرئيسية ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))

    # ── handlers من الملفات الأخرى ──
    analysis_handlers.register(app)
    plan_handlers.register(app)
    trading_handlers.register(app)

    # ── معالج الأخطاء ──
    app.add_error_handler(error_handler)

    # ── Safety Monitor كل ٥ دقائق ──
    app.job_queue.run_repeating(
        safety_monitor_job,
        interval=300,
        first=60,
    )

    return app


def main():
    logger.info("🚀 Starting Raed — Institutional Balanced Crypto AI Agent v2")
    app = build_app()
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

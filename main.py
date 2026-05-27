"""
🤖 رائد — Institutional Balanced Crypto AI Trading Agent
main.py — نقطة الدخول الرئيسية
"""

import asyncio
import logging
import os
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

import handlers.analysis as analysis_handlers
import handlers.plan     as plan_handlers
import handlers.trading  as trading_handlers

from core.raed_engine import RaedEngine

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/raed.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# ── متغيرات البيئة ────────────────────────────────────────────
BOT_CONFIG = {
    "BOT_TOKEN":          os.getenv("BOT_TOKEN", ""),
    "GROQ_API_KEY":         os.getenv("GROQ_API_KEY", ""),
    "EXCHANGE":             os.getenv("EXCHANGE", "bybit"),
    "EXCHANGE_API_KEY":     os.getenv("EXCHANGE_API_KEY", ""),
    "EXCHANGE_API_SECRET":  os.getenv("EXCHANGE_API_SECRET", ""),
    "EXCHANGE_TESTNET":     os.getenv("EXCHANGE_TESTNET", "false").lower() == "true",
    "CRYPTOPANIC_KEY":    os.getenv("CRYPTOPANIC_KEY", ""),
    "ETHERSCAN_KEY":      os.getenv("ETHERSCAN_KEY", ""),
    "OWNER_CHAT_ID":      os.getenv("OWNER_CHAT_ID", ""),
    "PORTFOLIO_SIZE":     float(os.getenv("PORTFOLIO_SIZE", "10000")),
    "MAX_RISK_PER_TRADE": float(os.getenv("MAX_RISK_PER_TRADE", "0.02")),
    "MAX_DAILY_LOSS":     float(os.getenv("MAX_DAILY_LOSS", "0.05")),
    "MAX_DRAWDOWN":       float(os.getenv("MAX_DRAWDOWN", "0.15")),
    "MAX_OPEN_POSITIONS": int(os.getenv("MAX_OPEN_POSITIONS", "5")),
    "MAX_SINGLE_EXPOSURE":float(os.getenv("MAX_SINGLE_EXPOSURE", "0.20")),
    "MIN_CONFIDENCE":     float(os.getenv("MIN_CONFIDENCE", "0.65")),
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

    # رسالة ترحيب
    welcome_lines = [
        "🤖 *مرحباً بك في رائد*",
        "━━━━━━━━━━━━━━━━━━",
        f"باقتك: {tier_name} | حد العملات: {coins}",
    ]
    if tier == "free" and days_left > 0:
        welcome_lines.append(f"⏰ تداول آلي مجاني: {days_left} يوم متبقٍ")
    welcome_lines += [
        "",
        "رائد وكيل تداول ذكي بالذكاء الاصطناعي.",
        "يُحلل الأسواق ويُقدم إشارات مؤسسية.",
    ]
    await update.message.reply_text(
        "\n".join(welcome_lines), parse_mode=ParseMode.MARKDOWN)

    # الأوامر المتاحة حسب الباقة
    cmds_text = _build_commands_text(user_id, tier, tier_name)
    await update.message.reply_text(cmds_text, parse_mode=ParseMode.MARKDOWN)

    # جدول المسح للجميع
    scan_lines = [
        "⏰ *المسح التلقائي — كل ٤ ساعات*",
        "━━━━━━━━━━━━━━━━━━",
        "🌏 01:00 | 🌅 05:00 | 🌍 09:00",
        "📈 13:00 | 🗽 17:00 | 🌙 21:00 KSA",
        "",
        "• تحليل أفضل العملات تلقائياً",
        "• تنبيه فوري عند فرصة ≥ 65%",
        "• تنفيذ آلي إذا /autotrade مفعّل",
    ]
    await update.message.reply_text(
        "\n".join(scan_lines), parse_mode=ParseMode.MARKDOWN)


def _build_commands_text(user_id: int, tier: str, tier_name: str) -> str:
    """يبني نص الأوامر حسب الباقة."""
    from core.state_manager import state_manager as _sm

    # الأوامر حسب الفئة مع الأوامر المتاحة للمستخدم
    sections = []

    # ── أوامر الجميع ──
    basic = [
        "/quicksignal — تحليل أولي سريع مع نقاط الدخول والخروج",
        "/portfolio — استعراض المحفظة",
        "/trades — الصفقات السابقة والقائمة",
        "/live — التداول الحقيقي (ربط منصة)",
        "/autotrade — تداول تلقائي",
        "/setportfolio — ضبط حجم المحفظة",
        "/upgrade — جدول الباقات والأسعار",
        "/about — عن رائد والدعم الفني",
    ]
    sections.append(("⚙️ *أوامر أساسية*", basic))

    # ── التحليل (فضي+) ──
    if _sm.can_use_command(user_id, "signal"):
        analysis = [
            "/signal — إشارة تداول شاملة ٥ مصادر",
            "/news — تحليل الأخبار بالذكاء الاصطناعي",
            "/regime — حالة السوق",
            "/backtest — اختبار تاريخي",
            "/events — الأحداث الماكرو القادمة",
        ]
        sections.append(("📊 *التحليل*", analysis))

    # ── التخطيط والتعمق (ذهبي+) ──
    if _sm.can_use_command(user_id, "analyze"):
        deep = [
            "/analyze — تحليل عميق بالذكاء الاصطناعي",
            "/liquidity — تحليل السيولة المتقدم",
            "/onchain — تحليل بيانات الشبكة",
            "/planweek — خطة أسبوعية",
            "/planmonth — خطة شهرية",
            "/drift — مراقبة دقة النموذج",
        ]
        sections.append(("🔬 *التحليل المتقدم والتخطيط*", deep))

    # ── ماسي ──
    if _sm.can_use_command(user_id, "chart"):
        premium = [
            "/chart — تحليل شارت بصري (أرفع صورة)",
        ]
        sections.append(("💎 *ماسي حصراً*", premium))

    # ── المدير ──
    if tier == "admin":
        admin_cmds = [
            "/killswitch — إيقاف طارئ",
            "/stats — إحصائيات النظام",
            "/risk — Risk Engine",
            "/premium — إدارة الباقات",
        ]
        sections.append(("👑 *المدير*", admin_cmds))

    # بناء النص
    lines = [f"📋 *أوامرك — {tier_name}*", ""]
    for title, cmds in sections:
        lines.append(title)
        lines += cmds
        lines.append("")

    # إذا مجاني → عرض ما يمكن فعله بالترقية
    if tier == "free":
        lines += [
            "━━━━━━━━━━━━━━━━━━",
            "🔒 *أوامر إضافية بالترقية:*",
            "🥈 فضي — signal/news/regime/backtest",
            "🥇 ذهبي — تحليل عميق/تخطيط/سيولة",
            "💎 ماسي — شارت بصري/300 عملة",
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
        # المدير: معلومات تقنية كاملة
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
        # المستخدمون: معلومات الدعم
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

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram error: {context.error}", exc_info=context.error)


# ════════════════════════════════════════════════════════════════
# Post Init — تهيئة الـ Engine
# ════════════════════════════════════════════════════════════════
async def post_init(app: Application):
    engine = RaedEngine(BOT_CONFIG)

    owner_id = BOT_CONFIG.get("OWNER_CHAT_ID", "")

    async def send_to_owner(message: str):
        if owner_id:
            try:
                await app.bot.send_message(
                    chat_id=owner_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"send_to_owner error: {e}")

    # تهيئة المالك والباقات من ENV عند كل بدء تشغيل
    from core.state_manager import state_manager as _sm_init
    _sm_init.initialize_owner()

    await engine.start(send_fn=send_to_owner)
    app.bot_data["raed_engine"] = engine

    # تحديث قائمة الأوامر
    await app.bot.set_my_commands([
        BotCommand("start",       "بدء التشغيل"),
        BotCommand("signal",      "إشارة تداول شاملة"),
        BotCommand("news",        "تحليل الأخبار"),
        BotCommand("regime",      "حالة السوق"),
        BotCommand("onchain",     "تحليل On-Chain"),
        BotCommand("liquidity",   "تحليل السيولة"),
        BotCommand("backtest",    "اختبار تاريخي"),
        BotCommand("planmonth",   "خطة شهرية"),
        BotCommand("planweek",    "خطة أسبوعية"),
        BotCommand("portfolio",   "توزيع المحفظة"),
        BotCommand("autotrade",   "تداول تلقائي on/off"),
        BotCommand("execute",     "تنفيذ فوري"),
        BotCommand("stats",       "إحصائيات فورية"),
        BotCommand("risk",        "حالة المخاطر"),
        BotCommand("events",      "الأحداث القادمة"),
        BotCommand("drift",       "حالة النموذج"),
        BotCommand("killswitch",  "Kill Switch"),
        BotCommand("about",          "عن رائد"),
        BotCommand("setportfolio",   "ضبط حجم محفظتك"),
        BotCommand("live",           "التداول الحقيقي"),
        BotCommand("trades",         "صفقاتي الحقيقية"),
        BotCommand("premium",        "إدارة الباقة المدفوعة"),
        BotCommand("analyze",        "تحليل عميق لعملة (مدفوع)"),
    ])

    logger.info("✅ RaedEngine initialized and connected to Telegram")


async def post_shutdown(app: Application):
    engine = app.bot_data.get("raed_engine")
    if engine:
        await engine.stop()


# ════════════════════════════════════════════════════════════════
# Safety Monitor — يعمل كل ٥ دقائق
# ════════════════════════════════════════════════════════════════
async def safety_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    engine = context.bot_data.get("raed_engine")
    if engine:
        try:
            await engine.run_safety_checks()
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

    # ── تسجيل handlers ──
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
    import os
    os.makedirs("logs", exist_ok=True)
    logger.info("🚀 Starting Raed — Institutional Balanced Crypto AI Agent")
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

"""
🤖 رائد — Institutional Balanced Crypto AI Trading Agent
main.py v2 — نقطة الدخول الرئيسية

الإصلاحات:
- تحقق من OWNER_CHAT_ID صحيح (رقمي وغير فارغ)
- معالجة أخطاء error_handler مع إرسال رسالة للمستخدم
- تقليل رسائل /start من 3 إلى رسالة موحَّدة
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

    # رسالة موحَّدة (بدلاً من 3 رسائل منفصلة)
    cmds_text = _build_commands_text(user_id, tier, tier_name)

    scan_info = (
        "\n\n⏰ *المسح التلقائي — كل 4 ساعات*\n"
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
            "/signal — إشارة تداول شاملة 5 مصادر",
            "/news — تحليل الأخبار بالذكاء الاصطناعي",
            "/regime — حالة السوق",
            "/backtest — اختبار تاريخي 3 سنوات",
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
        lines = [
            "🤖 *رائد — Institutional Balanced Crypto AI*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "🏗️ *المعمارية — 10 طبقات*",
            "1. Data Layer — CoinGecko · Binance · DeFiLlama",
            "2. Data Validator",
            "3. Regime Detector — Market Regime",
            "4. Signal Layer + Strategy Router (6 مدارس)",
            "5. Risk Engine — Kelly + VaR",
            "6. Human Override",
            "7. Execution Quality — Microstructure",
            "8. Kill Switch + Audit Logger",
            "9. Monitoring + Drift",
            "10. Capital Allocation Engine",
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
                "إذا استمر الخطأ, تواصل مع الدعم الفني."
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
        """يُرسل للمالك فقط (تقارير النظام والأخطاء)."""
        if owner_id:
            try:
                pm = ParseMode.MARKDOWN if "*" in message else None
                await app.bot.send_message(
                    chat_id=owner_id, text=message,
                    parse_mode=pm, disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"send_to_owner error: {e}")

    async def broadcast_fn(message: str):
        """
        يُرسل تقارير المسح لجميع المستخدمين الذين فعّلوا autotrade أو لديهم باقة مدفوعة.
        المالك يتلقى دائماً بغض النظر.
        """
        from core.state_manager import state_manager as _sm_bc
        # المستخدمون المستهدفون: autotrade مفعّل أو مدفوع
        target_ids = set(_sm_bc.get_autotrade_users())
        # إضافة المدفوعين
        for uid in _sm_bc.get_all_user_ids():
            if _sm_bc.is_premium(uid):
                target_ids.add(uid)
        # المالك دائماً
        if owner_id:
            target_ids.add(owner_id)

        pm = ParseMode.MARKDOWN if "*" in message else None
        sent = 0
        for uid in target_ids:
            try:
                await app.bot.send_message(
                    chat_id=uid, text=message,
                    parse_mode=pm, disable_web_page_preview=True)
                sent += 1
            except Exception as e:
                logger.warning(f"broadcast to {uid}: {e}")
        if sent:
            logger.info(f"✅ Broadcast أُرسل لـ {sent} مستخدم")

    from core.state_manager import state_manager as _sm_init
    _sm_init.initialize_owner()

    await engine.start(send_fn=broadcast_fn)
    app.bot_data["raed_engine"] = engine

    # ── تشغيل Scheduler مع ربطه بالمحرك ─────────────────────
    try:
        from core.scheduler import Scheduler

        async def notify_user_fn(message: str, user_id: int = None):
            """دالة الإشعارات للمستخدمين الفرديين والبث."""
            if user_id:
                try:
                    pm = ParseMode.MARKDOWN if "*" in message else None
                    await app.bot.send_message(
                        chat_id=user_id, text=message,
                        parse_mode=pm, disable_web_page_preview=True)
                except Exception as _e:
                    logger.warning(f"notify_user {user_id}: {_e}")
            else:
                await broadcast_fn(message)

        scheduler = Scheduler(send_fn=notify_user_fn)
        scheduler.set_engine(engine)

        # ربط دوال التقارير الدورية
        if hasattr(engine, "run_weekly_report"):
            scheduler.register_weekly(engine.run_weekly_report)
        if hasattr(engine, "run_monthly_report"):
            scheduler.register_monthly(engine.run_monthly_report)
        if hasattr(engine, "run_scan"):
            scheduler.register_scan(engine.run_scan)

        scheduler.start()
        app.bot_data["scheduler"] = scheduler
        engine.scheduler = scheduler
        logger.info("✅ Scheduler مُشغَّل — مراقبة Limit Orders + Trailing Stop")
    except Exception as _se:
        logger.error(f"Scheduler start error: {_se}")

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
    # إيقاف Scheduler أولاً
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        try:
            scheduler.stop()
            logger.info("🛑 Scheduler أوقف")
        except Exception:
            pass
    engine = app.bot_data.get("raed_engine")
    if engine:
        await engine.stop()


# ════════════════════════════════════════════════════════════════
# Safety Monitor — كل 5 دقائق
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


    # T1/T2/T4 مُسجَّلة في trading_handlers.register() ✅

    # ── معالج الأخطاء ──
    app.add_error_handler(error_handler)

    # ── Safety Monitor كل 5 دقائق ──
    app.job_queue.run_repeating(
        safety_monitor_job,
        interval=300,
        first=60,
    )

    # ── مسح خفيف كل ساعة — تنبيه عند ثقة >= 80% ──
    app.job_queue.run_repeating(
        hourly_alert_job,
        interval=3600,
        first=120,
    )

    return app


async def hourly_alert_job(context: ContextTypes.DEFAULT_TYPE):
    """مسح خفيف كل ساعة — يُرسل تنبيهاً فقط عند وجود إشارة >= 80%."""
    engine = context.bot_data.get("raed_engine")
    if not engine:
        return
    try:
        from core.state_manager import state_manager as _sm_h
        # جلب البيانات الأساسية
        btc_c = await engine.data_layer.get_ohlcv("BTC", "1d", 100)
        fear  = await engine.data_layer.get_fear_greed()
        btc_c    = btc_c if isinstance(btc_c, list) else []
        fear_val = int((fear or {}).get("value") or 50)
        if len(btc_c) < 30:
            return
        regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)

        alerts = []
        strong_signals_data = []
        for sym in ["BTC", "ETH", "SOL", "BNB"]:
            try:
                candles = await engine.data_layer.get_ohlcv(sym, "1d", 100)
                candles = candles if isinstance(candles, list) else []
                if len(candles) < 30:
                    continue
                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data={},
                    news_sentiment=0, backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val}, regime=regime)
                if signal.confidence >= 0.80 and signal.direction != "neutral":
                    dir_ar  = "🟢 شراء" if signal.direction == "long" else "🔴 بيع"
                    price_d = await engine.data_layer.get_price(sym)
                    price   = float((price_d or {}).get("price") or 0)
                    alerts.append(
                        f"🚨 {sym} {dir_ar} | ثقة: {signal.confidence:.0%} | ${price:,.2f}")
                    strong_signals_data.append({
                        "symbol":       sym,
                        "direction":    signal.direction,
                        "confidence":   signal.confidence,
                        "price":        price,
                        "approved_size": min(price * 0.001, 1000),  # تقدير بسيط
                    })
            except Exception:
                continue

        if alerts:
            from telegram.constants import ParseMode
            from core.virtual_wallet import VirtualWallet as _VW_h
            import time as _th

            # dedup: لا نُرسل نفس التنبيه مرتين في ساعة
            _alert_key = "|".join(sorted(a.split()[1] for a in alerts if a.startswith("🚨")))
            _last_sent = getattr(hourly_alert_job, "_last_alert_key", ("", 0))
            if _alert_key == _last_sent[0] and _th.time() - _last_sent[1] < 3600:
                logger.info("⚡ تنبيه مكرر — تخطى (dedup)")
                return
            hourly_alert_job._last_alert_key = (_alert_key, _th.time())

            header = "⚡ *تنبيه فوري — إشارة قوية*"
            footer = "\n\n💡 /signal للتفاصيل الكاملة"
            msg    = header + "\n" + "\n".join(alerts) + footer

            # إرسال التنبيه لكل premium + autotrade users
            target_ids = set(_sm_h.get_autotrade_users())
            for uid in _sm_h.get_all_user_ids():
                if _sm_h.is_premium(uid):
                    target_ids.add(uid)
            for uid in target_ids:
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=msg, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass

            # تنفيذ Virtual Wallet لمستخدمي autotrade
            for uid in _sm_h.get_autotrade_users():
                try:
                    _wdata = _sm_h.get_virtual_wallet(uid)
                    if not _wdata:
                        _wdata = {"balance": 10000.0, "invested": 0.0,
                                  "profit": 0.0, "positions": {}, "history": []}
                    _vw_h = _VW_h(_wdata)

                    _executed_syms = []
                    for sig_info in strong_signals_data:
                        if sig_info["confidence"] < 0.80:
                            continue
                        # K2+K3: الفحص الموحد
                        _sym_q = sig_info["symbol"]
                        _can, _buy_amt, _reason = _sm_h.can_auto_execute(
                            uid, _sym_q, _vw_h.total_value, _vw_h.positions)
                        if not _can:
                            logger.info(f"Auto skip {_sym_q}: {_reason}")
                            continue
                        _buy_amt = min(_buy_amt, _vw_h.balance * 0.95)
                        _buy_amt = max(_buy_amt, 50)
                        _result  = _vw_h.buy(
                            symbol     = sig_info["symbol"],
                            price      = sig_info["price"],
                            amount_usd = _buy_amt,
                        )
                        if _result.get("ok"):
                            # Q1-Q5: تسجيل الصفقة لتفعيل القيود
                            _sm_h.record_auto_trade(uid, _sym_q, "daily", _buy_amt)
                            _executed_syms.append(
                                f"• {sig_info['symbol']} ${_buy_amt:,.0f}")

                    if _executed_syms:
                        _sm_h.save_virtual_wallet(uid, _vw_h.to_dict())
                        _confirm = (
                            "✅ *تم تنفيذ صفقات افتراضية تلقائياً*\n\n" +
                            "\n".join(_executed_syms) +
                            f"\n\n💰 رصيدك: ${_vw_h.balance:,.0f}"
                            "\n🎮 /portfolio للتفاصيل"
                        )
                        try:
                            await context.bot.send_message(
                                chat_id=uid, text=_confirm,
                                parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                except Exception as _ve:
                    logger.warning(f"Virtual exec uid={uid}: {_ve}")

            logger.info(f"⚡ تنبيه ساعي: {len(alerts)} إشارة >= 80%")
    except Exception as e:
        logger.error(f"hourly_alert_job: {e}")


def main():
    logger.info("🚀 Starting Raed — Institutional Balanced Crypto AI Agent v2")
    app = build_app()
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

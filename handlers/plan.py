"""
📋 رائد — handlers/plan.py
أوامر: /plan_month /plan_week /portfolio /stats /approve /reject
"""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _get_engine(context):
    return context.bot_data.get("raed_engine")


# ════════════════════════════════════════════════════════════════
# /plan_month — خطة شهرية
# ════════════════════════════════════════════════════════════════
async def cmd_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args    = context.args or DEFAULT_SYMBOLS
    symbols = [a.upper() for a in args[:6]]

    msg = await update.message.reply_text(
        f"📋 جاري بناء الخطة الشهرية لـ {', '.join(symbols)}...\n"
        "قد يستغرق هذا ٣٠-٦٠ ثانية.",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # جمع البيانات لجميع العملات
        candidates = []
        fear       = await engine.data_layer.get_fear_greed()
        onchain    = await engine.data_layer.get_onchain()
        btc_candles= await engine.data_layer.get_ohlcv("BTC", "1d", 250)
        regime     = engine.regime_detector.detect(
            btc_candles, fear_greed=fear.get("value", 50))

        for sym in symbols:
            try:
                candles   = await engine.data_layer.get_ohlcv(sym, "1d", 250)
                news_raw  = await engine.data_layer.get_news(currencies=sym, limit=5)
                news_an   = await engine.news_engine.analyze(news_raw, [sym])
                if len(candles) < 50:
                    continue

                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_an.get("sentiment_score", 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear.get("value", 50)},
                    regime=regime,
                )
                atr_pct = _calc_atr_pct(candles)
                liq     = await engine.microstructure.analyze(sym, 1000)

                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         atr_pct,
                    "liquidity_score": liq.liquidity_score,
                    "expected_return": _estimate_return(signal, regime),
                })
            except Exception as e:
                logger.warning(f"plan_month skip {sym}: {e}")

        # Event Risk
        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()

        # توزيع المحفظة
        portfolio_val = engine.risk_engine.cfg.get("portfolio_size", 10_000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, event_multiplier=ev_mult)

        # بناء الخطة
        lines = [
            "📋 *الخطة الشهرية — رائد التداول الذكي*",
            "━━━━━━━━━━━━━━━━━━",
            f"العملات المُحلَّلة: {', '.join(symbols)}",
            f"حالة السوق: {regime.description_ar}",
            f"ثقة Regime: {regime.confidence:.0%}",
            "",
        ]

        if ev_reason:
            lines += [f"⚠️ تعديل الأحداث: {ev_reason} ({ev_mult:.0%})", ""]

        lines.append(engine.capital_engine.format_ar(allocation, regime))
        lines += [
            "",
            "📅 *جدول الشهر المقترح*",
            f"• أسبوع ١: دخول مراكز #{1} و#{2}",
            f"• أسبوع ٢: مراقبة + إعادة توازن",
            f"• أسبوع ٣: مراجعة الأهداف",
            f"• أسبوع ٤: تقييم النتائج",
            "",
            f"⚠️ هذه خطة استرشادية — القرار النهائي للمستخدم",
            f"🤖 رائد التداول الذكي",
        ]

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_month error: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /plan_week — خطة أسبوعية
# ════════════════════════════════════════════════════════════════
async def cmd_plan_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args    = context.args or DEFAULT_SYMBOLS[:3]
    symbols = [a.upper() for a in args[:4]]

    msg = await update.message.reply_text(
        f"📅 جاري بناء الخطة الأسبوعية لـ {', '.join(symbols)}...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        fear       = await engine.data_layer.get_fear_greed()
        btc_candles= await engine.data_layer.get_ohlcv("BTC", "1d", 200)
        regime     = engine.regime_detector.detect(
            btc_candles, fear_greed=fear.get("value", 50))
        onchain    = await engine.data_layer.get_onchain()

        daily_plan = {}
        days_ar    = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء",
                       "الخميس", "الجمعة", "السبت"]

        from datetime import datetime, timezone
        today_wd = datetime.now(timezone.utc).weekday()  # 0=Mon

        lines = [
            "📅 *الخطة الأسبوعية — رائد التداول الذكي*",
            "━━━━━━━━━━━━━━━━━━",
            f"حالة السوق: {regime.description_ar}",
            f"Fear & Greed: {fear['value']} — {fear['label_ar']}",
            "",
        ]

        for i, sym in enumerate(symbols):
            try:
                candles = await engine.data_layer.get_ohlcv(sym, "4h", 100)
                if len(candles) < 30:
                    continue
                news_raw = await engine.data_layer.get_news(currencies=sym, limit=3)
                news_an  = await engine.news_engine.analyze(news_raw, [sym])
                signal   = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_an.get("sentiment_score", 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear.get("value", 50)},
                    regime=regime,
                )
                strategy, _ = engine.strategy_router.select(regime, signal)
                price_info  = await engine.data_layer.get_price(sym)
                price       = price_info.get("price", 0) if price_info else 0

                dir_ar = "🟢 شراء" if signal.direction == "long" else \
                         "🔴 بيع" if signal.direction == "short" else "⚪ انتظار"

                lines += [
                    f"💎 *{sym}* — ${price:,.2f}",
                    f"  الإشارة: {dir_ar} | الثقة: {signal.confidence:.0%}",
                    f"  الاستراتيجية: {strategy.value}",
                    "",
                ]
            except Exception as e:
                logger.warning(f"week plan {sym}: {e}")

        # أحداث الأسبوع
        events_text = engine.event_risk.format_upcoming_ar(hours=168)
        lines += [
            "📅 *أحداث الأسبوع*",
            events_text,
            "",
            f"⏰ التقرير الأسبوعي: {engine.scheduler.next_weekly_ar()}",
            f"⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            f"🤖 رائد التداول الذكي",
        ]

        # تنظيف كامل — استبدال _ بمسافة في السطور غير الـ Markdown headers
        clean_lines = []
        for line in lines:
            if not (line.startswith("*") and line.endswith("*")):
                # نحافظ على bold لكن ننظف باقي الـ _
                parts = line.split("*")
                cleaned_parts = []
                for j, part in enumerate(parts):
                    if j % 2 == 0:  # خارج bold
                        part = part.replace("_", " ")
                    cleaned_parts.append(part)
                line = "*".join(cleaned_parts)
            clean_lines.append(line)
        final_text = "\n".join(clean_lines)
        await msg.edit_text(final_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_week error: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة الأسبوعية: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /portfolio — عرض توزيع المحفظة
# ════════════════════════════════════════════════════════════════
async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text(
        "💼 جاري تحليل المحفظة...", parse_mode=ParseMode.MARKDOWN)

    try:
        fear       = await engine.data_layer.get_fear_greed()
        btc_candles= await engine.data_layer.get_ohlcv("BTC", "1d", 200)
        regime     = engine.regime_detector.detect(
            btc_candles, fear_greed=fear.get("value", 50))
        ev_mult, _ = engine.event_risk.get_exposure_multiplier()
        onchain    = await engine.data_layer.get_onchain()

        candidates = []
        top_coins  = await engine.data_layer.get_top_coins(limit=20)

        for coin in top_coins[:8]:
            sym = coin.get("symbol", "").upper()
            try:
                candles = await engine.data_layer.get_ohlcv(sym, "1d", 100)
                if len(candles) < 50:
                    continue
                news_raw = await engine.data_layer.get_news(currencies=sym, limit=3)
                news_an  = await engine.news_engine.analyze(news_raw, [sym])
                signal   = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_an.get("sentiment_score", 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear.get("value", 50)},
                    regime=regime,
                )
                liq = await engine.microstructure.analyze(sym, 1000)
                candidates.append({
                    "symbol": sym,
                    "confidence": signal.confidence,
                    "direction":  signal.direction,
                    "atr_pct":    _calc_atr_pct(candles),
                    "liquidity_score": liq.liquidity_score,
                    "expected_return": _estimate_return(signal, regime),
                })
            except Exception:
                pass

        portfolio_val = engine.risk_engine.cfg.get("portfolio_size", 10_000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, ev_mult)
        text = engine.capital_engine.format_ar(allocation, regime)

        risk_status = engine.risk_engine.status_report(portfolio_val)
        text += (
            f"\n\n⚖️ *حالة المخاطر*\n"
            f"• Drawdown: {risk_status['drawdown_pct']:.1f}٪\n"
            f"• PnL اليوم: ${risk_status['today_pnl']:+,.2f}\n"
            f"• صفقات مفتوحة: {risk_status['open_positions']}"
        )

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_portfolio error: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل المحفظة: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /stats — إحصائيات فورية شاملة
# ════════════════════════════════════════════════════════════════
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    try:
        portfolio_val = engine.risk_engine.cfg.get("portfolio_size", 10_000)
        risk_st       = engine.risk_engine.status_report(portfolio_val)
        pnl_summary   = engine.audit_logger.pnl_summary()
        drift_st      = engine.drift_monitor.assess()
        kill_st       = engine.kill_switch.status_ar()
        override_st   = engine.human_override.pending_list_ar()
        sched_w       = engine.scheduler.next_weekly_ar()
        sched_m       = engine.scheduler.next_monthly_ar()
        ev_mult, ev_r = engine.event_risk.get_exposure_multiplier()

        lines = [
            "📊 *إحصائيات رائد الفورية*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "💰 *المحفظة*",
            f"• القيمة الكلية: ${risk_st['portfolio']:,.0f}",
            f"• Drawdown: {risk_st['drawdown_pct']:.1f}٪",
            f"• PnL اليوم: ${risk_st['today_pnl']:+,.2f}",
            f"• صفقات مفتوحة: {risk_st['open_positions']}",
            f"• حد الخسارة اليومية مُستهلك: {risk_st['daily_loss_used']:.0f}٪",
            "",
            "📈 *الأداء الإجمالي*",
            f"• إجمالي الصفقات: {pnl_summary.get('trades', 0)}",
            f"• صافي الربح: ${pnl_summary.get('total_pnl', 0):+,.2f}",
            f"• نسبة الفوز: {pnl_summary.get('win_rate', 0):.1f}٪",
            f"• متوسط الربح: ${pnl_summary.get('avg_win', 0):,.2f}",
            f"• متوسط الخسارة: ${abs(pnl_summary.get('avg_loss', 0)):,.2f}",
            "",
            "🔬 *حالة النموذج*",
            f"• معدل فوز النموذج: {drift_st.current_win_rate:.0%}",
            f"• الانحراف: {drift_st.drift_pct:.1f}٪ ({drift_st.drift_level})",
            f"• {drift_st.recommendation_ar}",
            "",
            "📅 *الأحداث والمخاطر*",
            f"• تعرض الأحداث: {ev_mult:.0%}" + (f" ({ev_r})" if ev_r else ""),
            "",
            "⏰ *التقارير التلقائية*",
            f"• {sched_w}",
            f"• {sched_m}",
            "",
            kill_st,
            "",
            override_st,
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_stats error: {e}")
        await update.message.reply_text(f"❌ خطأ في الإحصائيات: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /approve و /reject — موافقة/رفض العمليات
# ════════════════════════════════════════════════════════════════
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ استخدام: /approve [approval_id]")
        return
    ok = await engine.human_override.approve(args[0], "user")
    await update.message.reply_text(
        "✅ تمت الموافقة وجاري التنفيذ" if ok
        else "⚠️ رمز الموافقة غير موجود أو انتهت صلاحيته"
    )


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ استخدام: /reject [approval_id]")
        return
    ok = await engine.human_override.reject(args[0], "user")
    await update.message.reply_text(
        "🚫 تم رفض العملية" if ok
        else "⚠️ رمز الموافقة غير موجود أو انتهت صلاحيته"
    )


# ── Helpers ───────────────────────────────────────────────────
def _safe(text: str) -> str:
    """يُنظّف النص من رموز Markdown الخاصة لتجنب أخطاء التنسيق."""
    if not text:
        return ""
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text


def _calc_atr_pct(candles, period=14):
    if len(candles) < period + 1:
        return 3.0
    trs = []
    for i in range(1, len(candles)):
        hl = candles[i]["high"] - candles[i]["low"]
        hc = abs(candles[i]["high"] - candles[i-1]["close"])
        lc = abs(candles[i]["low"]  - candles[i-1]["close"])
        trs.append(max(hl, hc, lc))
    atr   = sum(trs[-period:]) / period
    price = candles[-1]["close"]
    return (atr / price * 100) if price > 0 else 3.0


def _estimate_return(signal, regime):
    from core.regime_detector import Regime
    base = signal.confidence * 15
    adj  = {
        Regime.BULL_TREND: 1.2, Regime.ACCUMULATION: 1.1,
        Regime.SIDEWAYS: 0.8,  Regime.HIGH_VOLATILITY: 0.6,
        Regime.BEAR_TREND: 0.5, Regime.UNKNOWN: 0.4,
    }.get(regime.regime, 0.8)
    return round(base * adj, 1)


# ── تسجيل الـ Handlers ─────────────────────────────────────
def register(app):
    app.add_handler(CommandHandler("plan_month", cmd_plan_month))
    app.add_handler(CommandHandler("plan_week",  cmd_plan_week))
    app.add_handler(CommandHandler("portfolio",  cmd_portfolio))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))

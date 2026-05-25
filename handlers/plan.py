"""
📋 رائد — handlers/plan.py
أوامر: /plan_month /plan_week /portfolio /stats /approve /reject
- جميع النتائج محمية من None
- Markdown آمن — لا أخطاء تنسيق
"""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context): return context.bot_data.get("raed_engine")

def _clean(text: str) -> str:
    """يُنظّف النص من رموز Markdown التي تسبب أخطاء."""
    if not text:
        return ""
    # استبدال _ بمسافة في كل ما ليس داخل *bold*
    parts = text.split("*")
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:   # خارج bold
            part = part.replace("_", " ").replace("`", "'")
        result.append(part)
    return "*".join(result)

def _calc_atr(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < period + 1:
        return 3.0
    try:
        trs = []
        for i in range(1, len(candles)):
            h = float(candles[i].get("high", 0))
            l = float(candles[i].get("low", 0))
            c = float(candles[i-1].get("close", 0))
            if h > 0 and l > 0 and c > 0:
                trs.append(max(h - l, abs(h - c), abs(l - c)))
        if not trs:
            return 3.0
        atr   = sum(trs[-period:]) / period
        price = float(candles[-1].get("close", 0))
        return (atr / price * 100) if price > 0 else 3.0
    except (ValueError, TypeError, ZeroDivisionError):
        return 3.0

def _est_return(signal, regime) -> float:
    from core.regime_detector import Regime
    base = float(signal.confidence or 0) * 15
    adj  = {
        Regime.BULL_TREND: 1.2, Regime.ACCUMULATION: 1.1,
        Regime.SIDEWAYS: 0.8,   Regime.HIGH_VOLATILITY: 0.6,
        Regime.BEAR_TREND: 0.5, Regime.UNKNOWN: 0.4,
    }.get(regime.regime, 0.8)
    return round(base * adj, 1)


# ════════════════════════════════════════════════════════════════
# /plan_month
# ════════════════════════════════════════════════════════════════
async def cmd_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or DEFAULT_SYMBOLS
    symbols = [a.upper() for a in args[:6]]
    msg = await update.message.reply_text(
        f"📋 جاري بناء الخطة الشهرية لـ {', '.join(symbols)}...\n"
        f"قد يستغرق ٣٠-٦٠ ثانية")

    try:
        fear       = await engine.data_layer.get_fear_greed() or {"value": 50, "label_ar": "محايد"}
        onchain    = await engine.data_layer.get_onchain()    or {}
        btc_c      = await engine.data_layer.get_ohlcv("BTC", "1d", 200)
        btc_c      = btc_c or []
        fear_val   = int(fear.get("value") or 50)

        regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val) \
                 if len(btc_c) >= 30 else None

        from core.regime_detector import Regime, RegimeResult
        if regime is None:
            from core.regime_detector import REGIME_AR, REGIME_STRATEGY
            regime = RegimeResult(
                Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                ["reduce_size"], {}, "reduce_size")

        candidates = []
        for sym in symbols:
            try:
                candles = await engine.data_layer.get_ohlcv(sym, "1d", 200)
                candles = candles or []
                if len(candles) < 30:
                    continue
                news_r  = await engine.data_layer.get_news(currencies=sym, limit=5)
                news_r  = news_r or []
                news_an = await engine.news_engine.analyze(news_r, [sym]) or {}
                signal  = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=float(news_an.get("sentiment_score") or 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                liq = await engine.microstructure.analyze(sym, 1000)
                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         _calc_atr(candles),
                    "liquidity_score": liq.liquidity_score if liq else 0.7,
                    "expected_return": _est_return(signal, regime),
                })
            except Exception as e:
                logger.warning(f"plan_month {sym}: {e}")

        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        portfolio_val = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, event_multiplier=ev_mult)

        lines = [
            "📋 *الخطة الشهرية — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"العملات: {', '.join(symbols)}",
            f"السوق: {regime.description_ar}",
            f"الثقة: {regime.confidence:.0%}",
            "",
        ]
        if ev_reason:
            lines.append(f"⚠️ تعديل أحداث: {_clean(ev_reason)} ({ev_mult:.0%})")
            lines.append("")

        lines.append(_clean(engine.capital_engine.format_ar(allocation, regime)))
        lines += [
            "",
            "📅 *جدول الشهر المقترح*",
            "• أسبوع ١: دخول المراكز ذات الأولوية",
            "• أسبوع ٢: مراقبة وإعادة توازن",
            "• أسبوع ٣: مراجعة الأهداف",
            "• أسبوع ٤: تقييم النتائج",
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]

        final = _clean("\n".join(lines))
        await msg.edit_text(final, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_month: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /plan_week
# ════════════════════════════════════════════════════════════════
async def cmd_plan_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or DEFAULT_SYMBOLS[:3]
    symbols = [a.upper() for a in args[:4]]
    msg = await update.message.reply_text(
        f"📅 جاري بناء الخطة الأسبوعية لـ {', '.join(symbols)}...")

    try:
        fear     = await engine.data_layer.get_fear_greed() or {"value": 50, "label_ar": "محايد"}
        onchain  = await engine.data_layer.get_onchain()    or {}
        btc_c    = await engine.data_layer.get_ohlcv("BTC", "1d", 200) or []
        fear_val = int(fear.get("value") or 50)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        lines = [
            "📅 *الخطة الأسبوعية — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"السوق: {regime.description_ar}",
            f"Fear & Greed: {fear_val} — {fear.get('label_ar','محايد')}",
            "",
        ]

        for sym in symbols:
            try:
                # نستخدم 1d بدلاً من 4h — CoinGecko لا يدعم 4h
                candles = await engine.data_layer.get_ohlcv(sym, "1d", 100) or []
                if len(candles) < 20:
                    lines.append(f"⚠️ {sym}: بيانات غير كافية")
                    lines.append("")
                    continue

                news_r  = await engine.data_layer.get_news(currencies=sym, limit=3) or []
                news_an = await engine.news_engine.analyze(news_r, [sym]) or {}
                signal  = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=float(news_an.get("sentiment_score") or 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                strat, _ = engine.strategy_router.select(regime, signal)
                price_d  = await engine.data_layer.get_price(sym)
                price    = float((price_d or {}).get("price") or 0)

                dir_ar = ("🟢 شراء" if signal.direction == "long"
                          else "🔴 بيع" if signal.direction == "short"
                          else "⚪ انتظار")
                strat_name = strat.value.replace("_", " ")

                lines += [
                    f"💎 *{sym}*" + (f" — ${price:,.2f}" if price > 0 else ""),
                    f"  الإشارة: {dir_ar} | الثقة: {signal.confidence:.0%}",
                    f"  الاستراتيجية: {strat_name}",
                    "",
                ]
            except Exception as e:
                logger.warning(f"plan_week {sym}: {e}")
                lines.append(f"⚠️ {sym}: {str(e)[:50]}")
                lines.append("")

        events_text = engine.event_risk.format_upcoming_ar(hours=168)
        sched_text  = engine.scheduler.next_weekly_ar() if engine.scheduler else ""

        lines += [
            "📅 *أحداث الأسبوع*",
            events_text,
        ]
        if sched_text:
            lines += ["", f"⏰ {sched_text}"]
        lines += [
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]

        final = _clean("\n".join(lines))
        await msg.edit_text(final, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_week: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة الأسبوعية: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /portfolio
# ════════════════════════════════════════════════════════════════
async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    msg = await update.message.reply_text("💼 جاري تحليل المحفظة...")
    try:
        fear     = await engine.data_layer.get_fear_greed() or {"value": 50}
        btc_c    = await engine.data_layer.get_ohlcv("BTC", "1d", 200) or []
        onchain  = await engine.data_layer.get_onchain() or {}
        fear_val = int(fear.get("value") or 50)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        ev_mult, _ = engine.event_risk.get_exposure_multiplier()
        candidates = []
        top_coins  = await engine.data_layer.get_top_coins(limit=15) or []

        for coin in top_coins[:8]:
            sym = (coin.get("symbol") or "").upper()
            if not sym:
                continue
            try:
                candles = await engine.data_layer.get_ohlcv(sym, "1d", 100) or []
                if len(candles) < 30:
                    continue
                news_r  = await engine.data_layer.get_news(currencies=sym, limit=3) or []
                news_an = await engine.news_engine.analyze(news_r, [sym]) or {}
                signal  = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=float(news_an.get("sentiment_score") or 0),
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                liq = await engine.microstructure.analyze(sym, 1000)
                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         _calc_atr(candles),
                    "liquidity_score": liq.liquidity_score if liq else 0.7,
                    "expected_return": _est_return(signal, regime),
                })
            except Exception:
                pass

        portfolio_val = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, ev_mult)
        risk_st       = engine.risk_engine.status_report(portfolio_val)

        text = _clean(engine.capital_engine.format_ar(allocation, regime))
        text += (
            f"\n\n⚖️ *حالة المخاطر*\n"
            f"• Drawdown: {risk_st.get('drawdown_pct',0):.1f}٪\n"
            f"• PnL اليوم: ${risk_st.get('today_pnl',0):+,.2f}\n"
            f"• صفقات مفتوحة: {risk_st.get('open_positions',0)}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_portfolio: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل المحفظة: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /stats
# ════════════════════════════════════════════════════════════════
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    try:
        portfolio_val = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        risk_st       = engine.risk_engine.status_report(portfolio_val) or {}
        pnl           = engine.audit_logger.pnl_summary() or {}
        drift_st      = engine.drift_monitor.assess()
        kill_st       = engine.kill_switch.status_ar()
        override_st   = engine.human_override.pending_list_ar()
        ev_mult, ev_r = engine.event_risk.get_exposure_multiplier()
        sched_w = engine.scheduler.next_weekly_ar()  if engine.scheduler else "غير مُفعَّل"
        sched_m = engine.scheduler.next_monthly_ar() if engine.scheduler else "غير مُفعَّل"

        lines = [
            "📊 *إحصائيات رائد الفورية*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "💰 *المحفظة*",
            f"• القيمة: ${risk_st.get('portfolio',0):,.0f}",
            f"• Drawdown: {risk_st.get('drawdown_pct',0):.1f}٪",
            f"• PnL اليوم: ${risk_st.get('today_pnl',0):+,.2f}",
            f"• صفقات مفتوحة: {risk_st.get('open_positions',0)}",
            f"• حد الخسارة اليومية: {risk_st.get('daily_loss_used',0):.0f}٪ مُستهلك",
            "",
            "📈 *الأداء الإجمالي*",
            f"• إجمالي الصفقات: {pnl.get('trades',0)}",
            f"• صافي الربح: ${pnl.get('total_pnl',0):+,.2f}",
            f"• نسبة الفوز: {pnl.get('win_rate',0):.1f}٪",
            f"• متوسط الربح: ${pnl.get('avg_win',0):,.2f}",
            f"• متوسط الخسارة: ${abs(pnl.get('avg_loss',0)):,.2f}",
            "",
            "🔬 *حالة النموذج*",
            f"• معدل فوز: {drift_st.current_win_rate:.0%}",
            f"• الانحراف: {drift_st.drift_pct:.1f}٪",
            f"• {drift_st.recommendation_ar}",
            "",
            "📅 *الأحداث*",
            f"• تعرض الأحداث: {ev_mult:.0%}" + (f" — {_clean(ev_r)}" if ev_r else ""),
            "",
            "⏰ *التقارير التلقائية*",
            f"• {_clean(sched_w)}",
            f"• {_clean(sched_m)}",
            "",
            kill_st,
            "",
            override_st,
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_stats: {e}")
        await update.message.reply_text(f"❌ خطأ في الإحصائيات: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /approve و /reject
# ════════════════════════════════════════════════════════════════
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ الاستخدام: /approve [رمز]"); return
    try:
        ok = await engine.human_override.approve(args[0], "user")
        await update.message.reply_text(
            "✅ تمت الموافقة وجاري التنفيذ" if ok
            else "⚠️ رمز غير موجود أو انتهت صلاحيته")
    except Exception as e:
        logger.error(f"cmd_approve: {e}")
        await update.message.reply_text("❌ خطأ في معالجة الموافقة")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ الاستخدام: /reject [رمز]"); return
    try:
        ok = await engine.human_override.reject(args[0], "user")
        await update.message.reply_text(
            "🚫 تم الرفض" if ok
            else "⚠️ رمز غير موجود أو انتهت صلاحيته")
    except Exception as e:
        logger.error(f"cmd_reject: {e}")
        await update.message.reply_text("❌ خطأ في معالجة الرفض")


def register(app):
    app.add_handler(CommandHandler("plan_month", cmd_plan_month))
    app.add_handler(CommandHandler("plan_week",  cmd_plan_week))
    app.add_handler(CommandHandler("portfolio",  cmd_portfolio))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))

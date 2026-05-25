"""
📡 رائد — handlers/analysis.py
أوامر: /news /onchain /regime /backtest /signal /liquidity /events /drift
"""

import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# ── الرموز الافتراضية ─────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL", "ADA"]


def _get_engine(context: ContextTypes.DEFAULT_TYPE):
    """يُعيد raed_engine من bot_data."""
    return context.bot_data.get("raed_engine")


# ════════════════════════════════════════════════════════════════
# /news — تحليل الأخبار
# ════════════════════════════════════════════════════════════════
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args    = context.args or []
    symbols = [a.upper() for a in args] or DEFAULT_SYMBOLS[:3]

    msg = await update.message.reply_text(
        f"📰 جاري جمع وتحليل الأخبار لـ {', '.join(symbols)}...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        items    = await engine.data_layer.get_news(
            currencies=",".join(symbols), limit=20)
        analysis = await engine.news_engine.analyze(items, symbols)
        engine.event_risk.ingest_news_events(items)   # تحديث Event Risk

        text = engine.news_engine.format_ar(items, analysis)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"cmd_news error: {e}")
        await msg.edit_text("❌ خطأ في جلب الأخبار، يرجى المحاولة لاحقاً")


# ════════════════════════════════════════════════════════════════
# /onchain — تحليل On-Chain
# ════════════════════════════════════════════════════════════════
async def cmd_onchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text(
        "🔗 جاري جلب بيانات On-Chain من DeFiLlama...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        data   = await engine.data_layer.get_onchain()
        fear   = await engine.data_layer.get_fear_greed()

        top_p  = data.get("protocols", [])[:5]
        tvl    = data.get("tvl", 0)

        lines = [
            "🔗 *تحليل On-Chain — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 إجمالي TVL: ${tvl/1e9:.2f}B",
            f"😨 Fear & Greed: {fear['value']} — {fear['label_ar']}",
            "",
            "🏆 *أكبر البروتوكولات*",
        ]
        for i, p in enumerate(top_p, 1):
            tvl_b = p.get("tvl", 0) / 1e9
            lines.append(f"{i}. {p['name']}: ${tvl_b:.2f}B — {p.get('chain','multi')}")

        lines += [
            "",
            "📡 *المصدر:* DeFiLlama (مجاني)",
            "🤖 رائد التداول الذكي",
        ]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_onchain error: {e}")
        await msg.edit_text("❌ خطأ في جلب بيانات On-Chain")


# ════════════════════════════════════════════════════════════════
# /regime — حالة السوق
# ════════════════════════════════════════════════════════════════
async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()

    msg = await update.message.reply_text(
        f"📊 جاري تحليل حالة السوق لـ {symbol}...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        candles = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        fear    = await engine.data_layer.get_fear_greed()

        if len(candles) < 30:
            await msg.edit_text(
            f"⚠️ لم أتمكن من جلب بيانات {symbol}\n"
            f"تأكد من صحة الرمز أو حاول مرة أخرى بعد لحظة"
        )
            return

        result = engine.regime_detector.detect(
            candles,
            btc_dominance=50.0,
            fear_greed=fear.get("value", 50)
        )
        text = engine.regime_detector.format_ar(result)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_regime error: {e}")
        await msg.edit_text("❌ خطأ في تحليل حالة السوق")


# ════════════════════════════════════════════════════════════════
# /signal — إشارة تداول شاملة
# ════════════════════════════════════════════════════════════════
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()

    msg = await update.message.reply_text(
        f"📡 جاري تحليل إشارة {symbol} عبر ٥ مصادر...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # جمع البيانات
        candles  = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        onchain  = await engine.data_layer.get_onchain()
        fear     = await engine.data_layer.get_fear_greed()
        news_raw = await engine.data_layer.get_news(currencies=symbol)
        news_an  = await engine.news_engine.analyze(news_raw, [symbol])

        if len(candles) < 50:
            await msg.edit_text(
                f"⚠️ لم أتمكن من جلب بيانات كافية لـ {symbol}\n"
                f"جاري المحاولة مع CoinGecko — أعد المحاولة بعد ١٠ ثواني"
            )
            return

        # Regime
        regime = engine.regime_detector.detect(
            candles, fear_greed=fear.get("value", 50))

        # إشارة شاملة
        signal = engine.signal_layer.generate(
            symbol=symbol,
            candles=candles,
            onchain_data=onchain,
            news_sentiment=news_an.get("sentiment_score", 0),
            backtest_win_rate=0.55,
            macro_data={"fear_greed": fear.get("value", 50)},
            regime=regime,
        )

        # استراتيجية
        strategy, params = engine.strategy_router.select(regime, signal)

        # مخاطر
        atr_pct = candles[-1]["high"] / candles[-1]["low"] - 1 if candles else 0.03
        risk    = engine.risk_engine.assess(
            symbol=symbol,
            direction=signal.direction,
            confidence=signal.confidence,
            price=candles[-1]["close"] if candles else 0,
            atr_pct=atr_pct * 100,
            regime=regime.regime.value,
        )

        # تجميع التقرير
        text = (
            engine.signal_layer.format_ar(signal) + "\n\n" +
            engine.regime_detector.format_ar(regime) + "\n\n" +
            engine.strategy_router.format_ar(strategy, params) + "\n\n" +
            engine.risk_engine.format_assessment_ar(risk, symbol)
        )
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_signal error: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل {symbol}: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /backtest — اختبار تاريخي
# ════════════════════════════════════════════════════════════════
async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args     = context.args or []
    symbol   = args[0].upper() if args else "BTC"
    strategy = args[1].lower() if len(args) > 1 else "trend_following"

    valid_strategies = ["trend_following", "mean_reversion", "breakout"]
    if strategy not in valid_strategies:
        await update.message.reply_text(
            f"⚠️ الاستراتيجيات المتاحة:\n" +
            "\n".join(f"• {s}" for s in valid_strategies)
        )
        return

    msg = await update.message.reply_text(
        f"⏳ جاري تشغيل Backtest لـ {symbol} (٣ سنوات)...\n"
        "قد يستغرق هذا حتى ٣٠ ثانية.",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # جلب ٣ سنوات بيانات
        price_data = await engine.data_layer.get_historical_prices(symbol, days=1095)

        if not price_data:
            await msg.edit_text(f"⚠️ لا تتوفر بيانات تاريخية لـ {symbol}")
            return

        result = await engine.backtest_engine.run(symbol, price_data, strategy)
        text   = engine.backtest_engine.format_ar(result)

        # تحديث baseline لـ Drift Monitor
        if result.win_rate > 0:
            engine.drift_monitor.update_baseline(result.win_rate / 100)

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_backtest error: {e}")
        await msg.edit_text(f"❌ خطأ في Backtest: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /liquidity — تحليل السيولة
# ════════════════════════════════════════════════════════════════
async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()

    msg = await update.message.reply_text(
        f"🔬 جاري تحليل Order Book لـ {symbol}...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        profile = await engine.microstructure.analyze(symbol, order_size_usd=1000)
        walls   = await engine.microstructure.detect_walls(symbol)

        text = engine.microstructure.format_ar(profile)
        if walls.buy_walls or walls.sell_walls:
            text += f"\n\n🧱 *جدران السوق*\n"
            text += f"• الدعم: ${walls.support_level:,.2f}\n"
            text += f"• المقاومة: ${walls.resistance_level:,.2f}\n"
            text += f"• ضغط صافٍ: {'شراء 🟢' if walls.net_pressure > 0 else 'بيع 🔴' if walls.net_pressure < 0 else 'محايد ⚪'}"

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_liquidity error: {e}")
        await msg.edit_text("❌ خطأ في تحليل السيولة")


# ════════════════════════════════════════════════════════════════
# /events — الأحداث القادمة
# ════════════════════════════════════════════════════════════════
async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    state   = engine.event_risk.assess()
    text_ev = engine.event_risk.format_upcoming_ar(hours=72)

    lines = [
        "📅 *فلتر مخاطر الأحداث — رائد*",
        "━━━━━━━━━━━━━━━━━━",
        state.message_ar,
        "",
        text_ev,
    ]
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ════════════════════════════════════════════════════════════════
# /drift — حالة النموذج
# ════════════════════════════════════════════════════════════════
async def cmd_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    state = engine.drift_monitor.assess()
    text  = engine.drift_monitor.format_ar(state)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── تسجيل الـ Handlers ───────────────────────────────────────
def register(app):
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("onchain",   cmd_onchain))
    app.add_handler(CommandHandler("regime",    cmd_regime))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("backtest",  cmd_backtest))
    app.add_handler(CommandHandler("liquidity", cmd_liquidity))
    app.add_handler(CommandHandler("events",    cmd_events))
    app.add_handler(CommandHandler("drift",     cmd_drift))

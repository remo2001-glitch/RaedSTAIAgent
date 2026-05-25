"""
📡 رائد — handlers/analysis.py
أوامر: /news /onchain /regime /backtest /signal /liquidity /events /drift
جميع النتائج محمية من None — لا TypeError أبداً
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context): return context.bot_data.get("raed_engine")

def _safe_md(text: str) -> str:
    """يُنظّف النص من رموز Markdown الخاصة."""
    if not text:
        return ""
    for ch in ['_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text


# ════════════════════════════════════════════════════════════════
# /news
# ════════════════════════════════════════════════════════════════
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or []
    symbols = [a.upper() for a in args] or ["BTC", "ETH", "BNB"]
    msg = await update.message.reply_text(
        f"📰 جاري تحليل الأخبار لـ {', '.join(symbols)}...")

    try:
        items    = await engine.data_layer.get_news(
            currencies=",".join(symbols), limit=20)
        items    = items or []
        analysis = await engine.news_engine.analyze(items, symbols)
        if items:
            engine.event_risk.ingest_news_events(items)
        text = engine.news_engine.format_ar(items, analysis)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"cmd_news: {e}")
        await msg.edit_text("❌ خطأ في جلب الأخبار، حاول مجدداً")


# ════════════════════════════════════════════════════════════════
# /onchain
# ════════════════════════════════════════════════════════════════
async def cmd_onchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    msg = await update.message.reply_text("🔗 جاري جلب بيانات On-Chain...")
    try:
        data  = await engine.data_layer.get_onchain()
        fear  = await engine.data_layer.get_fear_greed()
        data  = data  or {"tvl": 0, "protocols": []}
        fear  = fear  or {"value": 50, "label_ar": "محايد"}
        top_p = (data.get("protocols") or [])[:5]
        tvl   = data.get("tvl") or 0

        lines = [
            "🔗 *تحليل On-Chain — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 إجمالي TVL: ${tvl/1e9:.2f}B",
            f"😨 Fear & Greed: {fear.get('value',50)} — {fear.get('label_ar','محايد')}",
            "", "🏆 *أكبر البروتوكولات*",
        ]
        for i, p in enumerate(top_p, 1):
            tvl_b = (p.get("tvl") or 0) / 1e9
            lines.append(f"{i}\\. {p.get('name','')} — ${tvl_b:.2f}B")
        lines += ["", "📡 المصدر: DeFiLlama | 🤖 رائد"]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_onchain: {e}")
        await msg.edit_text("❌ خطأ في جلب بيانات On-Chain")


# ════════════════════════════════════════════════════════════════
# /regime
# ════════════════════════════════════════════════════════════════
async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(f"📊 جاري تحليل حالة السوق لـ {symbol}...")

    try:
        candles = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        candles = candles or []
        fear    = await engine.data_layer.get_fear_greed()
        fear    = fear    or {"value": 50}

        if len(candles) < 30:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"المصادر تحتاج لحظة للاستجابة — أعد المحاولة بعد دقيقة"
            ); return

        result = engine.regime_detector.detect(
            candles, btc_dominance=50.0,
            fear_greed=int(fear.get("value") or 50))
        await msg.edit_text(
            engine.regime_detector.format_ar(result),
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_regime: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل السوق: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════════
# /signal
# ════════════════════════════════════════════════════════════════
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(
        f"📡 جاري تحليل {symbol} عبر ٥ مصادر...")

    try:
        candles  = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        candles  = candles or []
        onchain  = await engine.data_layer.get_onchain()
        onchain  = onchain or {}
        fear     = await engine.data_layer.get_fear_greed()
        fear     = fear    or {"value": 50}
        news_raw = await engine.data_layer.get_news(currencies=symbol)
        news_raw = news_raw or []
        news_an  = await engine.news_engine.analyze(news_raw, [symbol])
        news_an  = news_an  or {}

        if len(candles) < 50:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"أعد المحاولة بعد دقيقة — الـ APIs تستجيب"
            ); return

        fear_val = int(fear.get("value") or 50)
        regime   = engine.regime_detector.detect(candles, fear_greed=fear_val)

        signal = engine.signal_layer.generate(
            symbol=symbol, candles=candles, onchain_data=onchain,
            news_sentiment=float(news_an.get("sentiment_score") or 0),
            backtest_win_rate=0.55,
            macro_data={"fear_greed": fear_val},
            regime=regime,
        )
        strategy, params = engine.strategy_router.select(regime, signal)

        atr_pct = _calc_atr(candles)
        price   = float(candles[-1]["close"]) if candles else 0
        risk    = engine.risk_engine.assess(
            symbol=symbol, direction=signal.direction,
            confidence=signal.confidence, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
        )

        parts = [
            engine.signal_layer.format_ar(signal),
            engine.regime_detector.format_ar(regime),
            engine.strategy_router.format_ar(strategy, params),
            engine.risk_engine.format_assessment_ar(risk, symbol),
        ]
        await msg.edit_text("\n\n".join(parts), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_signal: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل {symbol}: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /backtest
# ════════════════════════════════════════════════════════════════
async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args     = context.args or []
    symbol   = args[0].upper() if args else "BTC"
    strategy = args[1].lower() if len(args) > 1 else "trend_following"
    valid    = ["trend_following", "mean_reversion", "breakout"]

    if strategy not in valid:
        await update.message.reply_text(
            "⚠️ الاستراتيجيات المتاحة:\n" +
            "\n".join(f"• {s}" for s in valid)); return

    msg = await update.message.reply_text(
        f"⏳ جاري Backtest لـ {symbol} — ٣ سنوات بيانات حقيقية\n"
        f"قد يستغرق ٣٠-٦٠ ثانية...")

    try:
        price_data = await engine.data_layer.get_historical_prices(symbol, days=1095)
        price_data = price_data or []

        if len(price_data) < 90:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} التاريخية غير كافية حالياً\n"
                f"({len(price_data)} يوم متاح — الحد الأدنى 90)\n"
                f"أعد المحاولة بعد دقيقتين"
            ); return

        result = await engine.backtest_engine.run(symbol, price_data, strategy)
        if result.win_rate > 0:
            engine.drift_monitor.update_baseline(result.win_rate / 100)
        await msg.edit_text(
            engine.backtest_engine.format_ar(result),
            parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_backtest: {e}")
        await msg.edit_text(f"❌ خطأ في Backtest: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /liquidity
# ════════════════════════════════════════════════════════════════
async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(f"🔬 جاري تحليل السيولة لـ {symbol}...")

    try:
        profile = await engine.microstructure.analyze(symbol, order_size_usd=1000)
        walls   = await engine.microstructure.detect_walls(symbol)
        text    = engine.microstructure.format_ar(profile)

        if walls and (walls.buy_walls or walls.sell_walls):
            net_ar = ("🟢 شراء" if walls.net_pressure > 0.1
                      else "🔴 بيع" if walls.net_pressure < -0.1
                      else "⚪ متوازن")
            text += (f"\n\n🧱 *جدران السوق*\n"
                     f"• الدعم: ${walls.support_level:,.2f}\n"
                     f"• المقاومة: ${walls.resistance_level:,.2f}\n"
                     f"• ضغط: {net_ar}")
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_liquidity: {e}")
        await msg.edit_text("❌ خطأ في تحليل السيولة")


# ════════════════════════════════════════════════════════════════
# /events
# ════════════════════════════════════════════════════════════════
async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    try:
        state   = engine.event_risk.assess()
        text_ev = engine.event_risk.format_upcoming_ar(hours=72)
        lines   = [
            "📅 *فلتر مخاطر الأحداث — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            state.message_ar, "", text_ev,
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_events: {e}")
        await update.message.reply_text("❌ خطأ في جلب الأحداث")


# ════════════════════════════════════════════════════════════════
# /drift
# ════════════════════════════════════════════════════════════════
async def cmd_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    try:
        state = engine.drift_monitor.assess()
        await update.message.reply_text(
            engine.drift_monitor.format_ar(state),
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_drift: {e}")
        await update.message.reply_text("❌ خطأ في تحليل النموذج")


# ── Helper ────────────────────────────────────────────────────
def _calc_atr(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < period + 1:
        return 3.0
    try:
        trs = []
        for i in range(1, len(candles)):
            h = float(candles[i]["high"])
            l = float(candles[i]["low"])
            c = float(candles[i-1]["close"])
            trs.append(max(h - l, abs(h - c), abs(l - c)))
        atr   = sum(trs[-period:]) / period
        price = float(candles[-1]["close"])
        return (atr / price * 100) if price > 0 else 3.0
    except (ValueError, TypeError, ZeroDivisionError):
        return 3.0


def register(app):
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("onchain",   cmd_onchain))
    app.add_handler(CommandHandler("regime",    cmd_regime))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("backtest",  cmd_backtest))
    app.add_handler(CommandHandler("liquidity", cmd_liquidity))
    app.add_handler(CommandHandler("events",    cmd_events))
    app.add_handler(CommandHandler("drift",     cmd_drift))

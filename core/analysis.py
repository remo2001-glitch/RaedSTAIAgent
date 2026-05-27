"""
📡 رائد — handlers/analysis.py
أوامر: /news /onchain /regime /backtest /signal /liquidity /events /drift
- جميع النتائج محمية من None
- جميع النصوص مُنظَّفة من رموز Markdown قبل الإرسال
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from core.user_manager import user_manager as _um
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context):
    return context.bot_data.get("raed_engine")


def _clean_md(text: str) -> str:
    """يُنظّف النص من رموز Markdown v1 الخطرة خارج bold."""
    if not text:
        return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        parts = line.split("*")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:  # خارج bold — نُنظّف
                part = part.replace("_", " ").replace("`", "'")
            result.append(part)
        clean.append("*".join(result))
    return "\n".join(clean)


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


# ════════════════════════════════════════════════════════════════
# /news
# ════════════════════════════════════════════════════════════════
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args     = context.args or []
    symbols  = [a.upper() for a in args] or ["BTC", "ETH", "BNB"]
    sym_str  = ", ".join(symbols)
    msg = await update.message.reply_text(
        f"📰 جاري جلب وتحليل الأخبار لـ {sym_str}...\n"
        "⏳ قد يستغرق ١٠-٢٠ ثانية — يُرجى الانتظار"
    )

    try:
        items = await engine.data_layer.get_news(
            currencies=",".join(symbols), limit=20)
        items = items or []

        try:
            analysis = await engine.news_engine.analyze(items, symbols)
            if not analysis or not isinstance(analysis, dict):
                analysis = engine.news_engine._neutral_analysis()
        except Exception as e:
            logger.warning(f"news analyze error: {e}")
            analysis = engine.news_engine._neutral_analysis()

        try:
            if items:
                engine.event_risk.ingest_news_events(items)
        except Exception:
            pass

        text = engine.news_engine.format_ar(items, analysis)
        text = _clean_md(text)
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
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text("🔗 جاري جلب بيانات On-Chain...")
    try:
        data  = await engine.data_layer.get_onchain()
        fear  = await engine.data_layer.get_fear_greed()
        data  = data  or {"tvl": 0, "protocols": []}
        fear  = fear  or {"value": 50, "label_ar": "محايد"}
        top_p = (data.get("protocols") or [])[:5]
        tvl   = float(data.get("tvl") or 0)

        lines = [
            "🔗 *تحليل On-Chain — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 إجمالي TVL: ${tvl/1e9:.2f}B",
            f"😨 Fear & Greed: {fear.get('value',50)} — {fear.get('label_ar','محايد')}",
            "",
            "🏆 *أكبر البروتوكولات*",
        ]
        for i, p in enumerate(top_p, 1):
            tvl_b = float(p.get("tvl") or 0) / 1e9
            name  = str(p.get("name","")).replace("_"," ")
            lines.append(f"{i}\\. {name} — ${tvl_b:.2f}B")
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
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(
        f"📊 جاري تحليل حالة السوق لـ {symbol}...")

    try:
        candles = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        candles = candles or []
        fear    = await engine.data_layer.get_fear_greed()
        fear    = fear    or {"value": 50}

        if len(candles) < 30:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"أعد المحاولة بعد دقيقة")
            return

        result = engine.regime_detector.detect(
            candles, btc_dominance=50.0,
            fear_greed=int(fear.get("value") or 50))
        text = _clean_md(engine.regime_detector.format_ar(result))
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_regime: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل السوق: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════════
# /signal
# ════════════════════════════════════════════════════════════════
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(
        f"📡 جاري تحليل {symbol} عبر ٥ مصادر...\n"
        "⏳ قد يستغرق ١٠-٢٠ ثانية — يُرجى الانتظار"
    )

    try:
        candles  = await engine.data_layer.get_ohlcv(symbol, "1d", 250)
        candles  = candles or []
        onchain  = await engine.data_layer.get_onchain()     or {}
        fear     = await engine.data_layer.get_fear_greed()  or {"value": 50}
        news_raw = await engine.data_layer.get_news(currencies=symbol) or []

        try:
            news_an = await engine.news_engine.analyze(news_raw, [symbol])
            news_an = news_an or {}
        except Exception:
            news_an = {}

        if len(candles) < 50:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"أعد المحاولة بعد دقيقة")
            return

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
        atr_pct  = _calc_atr(candles)
        price    = float(candles[-1]["close"]) if candles else 0
        risk     = engine.risk_engine.assess(
            symbol=symbol, direction=signal.direction,
            confidence=signal.confidence, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
        )

        parts = [
            _clean_md(engine.signal_layer.format_ar(signal)),
            _clean_md(engine.regime_detector.format_ar(regime)),
            _clean_md(engine.strategy_router.format_ar(strategy, params)),
            _clean_md(engine.risk_engine.format_assessment_ar(risk, symbol)),
        ]
        await msg.edit_text("\n\n".join(parts), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_signal: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل {symbol}: {str(e)[:100]}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args     = context.args or []
    symbol   = args[0].upper() if args else "BTC"
    strategy = args[1].lower() if len(args) > 1 else "trend_following"
    valid    = ["trend_following", "mean_reversion", "breakout"]

    if strategy not in valid:
        await update.message.reply_text(
            "⚠️ الاستراتيجيات المتاحة:\n" +
            "\n".join(f"• {s.replace('_',' ')}" for s in valid))
        return

    msg = await update.message.reply_text(
        f"⏳ جاري Backtest لـ {symbol} — ٣ سنوات بيانات حقيقية\n"
        "🔬 قد يستغرق ٣٠-٦٠ ثانية — يُرجى عدم تكرار الأمر"
    )

    try:
        price_data = await engine.data_layer.get_historical_prices(symbol, days=1095)
        price_data = price_data or []

        if len(price_data) < 90:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} التاريخية غير كافية\n"
                f"({len(price_data)} يوم متاح — الحد الأدنى 90)\n"
                f"أعد المحاولة بعد دقيقتين")
            return

        result = await engine.backtest_engine.run(symbol, price_data, strategy)
        if result.win_rate > 0:
            engine.drift_monitor.update_baseline(result.win_rate / 100)

        text = _clean_md(engine.backtest_engine.format_ar(result))
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_backtest: {e}")
        await msg.edit_text(f"❌ خطأ في Backtest: {str(e)[:100]}")


async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(f"🔬 جاري تحليل السيولة لـ {symbol}...")

    try:
        profile = await engine.microstructure.analyze(symbol, order_size_usd=1000)
        walls   = await engine.microstructure.detect_walls(symbol)
        text    = _clean_md(engine.microstructure.format_ar(profile))

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
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    try:
        state   = engine.event_risk.assess()
        text_ev = engine.event_risk.format_upcoming_ar(hours=72)
        lines   = [
            "📅 *فلتر مخاطر الأحداث — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            state.message_ar,
            "",
            text_ev,
        ]
        await update.message.reply_text(
            _clean_md("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_events: {e}")
        await update.message.reply_text("❌ خطأ في جلب الأحداث")


# ════════════════════════════════════════════════════════════════
# /drift
# ════════════════════════════════════════════════════════════════
async def cmd_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    try:
        state = engine.drift_monitor.assess()
        text  = _clean_md(engine.drift_monitor.format_ar(state))
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_drift: {e}")
        await update.message.reply_text("❌ خطأ في تحليل النموذج")



async def cmd_analyze(update, context):
    engine  = context.bot_data.get("raed_engine")
    if not engine: await update.message.reply_text("test"); return
    user_id = update.effective_user.id
    um      = getattr(engine, "user_manager", None)
    if um and not um.is_premium(user_id):
        lines = [
            "locked تحليل عميق",
            "للترقية: /premium",
        ]
        await update.message.reply_text("\n".join(lines)); return
    args   = context.args or []
    symbol = args[0].upper() if args else ""
    if not symbol:
        await update.message.reply_text("مثال: /analyze BTC"); return
    msg = await update.message.reply_text(f"جاري التحليل لـ {symbol}...")
    try:
        import asyncio
        price_d, candles, fear = await asyncio.gather(
            engine.data_layer.get_price(symbol),
            engine.data_layer.get_ohlcv(symbol, "1d", 100),
            engine.data_layer.get_fear_greed(),
            return_exceptions=True)
        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list)  else []
        fear    = fear    if isinstance(fear, dict)     else {"value":50}
        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("price_change_percentage_24h") or 0)
        volume_24h = float(price_d.get("volume_24h") or 0)
        market_cap = float(price_d.get("market_cap") or 0)
        rsi = 50.0
        if len(candles) >= 14:
            try:
                px  = [float(c.get("close", c.get("price",0))) for c in candles[-15:]]
                gs  = [max(0, px[i]-px[i-1]) for i in range(1,len(px))]
                ls  = [max(0, px[i-1]-px[i]) for i in range(1,len(px))]
                ag  = sum(gs[-14:])/14; al = sum(ls[-14:])/14
                rsi = 100-(100/(1+ag/al)) if al > 0 else 50
            except Exception: pass
        regime = engine.regime_detector.detect(candles, fear_greed=fear_val) if len(candles)>=30 else None
        regime_desc = regime.description_ar if regime else "unknown"
        candles_summary = ""
        if len(candles) >= 5:
            p5 = [float(c.get("close",c.get("price",0))) for c in candles[-5:]]
            trend5 = "up" if p5[-1]>p5[0] else "down"
            candles_summary = f"last 5 candles {trend5}"
        analysis = await engine.news_engine.analyze_symbol(
            symbol=symbol, price=price, price_change_24h=change_24h,
            volume_24h=volume_24h, market_cap=market_cap, rsi=rsi,
            fear_greed=fear_val, regime_desc=regime_desc,
            candles_summary=candles_summary)
        parts = [
            f"تحليل {symbol}",
            f"السعر: ${price:,.4f}",
            f"RSI: {rsi:.0f} | Fear: {fear_val}",
            regime_desc,
            "---",
            analysis,
        ]
        full = "\n".join(parts)
        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await update.message.reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"error: {str(e)[:100]}")


async def cmd_chart(update, context):
    engine  = context.bot_data.get("raed_engine")
    if not engine: return
    user_id = update.effective_user.id
    um      = getattr(engine, "user_manager", None)
    if um and not um.is_premium(user_id):
        await update.message.reply_text("locked /premium"); return
    msg = await update.message.reply_text("analyzing chart...")
    try:
        photo = update.message.photo
        if photo:
            file = await photo[-1].get_file()
        elif update.message.document:
            file = await update.message.document.get_file()
        else:
            await msg.edit_text("send image"); return
        image_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""
        symbol  = ""
        for word in caption.split():
            w = word.strip("/").upper()
            if len(w) >= 2 and w.isalpha() and w not in ("ANALYZE","CHART"):
                symbol = w; break
        analysis = await engine.news_engine.analyze_chart_image(
            image_data=bytes(image_bytes), symbol=symbol)
        full = f"chart analysis {symbol}\n{analysis}"
        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await update.message.reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"error: {str(e)[:100]}")

def register(app):
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("onchain",   cmd_onchain))
    app.add_handler(CommandHandler("regime",    cmd_regime))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("backtest",  cmd_backtest))
    app.add_handler(CommandHandler("liquidity", cmd_liquidity))
    app.add_handler(CommandHandler("events",    cmd_events))
    app.add_handler(CommandHandler("drift",     cmd_drift))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE,
        cmd_chart))

"""
📡 رائد — handlers/analysis.py v2
أوامر: /news /onchain /regime /backtest /signal /liquidity /events /drift
       /analyze /quicksignal /upgrade /chart

الإصلاحات:
- import asyncio في الأعلى (ليس داخل الدوال)
- تحقق حقيقي من صلاحية الباقة في /analyze و /chart
- رسائل خطأ عربية كاملة
- RSI threshold مُصحَّح: 30/70 بدلاً من 35/65
- حماية من AttributeError في walls.buy_walls
- _clean_md مُحسَّن لا يُفسد أسماء العملات
- cmd_liquidity محمي من None في walls
- تحقق من price قبل حساب مستويات الدخول/الخروج
"""

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from core.state_manager import state_manager as _sm
from core.middleware    import require_tier
from core.user_manager import user_manager as _um
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context):
    return context.bot_data.get("raed_engine")


def _clean_md(text: str) -> str:
    """
    يُنظّف النص من رموز Markdown v1 الخطرة.
    لا يُعدِّل الأرقام العشرية أو أسماء العملات.
    """
    if not text:
        return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        parts = line.split("*")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # خارج bold — نُنظّف _ فقط إذا لم تكن داخل رقم أو رمز عملة
                # نستبدل _ المحاطة بمسافات فقط (ليست جزءاً من كلمة)
                import re
                part = re.sub(r'(?<!\w)_(?!\w)', ' ', part)
                part = part.replace("`", "'")
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


def _calc_rsi(candles: list, period: int = 14) -> float:
    """حساب RSI دقيق."""
    if len(candles) < period + 1:
        return 50.0
    try:
        px  = [float(c.get("close", c.get("price", 0))) for c in candles[-(period+1):]]
        if any(p <= 0 for p in px):
            return 50.0
        gs  = [max(0.0, px[i] - px[i-1]) for i in range(1, len(px))]
        ls  = [max(0.0, px[i-1] - px[i]) for i in range(1, len(px))]
        ag  = sum(gs) / period
        al  = sum(ls) / period
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + ag / al))
    except Exception:
        return 50.0


# ════════════════════════════════════════════════════════════════
# /news
# ════════════════════════════════════════════════════════════════
@require_tier("news")
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
        if not text:
            text = "📰 لا توجد أخبار متاحة حالياً. حاول لاحقاً."
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"cmd_news: {e}")
        await msg.edit_text("❌ خطأ في جلب الأخبار. حاول مجدداً")


# ════════════════════════════════════════════════════════════════
# /onchain
# ════════════════════════════════════════════════════════════════
@require_tier("onchain")
async def cmd_onchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text("🔗 جاري جلب بيانات On-Chain...")
    try:
        data, fear = await asyncio.gather(
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            return_exceptions=True
        )
        data  = data  if isinstance(data, dict) else {"tvl": 0, "protocols": []}
        fear  = fear  if isinstance(fear, dict) else {"value": 50, "label_ar": "محايد"}
        top_p = (data.get("protocols") or [])[:5]
        tvl   = float(data.get("tvl") or 0)
        fear_val = int(fear.get("value") or 50)

        # تفسير Fear & Greed
        if fear_val <= 20:
            fear_emoji = "😱"
        elif fear_val <= 40:
            fear_emoji = "😨"
        elif fear_val <= 60:
            fear_emoji = "😐"
        elif fear_val <= 80:
            fear_emoji = "😊"
        else:
            fear_emoji = "🤑"

        lines = [
            "🔗 *تحليل On-Chain — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 إجمالي TVL: ${tvl/1e9:.2f}B",
            f"{fear_emoji} Fear & Greed: {fear_val} — {fear.get('label_ar', 'محايد')}",
        ]

        if top_p:
            lines += ["", "🏆 *أكبر البروتوكولات*"]
            for i, p in enumerate(top_p, 1):
                tvl_b = float(p.get("tvl") or 0) / 1e9
                name  = str(p.get("name", "")).replace("_", " ")
                lines.append(f"{i}. {name} — ${tvl_b:.2f}B")
        else:
            lines += ["", "⚠️ بيانات البروتوكولات غير متاحة حالياً"]

        lines += ["", "📡 المصدر: DeFiLlama | 🤖 رائد"]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_onchain: {e}")
        await msg.edit_text("❌ خطأ في جلب بيانات On-Chain. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /regime
# ════════════════════════════════════════════════════════════════
@require_tier("regime")
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
        candles, fear = await asyncio.gather(
            engine.data_layer.get_ohlcv(symbol, "1d", 250),
            engine.data_layer.get_fear_greed(),
            return_exceptions=True
        )
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}

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
        await msg.edit_text(f"❌ خطأ في تحليل السوق. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /signal
# ════════════════════════════════════════════════════════════════
@require_tier("signal")
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
        candles, onchain, fear, news_raw = await asyncio.gather(
            engine.data_layer.get_ohlcv(symbol, "1d", 250),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_news(currencies=symbol),
            return_exceptions=True
        )
        candles  = candles  if isinstance(candles, list) else []
        onchain  = onchain  if isinstance(onchain, dict) else {}
        fear     = fear     if isinstance(fear, dict)    else {"value": 50}
        news_raw = news_raw if isinstance(news_raw, list) else []

        try:
            news_an = await engine.news_engine.analyze(news_raw, [symbol])
            news_an = news_an if isinstance(news_an, dict) else {}
        except Exception:
            news_an = {}

        if len(candles) < 50:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"أعد المحاولة بعد دقيقة")
            return

        fear_val = int(fear.get("value") or 50)
        regime   = engine.regime_detector.detect(candles, fear_greed=fear_val)

        sentiment = 0.0
        if news_an:
            raw_sent = news_an.get("sentiment_score")
            if raw_sent is not None:
                try:
                    sentiment = float(raw_sent)
                except (ValueError, TypeError):
                    sentiment = 0.0

        signal = engine.signal_layer.generate(
            symbol=symbol, candles=candles, onchain_data=onchain,
            news_sentiment=sentiment,
            backtest_win_rate=0.55,
            macro_data={"fear_greed": fear_val},
            regime=regime,
        )
        strategy, params = engine.strategy_router.select(regime, signal)
        atr_pct = _calc_atr(candles)
        price   = float(candles[-1]["close"]) if candles else 0.0
        risk    = engine.risk_engine.assess(
            symbol=symbol, direction=signal.direction,
            confidence=signal.confidence, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
        )

        # تحذير RSI/اتجاه متعارض
        rsi = _calc_rsi(candles)
        warning = ""
        if signal.direction == "short" and rsi < 30:
            warning = "\n\n⚠️ *تنبيه:* RSI في ذروة البيع مع إشارة بيع — خطر انعكاس مرتفع"
        elif signal.direction == "long" and rsi > 70:
            warning = "\n\n⚠️ *تنبيه:* RSI في ذروة الشراء مع إشارة شراء — تحقق من التوقيت"

        parts = [
            _clean_md(engine.signal_layer.format_ar(signal)),
            _clean_md(engine.regime_detector.format_ar(regime)),
            _clean_md(engine.strategy_router.format_ar(strategy, params)),
            _clean_md(engine.risk_engine.format_assessment_ar(risk, symbol)),
        ]
        full_text = "\n\n".join(parts) + warning
        await msg.edit_text(full_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_signal: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل {symbol}. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /backtest
# ════════════════════════════════════════════════════════════════
@require_tier("backtest")
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
            "⚠️ الاستراتيجيات المتاحة:\n"
            "• trend_following (الاتجاه — افتراضي)\n"
            "• mean_reversion (الارتداد)\n"
            "• breakout (الاختراق)\n\n"
            "مثال: /backtest BTC trend_following")
        return

    strategy_ar = {
        "trend_following": "اتباع الاتجاه",
        "mean_reversion":  "الارتداد للمتوسط",
        "breakout":        "الاختراق",
    }
    msg = await update.message.reply_text(
        f"⏳ جاري Backtest لـ {symbol} — {strategy_ar[strategy]}\n"
        "🔬 ٣ سنوات بيانات حقيقية — قد يستغرق ٣٠-٦٠ ثانية"
    )

    try:
        price_data = await engine.data_layer.get_historical_prices(symbol, days=1095)
        price_data = price_data if isinstance(price_data, list) else []

        if len(price_data) < 90:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} التاريخية غير كافية\n"
                f"({len(price_data)} يوم متاح — الحد الأدنى 90 يوماً)\n"
                f"أعد المحاولة بعد دقيقتين")
            return

        result = await engine.backtest_engine.run(symbol, price_data, strategy)
        if result.win_rate > 0:
            engine.drift_monitor.update_baseline(result.win_rate / 100)

        text = _clean_md(engine.backtest_engine.format_ar(result))
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_backtest: {e}")
        await msg.edit_text("❌ خطأ في Backtest. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /liquidity
# ════════════════════════════════════════════════════════════════
@require_tier("liquidity")
async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(f"🔬 جاري تحليل السيولة لـ {symbol}...")

    try:
        profile, walls = await asyncio.gather(
            engine.microstructure.analyze(symbol, order_size_usd=1000),
            engine.microstructure.detect_walls(symbol),
            return_exceptions=True
        )

        if not profile or isinstance(profile, Exception):
            await msg.edit_text(f"⚠️ بيانات السيولة لـ {symbol} غير متاحة حالياً")
            return

        # تمرير walls لـ format_ar مباشرة
        walls_safe = walls if not isinstance(walls, Exception) else None
        text = _clean_md(engine.microstructure.format_ar(profile, walls=walls_safe))
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_liquidity: {e}")
        await msg.edit_text("❌ خطأ في تحليل السيولة. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /events
# ════════════════════════════════════════════════════════════════
@require_tier("events")
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
        await update.message.reply_text("❌ خطأ في جلب الأحداث. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /drift
# ════════════════════════════════════════════════════════════════
@require_tier("drift")
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
        await update.message.reply_text("❌ خطأ في تحليل النموذج. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /analyze — ذهبي+
# ════════════════════════════════════════════════════════════════
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق من صلاحية الباقة
    if not _sm.can_use_command(user_id, "analyze"):
        await update.message.reply_text(
            "🔒 *التحليل العميق — ذهبي وماسي فقط*\n\n"
            "هذا الأمر يتطلب باقة ذهبي أو أعلى.\n"
            "للترقية: /upgrade",
            parse_mode="Markdown"
        )
        return

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    symbol = args[0].upper() if args else ""
    if not symbol:
        await update.message.reply_text(
            "📊 مثال الاستخدام: /analyze BTC\n"
            "أو: /analyze ETH"
        )
        return

    msg = await update.message.reply_text(f"🧠 جاري التحليل العميق لـ {symbol}...")

    try:
        price_d, candles, fear = await asyncio.gather(
            engine.data_layer.get_price(symbol),
            engine.data_layer.get_ohlcv(symbol, "1d", 100),
            engine.data_layer.get_fear_greed(),
            return_exceptions=True
        )
        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("price_change_percentage_24h") or 0)
        volume_24h = float(price_d.get("volume_24h") or 0)
        market_cap = float(price_d.get("market_cap") or 0)

        if price <= 0:
            await msg.edit_text(f"❌ لم أجد سعراً لـ {symbol}. تحقق من الرمز")
            return

        rsi = _calc_rsi(candles)

        regime = None
        regime_desc = "غير محدد"
        if len(candles) >= 30:
            try:
                regime = engine.regime_detector.detect(candles, fear_greed=fear_val)
                regime_desc = regime.description_ar
            except Exception:
                pass

        candles_summary = ""
        if len(candles) >= 5:
            p5 = [float(c.get("close", c.get("price", 0))) for c in candles[-5:]]
            if p5[-1] > p5[0]:
                candles_summary = "آخر ٥ شموع: اتجاه صاعد"
            else:
                candles_summary = "آخر ٥ شموع: اتجاه هابط"

        analysis = await engine.news_engine.analyze_symbol(
            symbol=symbol, price=price, price_change_24h=change_24h,
            volume_24h=volume_24h, market_cap=market_cap, rsi=rsi,
            fear_greed=fear_val, regime_desc=regime_desc,
            candles_summary=candles_summary)

        change_sign = "+" if change_24h >= 0 else ""
        parts = [
            f"🧠 *تحليل {symbol} — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: ${price:,.4f}  ({change_sign}{change_24h:.2f}%)",
            f"📊 RSI: {rsi:.0f} | Fear & Greed: {fear_val}",
            f"🌍 السوق: {regime_desc}",
            "━━━━━━━━━━━━━━━━━━",
            analysis,
            "",
            "⚠️ هذا التحليل استرشادي — القرار للمستخدم",
        ]
        full = _clean_md("\n".join(parts))

        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await update.message.reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_analyze: {e}")
        await msg.edit_text("❌ خطأ في التحليل العميق. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /chart — معالجة الأوامر (ماسي)
# ════════════════════════════════════════════════════════════════
async def cmd_chart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /chart — تعليمات تحليل الشارت البصري (ماسي)
    """
    user_id = update.effective_user.id
    if not _sm.can_use_command(user_id, "chart"):
        await update.message.reply_text(
            "💎 *تحليل الشارت البصري — ماسي فقط*\n\n"
            "هذا الأمر متاح لمشتركي الباقة الماسية.\n"
            "للترقية: /upgrade",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "📊 *تحليل الشارت البصري*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📌 *كيفية الاستخدام:*\n"
        "١. أرفع صورة الشارت كصورة\n"
        "٢. اكتب اسم العملة كتعليق (مثال: BTC)\n"
        "٣. رائد يُحللها تلقائياً بالذكاء الاصطناعي\n\n"
        "✅ الصيغ المدعومة: PNG و JPG\n"
        "⏳ وقت التحليل: ١٥-٣٠ ثانية",
        parse_mode="Markdown")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور المُرسَلة لتحليل الشارت."""
    user_id = update.effective_user.id
    if not _sm.can_use_command(user_id, "chart"):
        await update.message.reply_text(
            "💎 تحليل الشارت البصري متاح لمشتركي الباقة الماسية فقط.\n"
            "للترقية: /upgrade"
        )
        return

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text("🔍 جاري تحليل الشارت...")

    try:
        photo = update.message.photo
        if photo:
            file = await photo[-1].get_file()
        elif update.message.document:
            file = await update.message.document.get_file()
        else:
            await msg.edit_text(
                "⚠️ يُرجى إرسال صورة الشارت.\n"
                "اكتب /chart لمعرفة طريقة الاستخدام")
            return

        image_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""
        symbol  = ""
        for word in caption.split():
            w = word.strip("/").upper()
            if len(w) >= 2 and w.isalpha() and w not in ("ANALYZE", "CHART"):
                symbol = w
                break

        analysis = await engine.news_engine.analyze_chart_image(
            image_data=bytes(image_bytes), symbol=symbol)

        sym_label = f" — {symbol}" if symbol else ""
        full = _clean_md(
            f"📊 *تحليل الشارت البصري{sym_label}*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{analysis}\n\n"
            f"⚠️ التحليل استرشادي — القرار للمستخدم"
        )
        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await update.message.reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_chart: {e}")
        await msg.edit_text("❌ خطأ في تحليل الشارت. حاول مجدداً")


# ════════════════════════════════════════════════════════════════
# /quicksignal — متاح للجميع
# ════════════════════════════════════════════════════════════════
async def cmd_quicksignal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /quicksignal [عملة] — تحليل أولي سريع مع نقاط الدخول والخروج
    متاح لجميع الباقات
    """
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    symbol = args[0].upper() if args else "BTC"
    msg    = await update.message.reply_text(
        f"🔍 جاري التحليل الأولي لـ {symbol}...")

    try:
        price_d, candles, fear = await asyncio.gather(
            engine.data_layer.get_price(symbol),
            engine.data_layer.get_ohlcv(symbol, "1d", 100),
            engine.data_layer.get_fear_greed(),
            return_exceptions=True
        )

        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("price_change_percentage_24h") or 0)

        if price <= 0:
            await msg.edit_text(
                f"❌ لم أجد سعراً لـ {symbol}.\n"
                f"تحقق من الرمز وأعد المحاولة")
            return

        rsi = _calc_rsi(candles)

        # مستويات الدعم والمقاومة (20 شمعة)
        support = resistance = 0.0
        if len(candles) >= 20:
            lows  = [float(c.get("low",  c.get("close", 0))) for c in candles[-20:]
                     if float(c.get("low", c.get("close", 0))) > 0]
            highs = [float(c.get("high", c.get("close", 0))) for c in candles[-20:]
                     if float(c.get("high", c.get("close", 0))) > 0]
            if lows and highs:
                support    = min(lows)  * 0.99
                resistance = max(highs) * 1.01

        # توصية بناءً على RSI + Fear & Greed (معايير مُحسَّنة)
        if rsi < 30 and fear_val < 40:
            direction = "🟢 شراء محتمل"
            entry     = price * 0.99
            tp1       = price * 1.05
            tp2       = price * 1.10
            sl        = price * 0.95
        elif rsi > 70 and fear_val > 60:
            direction = "🔴 بيع محتمل"
            entry     = price * 1.01
            tp1       = price * 0.95
            tp2       = price * 0.90
            sl        = price * 1.05
        elif 30 <= rsi <= 45 and fear_val < 50:
            direction = "🟡 شراء محتاط"
            entry     = price * 0.99
            tp1       = price * 1.04
            tp2       = price * 1.08
            sl        = price * 0.96
        elif 55 <= rsi <= 70 and fear_val > 50:
            direction = "🟠 بيع محتاط"
            entry     = price * 1.01
            tp1       = price * 0.96
            tp2       = price * 0.92
            sl        = price * 1.04
        else:
            direction = "⚪ انتظار"
            entry     = price
            tp1       = price * 1.05
            tp2       = price * 1.08
            sl        = price * 0.96

        regime_desc = "غير محدد"
        if len(candles) >= 30:
            try:
                regime = engine.regime_detector.detect(candles, fear_greed=fear_val)
                regime_desc = regime.description_ar
            except Exception:
                pass

        change_sign = "+" if change_24h >= 0 else ""
        lines = [
            f"📊 *التحليل الأولي — {symbol}*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: ${price:,.4f} ({change_sign}{change_24h:.2f}%)",
            f"🌍 السوق: {regime_desc}",
            f"📈 RSI: {rsi:.0f} | Fear & Greed: {fear_val}",
            "",
            f"🎯 *التوصية: {direction}*",
            "",
            "📍 *مناطق الدخول والخروج*",
            f"• نقطة الدخول: ${entry:,.4f}",
            f"• هدف ١:       ${tp1:,.4f} ({(tp1/price-1)*100:+.1f}%)",
            f"• هدف ٢:       ${tp2:,.4f} ({(tp2/price-1)*100:+.1f}%)",
            f"• وقف الخسارة: ${sl:,.4f} ({(sl/price-1)*100:+.1f}%)",
        ]
        if support > 0 and resistance > 0:
            lines += [
                "",
                "🏗️ *المستويات الرئيسية*",
                f"• دعم:    ${support:,.4f}",
                f"• مقاومة: ${resistance:,.4f}",
            ]
        lines += [
            "",
            "💡 للتحليل العميق الكامل: /analyze (ذهبي+)",
            "⚠️ هذا تحليل استرشادي — القرار للمستخدم",
        ]

        await msg.edit_text(
            _clean_md("\n".join(lines)), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_quicksignal: {e}", exc_info=True)
        await msg.edit_text("❌ خطأ في التحليل الأولي. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /upgrade — جدول الباقات
# ════════════════════════════════════════════════════════════════
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state_manager import state_manager as _sm, TIERS
    user_id   = update.effective_user.id
    cur_tier  = _sm.get_tier(user_id)
    tier_info = TIERS[cur_tier]

    lines = [
        "💎 *باقات رائد للتداول الذكي*",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🆓 *مجاني — مجاناً*",
        "• /quicksignal — تحليل أولي سريع",
        "• تداول آلي مجاني ٣٠ يوم",
        "• مسح تلقائي للفرص",
        "• ١٥ عملة في المسح",
        "",
        "🥈 *فضي — $9/شهر*",
        "• كل المجاني +",
        "• /signal — إشارة شاملة ٥ مصادر",
        "• /news — تحليل الأخبار بالذكاء الاصطناعي",
        "• /regime — حالة السوق",
        "• /backtest — اختبار تاريخي ٣ سنوات",
        "• ٣٥ عملة في المسح",
        "",
        "🥇 *ذهبي — $29/شهر ⭐ الأكثر طلباً*",
        "• كل الفضي +",
        "• /analyze — تحليل عميق بالذكاء الاصطناعي",
        "• /liquidity — تحليل السيولة المتقدم",
        "• /onchain — تحليل On-Chain",
        "• /planweek و /planmonth — تخطيط ذكي",
        "• ١٠٠ عملة في المسح",
        "",
        "💎 *ماسي — $99/شهر*",
        "• كل الذهبي +",
        "• /chart — تحليل شارت بصري",
        "• تحليل كمي متقدم",
        "• ٣٠٠ عملة في المسح",
        "• دعم مباشر ٢٤/٧",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if cur_tier == "admin":
        lines.append(f"✅ باقتك: {tier_info['name']} — صلاحيات كاملة")
    elif cur_tier == "diamond":
        lines.append(f"✅ باقتك: {tier_info['name']} — أعلى باقة")
    else:
        lines.append(f"📌 باقتك الحالية: {tier_info['name']}")
        lines.append("للترقية: تواصل مع الدعم الفني")

    lines += ["", "📞 *الدعم الفني*", "للاشتراك والاستفسارات: قريباً"]

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown")


# ════════════════════════════════════════════════════════════════
# تسجيل الـ handlers
# ════════════════════════════════════════════════════════════════
def register(app):
    logger.info("analysis handlers: جاري التسجيل...")
    app.add_handler(CommandHandler("news",         cmd_news))
    app.add_handler(CommandHandler("onchain",      cmd_onchain))
    app.add_handler(CommandHandler("regime",       cmd_regime))
    app.add_handler(CommandHandler("signal",       cmd_signal))
    app.add_handler(CommandHandler("backtest",     cmd_backtest))
    app.add_handler(CommandHandler("liquidity",    cmd_liquidity))
    app.add_handler(CommandHandler("events",       cmd_events))
    app.add_handler(CommandHandler("drift",        cmd_drift))
    app.add_handler(CommandHandler("analyze",      cmd_analyze))
    app.add_handler(CommandHandler("quicksignal",  cmd_quicksignal))
    app.add_handler(CommandHandler("upgrade",      cmd_upgrade))
    app.add_handler(CommandHandler("chart",        cmd_chart_cmd))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE,
        cmd_chart))
    logger.info("✅ analysis handlers: تم تسجيل جميع الأوامر")

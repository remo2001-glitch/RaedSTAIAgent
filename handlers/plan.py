"""
📋 رائد — handlers/plan.py
أوامر: /plan_month /plan_week /portfolio /stats /approve /reject
- جميع النتائج محمية من None
- Markdown آمن — لا أخطاء تنسيق
"""

import asyncio
import logging
import re


def _fmt_price(price: float) -> str:
    if price <= 0:       return "$0"
    elif price >= 1000:  return f"${price:,.2f}"
    elif price >= 1:     return f"${price:,.4f}"
    elif price >= 0.001: return f"${price:.6f}"
    elif price >= 1e-6:  return f"${price:.8f}"
    else:                return f"${price:.10f}"
import uuid
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from core.state_manager import state_manager as _sm
from core.middleware    import require_tier
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
@require_tier("planmonth")
async def cmd_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    if context.user_data.get("planmonth_running"):
        await update.message.reply_text(
            "⏳ جاري بناء الخطة الشهرية بالفعل — يُرجى الانتظار")
        return
    context.user_data["planmonth_running"] = True

    args      = context.args or []
    scan_mode = len(args) == 0
    if scan_mode:
        symbols    = []   # سيُحدَّد بعد جلب top_coins
        sym_str    = "مسح شامل"
        plan_label = "مسح شامل للسوق"
    else:
        symbols    = [a.upper() for a in args[:3]]
        sym_str    = ", ".join(symbols)
        plan_label = f"خطة مخصصة لـ {sym_str}"

    msg = await update.message.reply_text(
        f"📋 جاري بناء {plan_label}...\n"
        + ("⏳ المسح الشامل يستغرق ٣٠-٦٠ ثانية — يُرجى الانتظار" if scan_mode
           else "⏳ قد يستغرق ٢٠-٤٠ ثانية — يُرجى عدم تكرار الأمر")
    )
    try:

        # ── ١. بيانات أساسية متوازية ──────────────────────────
        fear, onchain, btc_c = await asyncio.gather(
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_ohlcv("BTC", "1d", 200),
            return_exceptions=True,
        )
        fear    = fear    if isinstance(fear, dict)  else {"value": 50, "label_ar": "محايد"}
        onchain = onchain if isinstance(onchain, dict) else {}
        btc_c   = btc_c   if isinstance(btc_c, list)  else []
        fear_val = int((fear or {}).get("value") or 50)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        # ── ٢. OHLCV لجميع العملات متوازية ───────────────────
        ohlcv_all = await asyncio.gather(
            *[engine.data_layer.get_ohlcv(sym, "1d", 200) for sym in symbols],
            return_exceptions=True,
        )

        # ── ٣. أخبار مرة واحدة لجميع العملات ─────────────────
        news_raw = await engine.data_layer.get_news(
            currencies=",".join(symbols), limit=10) or []
        try:
            news_an = await engine.news_engine.analyze(news_raw, symbols)
            news_an = news_an or {}
        except Exception:
            news_an = {}
        news_sentiment = float(news_an.get("sentiment_score") or 0)

        # ── ٤. بناء المرشحين ──────────────────────────────────
        # في scan_mode: نجلب top_coins ونفلتر أفضلها
        if scan_mode:
            from core.state_manager import state_manager as _sm_pm
            tier      = _sm_pm.get_tier(update.effective_user.id)
            coin_lim  = {"free": 15, "silver": 35, "gold": 100,
                         "diamond": 300, "admin": 300}.get(tier, 15)
            top_coins = await engine.data_layer.get_top_coins(limit=coin_lim)
            top_coins = top_coins if isinstance(top_coins, list) else []
            STABLES   = {
                "USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX",
                "USDS","FDUSD","USDE","USDD","GUSD","LUSD","PYUSD",
                "CRVUSD","ALUSD","SUSD","MIM","USDB",
            }
            symbols   = [
                (c.get("symbol") or "").upper()
                for c in top_coins[:coin_lim]
                if (
                    (c.get("symbol") or "").upper() not in STABLES
                    and len((c.get("symbol") or "")) >= 2
                    and (c.get("symbol") or "").replace("_","").isalnum()
                )
            ][:10]   # أقصى 10 لتجنب timeout   # أقصى 10 في المسح لتجنب timeout

            # جلب OHLCV للعملات المُحدَّدة من المسح
            ohlcv_all = await asyncio.gather(
                *[engine.data_layer.get_ohlcv(sym, "1d", 200) for sym in symbols],
                return_exceptions=True,
            )

        MIN_CONFIDENCE_SCAN = 0.40  # فلترة في scan_mode
        candidates = []
        for i, sym in enumerate(symbols):
            candles = ohlcv_all[i] if isinstance(ohlcv_all[i], list) else []
            if len(candles) < 30:
                continue
            try:
                liq    = await engine.microstructure.analyze(sym, 1000)
                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_sentiment,
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                # جلب السعر الحالي
                price_d = await engine.data_layer.get_price(sym)
                price   = float((price_d or {}).get("price") or 0)
                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         _calc_atr(candles),
                    "liquidity_score": liq.liquidity_score if liq else 0.7,
                    "expected_return": _est_return(signal, regime),
                    "price":           price,
                })
            except Exception as e:
                logger.warning(f"plan_month {sym}: {e}")

        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        portfolio_val = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, event_multiplier=ev_mult)

        lines = [
            f"📋 *الخطة الشهرية — {plan_label}*",
            "━━━━━━━━━━━━━━━━━━",
            f"العملات: {', '.join(symbols)}",
            f"السوق: {regime.description_ar}",
            f"الثقة: {regime.confidence:.0%}",
            "",
        ]
        if ev_reason:
            lines.append(f"⚠️ تعديل أحداث: {_clean(ev_reason)} ({ev_mult:.0%})")
            lines.append("")

        # عرض أسعار العملات + مرجع BTC — دائماً حتى في Bear Market
        btc_ref     = f"📊 مرجع السوق: BTC ({regime.description_ar} {regime.confidence:.0%})"
        price_lines = []
        for sym_p in symbols:
            # البحث في candidates أولاً
            cand = next((c for c in candidates if c.get("symbol") == sym_p), None)
            price_v = float((cand or {}).get("price") or 0)
            # إذا لم يكن في candidates، جلب السعر مباشرة
            if price_v <= 0:
                try:
                    pd = await engine.data_layer.get_price(sym_p)
                    price_v = float((pd or {}).get("price") or 0)
                except Exception:
                    pass
            dir_ar = ("🟢 شراء" if (cand or {}).get("direction") == "long"
                      else "🔴 بيع" if (cand or {}).get("direction") == "short"
                      else "⚪ انتظار")
            conf = float((cand or {}).get("confidence") or 0)
            line = f"💎 *{sym_p}*"
            if price_v > 0:
                line += f" — ${price_v:,.2f}"
            if cand:
                line += f" | {dir_ar} | ثقة: {conf:.0%}"
            price_lines.append(line)

        lines += ["", "💰 *العملات المُحلَّلة*"] + price_lines
        lines += ["", btc_ref, ""]
        lines.append(_clean(engine.capital_engine.format_ar(allocation, regime)))
        # بناء جدول الشهر بناءً على حالة السوق الفعلية
        from core.regime_detector import Regime
        user_portfolio = engine.get_user_portfolio(update.effective_user.id)
        cash_pct       = 1.0 if regime.regime == Regime.BEAR_TREND else 0.3
        invest_pct     = 1.0 - cash_pct
        cash_amount    = user_portfolio * cash_pct
        invest_amount  = user_portfolio * invest_pct

        if regime.regime in (Regime.BEAR_TREND, Regime.DISTRIBUTION):
            week_plan = [
                f"• أسبوع ١: احتفظ بـ {cash_pct:.0%} سيولة (${cash_amount:,.0f}) — انتظر RSI < 30",
                "• أسبوع ٢: مراقبة مستويات الدعم ودخول تدريجي عند أول إشارة RSI",
                "• أسبوع ٣: راجع الإشارات — إذا Fear < 25 ابدأ التجميع التدريجي",
                "• أسبوع ٤: تقييم: هل تشكّل قاع؟ قرار الدخول الكامل",
            ]
        elif regime.regime in (Regime.BULL_TREND, Regime.ACCUMULATION):
            week_plan = [
                f"• أسبوع ١: دخول مبكر — {invest_pct:.0%} من المحفظة (${invest_amount:,.0f})",
                "• أسبوع ٢: مضاعفة المراكز الرابحة عند تأكيد الاتجاه",
                "• أسبوع ٣: رفع وقف الخسارة للتعادل على جميع المراكز",
                "• أسبوع ٤: جني جزء من الأرباح (30-50%) والاحتفاظ بالباقي",
            ]
        else:
            week_plan = [
                "• أسبوع ١: مراقبة — لا دخول حتى تتضح الإشارات",
                "• أسبوع ٢: دخول جزئي (25%) إذا RSI < 35 وFear < 35",
                "• أسبوع ٣: مراجعة وتعديل وقف الخسارة",
                "• أسبوع ٤: تقييم النتائج وقرار الاستمرار",
            ]

        lines += ["", "📅 *جدول الشهر المقترح*"] + week_plan + [
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]
        await msg.edit_text(_clean("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_month: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ خطأ في بناء الخطة: {str(e)[:100]}")
        except Exception:
            await update.message.reply_text("❌ خطأ في بناء الخطة الشهرية")
    finally:
        context.user_data["planmonth_running"] = False


@require_tier("planweek")
async def cmd_plan_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or []
    if args:
        symbols  = [a.upper() for a in args[:4]]
        sym_str2 = ", ".join(symbols)
        plan_label = f"خطة مخصصة لـ {sym_str2}"
    else:
        symbols    = ["BTC", "ETH"]
        sym_str2   = "BTC, ETH"
        plan_label = "خطة السوق العامة — BTC & ETH"
    msg = await update.message.reply_text(
        f"📅 جاري بناء {plan_label}...\n"
        "⏳ قد يستغرق ١٠-٢٠ ثانية — يُرجى الانتظار"
    )
    try:

        # ── ١. بيانات أساسية + OHLCV — كلها متوازية ─────────
        gathered = await asyncio.gather(
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_ohlcv("BTC", "1d", 200),
            *[engine.data_layer.get_ohlcv(sym, "1d", 100) for sym in symbols],
            engine.data_layer.get_news(currencies=",".join(symbols), limit=10),
            return_exceptions=True,
        )
        fear      = gathered[0] if isinstance(gathered[0], dict)  else {"value": 50, "label_ar": "محايد"}
        onchain   = gathered[1] if isinstance(gathered[1], dict)  else {}
        btc_c     = (gathered[2] if isinstance(gathered[2], list) else []) or []
        ohlcv_sym = [gathered[3+i] if isinstance(gathered[3+i], list) else []
                     for i in range(len(symbols))]
        news_raw  = gathered[3+len(symbols)] if isinstance(gathered[3+len(symbols)], list) else []
        fear_val  = int((fear or {}).get("value") or 50)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        # ── ٢. تحليل الأخبار مرة واحدة ────────────────────────
        try:
            news_an = await engine.news_engine.analyze(news_raw, symbols)
            news_an = news_an or {}
        except Exception:
            news_an = {}
        news_sentiment = float(news_an.get("sentiment_score") or 0)

        # ── ٣. أسعار لجميع العملات — متوازية ─────────────────
        price_results = await asyncio.gather(
            *[engine.data_layer.get_price(sym) for sym in symbols],
            return_exceptions=True,
        )

        lines = [
            "📅 *الخطة الأسبوعية — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"السوق: {regime.description_ar}",
            f"Fear & Greed: {fear_val} — {fear.get('label_ar','محايد')}",
            "",
        ]

        for i, sym in enumerate(symbols):
            candles = ohlcv_sym[i]
            if len(candles) < 20:
                lines.append(f"⚠️ {sym}: بيانات غير كافية")
                lines.append("")
                continue
            try:
                signal   = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_sentiment,
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                strat, _ = engine.strategy_router.select(regime, signal)
                price_d  = price_results[i] if not isinstance(price_results[i], Exception) else None
                price    = float((price_d or {}).get("price") or 0)

                dir_ar = ("🟢 شراء" if signal.direction == "long"
                          else "🔴 بيع" if signal.direction == "short"
                          else "⚪ انتظار")
                strat_name = strat.value.replace("_", " ")

                # حساب التغيير 24h
                change_24h = float((price_d or {}).get("change_24h") or 0)
                change_str = f" ({change_24h:+.2f}%)" if change_24h != 0 else ""

                # مستويات الدخول/الخروج المقترحة
                entry_lines = []
                if price > 0 and signal.direction != "neutral":
                    if signal.direction == "long":
                        entry  = price * 0.99
                        tp1    = price * 1.05
                        tp2    = price * 1.10
                        sl     = price * 0.95
                        entry_lines = [
                            f"  📍 دخول: ${entry:,.4f} | SL: ${sl:,.4f}",
                            f"  🎯 هدف١: ${tp1:,.4f} | هدف٢: ${tp2:,.4f}",
                        ]
                    else:
                        # عرض شروط الدخول المقترحة حتى في الانتظار
                        entry_lines = [
                            f"  ⏳ انتظار — شروط الدخول:",
                            f"  • RSI < 30 + إغلاق فوق EMA20",
                            f"  • أو Fear & Greed < 25",
                        ]

                lines += [
                    f"💎 *{sym}*" + (f" — {_fmt_price(price)}{change_str}" if price > 0 else ""),
                    f"  الإشارة: {dir_ar} | الثقة: {signal.confidence:.0%}",
                    f"  الاستراتيجية: {strat_name}",
                ] + entry_lines + [""]
            except Exception as e:
                logger.warning(f"plan_week {sym}: {e}")
                lines.append(f"⚠️ {sym}: {str(e)[:50]}")
                lines.append("")

        # ── ٤. الأحداث والجدول ────────────────────────────────
        events_text = engine.event_risk.format_upcoming_ar(hours=168)
        # إصلاح تنسيق "بعد 20ساعة" → "بعد 20 ساعة"
        events_text = re.sub(r'(بعد\s*)(\d+)(ساعة)', r'بعد  ساعة', events_text)
        events_text = re.sub(r'(بعد\s*)(\d+)(يوم)', r'بعد  يوم', events_text)
        sched_text  = engine.scheduler.next_weekly_ar() if engine.scheduler else ""

        lines += ["📅 *أحداث الأسبوع*"]
        # نُزيل العنوان المكرر من format_upcoming_ar
        ev_lines = events_text.split("\n")
        ev_body  = "\n".join(ev_lines[2:] if len(ev_lines) > 2 and "━━" in ev_lines[1] else ev_lines)
        lines.append(ev_body)

        if sched_text:
            lines += ["", f"⏰ {sched_text}"]
        lines += [
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]

        await msg.edit_text(_clean("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_plan_week: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة الأسبوعية: {str(e)[:100]}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    # منع الطلبات المتكررة
    if context.user_data.get("portfolio_running"):
        await update.message.reply_text(
            "⏳ جاري تحليل المحفظة بالفعل — يُرجى الانتظار")
        return
    context.user_data["portfolio_running"] = True

    msg = await update.message.reply_text(
        "💼 جاري تحليل المحفظة...\n"
        "⏳ قد يستغرق ١٠-٢٠ ثانية — يُرجى الانتظار"
    )
    try:

        # ── ١. طلبات البيانات الأساسية — متوازية ─────────────
        fear, btc_c, onchain, top_coins = await asyncio.gather(
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_ohlcv("BTC", "1d", 200),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_top_coins(limit=10),
            return_exceptions=True,
        )
        fear      = fear      if isinstance(fear, dict)  else {"value": 50}
        btc_c     = btc_c     if isinstance(btc_c, list) else []
        onchain   = onchain   if isinstance(onchain, dict) else {}
        top_coins = top_coins if isinstance(top_coins, list) else []
        fear_val  = int((fear or {}).get("value") or 50)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        ev_mult, _ = engine.event_risk.get_exposure_multiplier()

        # ── ٢. جلب الأخبار العامة مرة واحدة (لا داخل الحلقة) ─
        symbols_str = ",".join(
            (c.get("symbol") or "").upper()
            for c in top_coins[:5]
            if c.get("symbol")
        )
        news_general = await engine.data_layer.get_news(
            currencies=symbols_str or "BTC,ETH", limit=10) or []
        try:
            news_an_general = await engine.news_engine.analyze(
                news_general, symbols_str.split(","))
            news_an_general = news_an_general or {}
        except Exception:
            news_an_general = {}
        news_sentiment = float(news_an_general.get("sentiment_score") or 0)

        # ── ٣. OHLCV لجميع العملات — متوازية ─────────────────
        valid_coins = [
            c for c in top_coins[:5]
            if (c.get("symbol") or "").upper()
        ]
        ohlcv_results = await asyncio.gather(
            *[engine.data_layer.get_ohlcv(
                (c.get("symbol") or "").upper(), "1d", 60)
              for c in valid_coins],
            return_exceptions=True,
        )
        liq_results = await asyncio.gather(
            *[engine.microstructure.analyze(
                (c.get("symbol") or "").upper(), 1000)
              for c in valid_coins],
            return_exceptions=True,
        )

        # ── ٤. بناء المرشحين ──────────────────────────────────
        candidates = []
        for i, coin in enumerate(valid_coins):
            sym     = (coin.get("symbol") or "").upper()
            candles = ohlcv_results[i] if isinstance(ohlcv_results[i], list) else []
            liq     = liq_results[i]   if not isinstance(liq_results[i], Exception) else None

            if len(candles) < 30:
                continue
            try:
                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=onchain,
                    news_sentiment=news_sentiment,
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         _calc_atr(candles),
                    "liquidity_score": liq.liquidity_score if liq else 0.7,
                    "expected_return": _est_return(signal, regime),
                })
            except Exception as e:
                logger.warning(f"portfolio {sym}: {e}")

        # ── ٥. توزيع المحفظة ──────────────────────────────────
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
        logger.error(f"cmd_portfolio: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ خطأ في تحليل المحفظة: {str(e)[:100]}")
        except Exception:
            await update.message.reply_text("❌ خطأ في تحليل المحفظة — أعد المحاولة")
    finally:
        context.user_data["portfolio_running"] = False


@require_tier("stats")
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
        sched_w    = engine.scheduler.next_weekly_ar()  if engine.scheduler else "غير مُفعَّل"
        sched_m    = engine.scheduler.next_monthly_ar() if engine.scheduler else "غير مُفعَّل"
        sched_scan = engine.scheduler.next_scan_ar()    if engine.scheduler else ""

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
    app.add_handler(CommandHandler("planmonth",  cmd_plan_month))
    app.add_handler(CommandHandler("planweek",   cmd_plan_week))
    app.add_handler(CommandHandler("portfolio",  cmd_portfolio))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))

"""
📋 رائد — handlers/plan.py
أوامر: /plan_month /plan_week /portfolio /stats /approve /reject
- جميع النتائج محمية من None
- Markdown آمن — لا أخطاء تنسيق
"""

import asyncio
import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode
from core.middleware import require_tier
try:
    from core.coins_list import is_symbol_allowed, get_tier_message
except ImportError:
    is_symbol_allowed = lambda s, t: True
    get_tier_message  = lambda s, t: f'⛔ {s} غير متاحة'
from core.state_manager import state_manager as _sm

# T2_plan_fix: عتبات الثقة حسب الباقة في /plan
_TIER_CONF_PLAN = {
    "free":    45,   # مجاني
    "silver":  50,   # فضي
    "gold":    55,   # ذهبي
    "diamond": 55,   # ماسي
    "admin":   55,   # مدير
}
from core.pair_resolver import resolve_symbol, PairResolution, build_pair_addon_inline
from handlers.analysis import normalize_symbol_alias  # X6: مرادفات الرموز المركزية


def _fmt_price(price: float) -> str:
    """تنسيق السعر حسب حجمه — يعرض الأرقام المهمة دائماً."""
    if price <= 0:       return "$0"
    elif price >= 1000:  return f"${price:,.2f}"
    elif price >= 1:     return f"${price:,.4f}"
    elif price >= 0.001: return f"${price:.6f}"
    elif price >= 1e-6:  return f"${price:.8f}"
    else:                return f"${price:.10f}"

logger = logging.getLogger(__name__)
# plan_limits: حد العملات حسب الباقة
_PLAN_ASSET_LIMITS = {
    "diamond": {"crypto": 10, "tokenized": 10},
    "admin":   {"crypto": 10, "tokenized": 10},
    "gold":    {"crypto": 7,  "tokenized": 5},
    "silver":  {"crypto": 5,  "tokenized": 0},
    "free":    {"crypto": 3,  "tokenized": 0},
}

def _get_plan_limit(tier: str, asset_type: str = "crypto") -> int:
    """حد العملات حسب الباقة ونوع الأصل"""
    return _PLAN_ASSET_LIMITS.get(tier, _PLAN_ASSET_LIMITS["free"]).get(asset_type, 3)


# auditing_agent: طبقة التدقيق الإلزامية
try:
    from core.auditing_agent import audit_content, audit_financial_content
    _AUDITING_PLAN = True
except ImportError:
    def audit_content(c, source="default"): return True, c
    def audit_financial_content(c, source="plan"): return True, c
    _AUDITING_PLAN = False
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context): return context.bot_data.get("raed_engine")


# ════════════════════════════════════════════════════════════════
# #226/#227/#231/#232/#233 — helper موحَّد للرسائل في plan.py
# ════════════════════════════════════════════════════════════════
def _get_message_p(update, context=None):
    """يُعيد Message الصحيح سواء كان update مباشراً أو callback.
    FF1 (#2084/#2085): لا يُعيد None أبداً — fallback لـ effective_message.
    """
    # أولاً: الرسالة الأصلية من الأمر المباشر
    if getattr(update, "message", None) is not None:
        return update.message
    # ثانياً: من callback_query (عند الضغط على زر)
    if getattr(update, "callback_query", None) is not None:
        cq_msg = getattr(update.callback_query, "message", None)
        if cq_msg is not None:
            return cq_msg
    # ثالثاً: _cb_message المحفوظ (الحل القديم)
    if context is not None:
        cb_msg = context.user_data.get("_cb_message")
        if cb_msg is not None:
            return cb_msg
    # رابعاً: effective_message كـ fallback أخير
    eff = getattr(update, "effective_message", None)
    if eff is not None:
        return eff
    return None



async def callback_plan_exec(update, context):
    """
    plan_execute_v2: تنفيذ صفقة من الخطة عبر /execute مباشرة
    صيغة: plan_exec:{symbol}:{price}
    """
    query   = update.callback_query
    await query.answer()
    data    = query.data
    parts   = data.split(":")
    if len(parts) < 3:
        await query.edit_message_text("❌ بيانات غير صالحة"); return

    symbol = parts[1].upper()
    try:
        price = float(parts[2])
    except ValueError:
        await query.edit_message_text("❌ سعر غير صالح"); return

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await query.edit_message_text("⚠️ النظام لم يُهيَّأ بعد"); return

    # plan_execute_v2: إعداد context.args كما يتوقعه cmd_execute
    # ثم إرسال رسالة جديدة تحاكي /execute
    context.args = [symbol, "buy", "0"]
    context.user_data["_plan_exec_price"] = price
    context.user_data["_from_plan"] = True

    await query.edit_message_text(
        f"⏳ جاري تقييم {symbol} للتنفيذ من الخطة..."
    )

    try:
        from handlers.trading import cmd_execute as _cmd_ex
        # نُنشئ fake update يحتوي message حقيقي
        class _FakeMsg:
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            async def reply_text(self, *a, **kw):
                return await query.message.reply_text(*a, **kw)
            async def edit_text(self, *a, **kw):
                return await query.message.edit_text(*a, **kw)

        class _FakeUpdate:
            effective_user = update.effective_user
            effective_chat = update.effective_chat
            message = _FakeMsg()
            callback_query = query

        await _cmd_ex(_FakeUpdate(), context)
    except Exception as _ex_err:
        logger.error(f"callback_plan_exec v2: {type(_ex_err).__name__}: {_ex_err}")
        await query.message.reply_text(
            f"❌ خطأ في تنفيذ {symbol}\n"
            f"جرّب: /execute {symbol} buy"
        )


async def callback_plan_asset(update, context):
    """معالجة اختيار نوع الأصول (crypto/tokenized/both) للخطة الأسبوعية/الشهرية."""
    query   = update.callback_query
    await query.answer()
    data    = query.data  # plan_w_asset:crypto أو plan_m_asset:both
    parts   = data.split(":")
    if len(parts) < 2:
        await query.edit_message_text("❌ بيانات غير صالحة"); return

    plan_type  = "week" if "plan_w_" in data else "month"
    asset_type = parts[1]  # crypto | tokenized | both

    # حفظ نوع الأصول في user_data
    context.user_data["_plan_asset_type"] = asset_type

    # الانتقال لاختيار عام/محدد
    label_w = "الأسبوعية" if plan_type == "week" else "الشهرية"
    prefix  = f"plan_{plan_type[0]}"  # plan_w أو plan_m
    asset_label = {"crypto": "💰 عملات رقمية", "tokenized": "📈 أصول مُرمَّزة", "both": "🔀 الاثنان"}.get(asset_type, "")

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 عام (حسب باقتي)", callback_data=f"{prefix}_general"),
        InlineKeyboardButton("🎯 عملات محددة",     callback_data=f"{prefix}_custom"),
    ]])
    await query.edit_message_text(
        f"📅 *الخطة {label_w}* — {asset_label}\n\n"
        "هل تريد خطة عامة لأهم الأصول،\n"
        "أم تحليل عملات محددة؟",
        parse_mode="Markdown", reply_markup=buttons)


async def callback_planmkt(update, context):
    """تطوير #223: معالجة اختيار Spot/Futures للخطة الأسبوعية/الشهرية.
    صيغة: planmkt_{weekly|month}_{spot|futures}_{args}"""
    from telegram.ext import ContextTypes as _CT
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 3)
    if len(parts) < 3:
        await query.edit_message_text("❌ بيانات غير صالحة"); return

    plan_type = parts[1]   # week | month
    mkttype   = parts[2]   # spot | futures
    args_str  = parts[3].strip() if len(parts) > 3 else ""

    context.user_data["_mkttype_plan"] = mkttype
    # إصلاح #226/#232/#233: حفظ query.message لاستخدامه في cmd_plan_week/month
    context.user_data["_cb_message"] = query.message
    if args_str:
        context.args = args_str.split()
    else:
        context.args = []

    # إصلاح O2: تمرير رسالة "جاري" للأوامر لمنع التكرار
    label = "⚡ Spot" if mkttype == "spot" else "📈 Futures + الأسهم"
    plan_name = "الأسبوعية" if plan_type == "week" else "الشهرية"
    await query.edit_message_text(
        f"{label} — جاري بناء الخطة {plan_name}...",
        parse_mode="Markdown")
    # تمرير رسالة query كـ msg_override لمنع رسالة "جاري" مكررة
    context.user_data["_plan_msg_override"] = query.message

    if plan_type == "week":
        await cmd_plan_week(update, context)
    else:
        await cmd_plan_month(update, context)


async def _resolve_custom_symbols(raw_symbols, tier, data_layer):
    """تطوير #188 (Phase 2): يُطبِّق resolve_symbol على كل رمز مُدخَل من
    المستخدم في /weekly و/monthly. التحليل دائماً مقابل USDT
    (res.base) — مع PairResolution لكل رمز لإلحاق فقرة/سطر BTC/ETH
    الإضافي عبر build_pair_addon_inline داخل سطر العملة.
    PLAN_NAME_fix: يُعيد الرموز الأصلية (XGOOGL) للعرض مع base للتحليل."""
    resolved, resolutions, display_syms = [], [], []
    for raw in raw_symbols:
        raw_orig = raw.upper()  # حفظ الرمز الأصلي
        raw = normalize_symbol_alias(raw)  # X6: تطبيع المرادفات
        try:
            res = await resolve_symbol(raw, tier, data_layer)
        except Exception:
            res = PairResolution(base=raw)
        # PLAN_NAME_fix: للأصول X-prefix، أبقِ الرمز الأصلي للعرض
        display_sym = raw_orig if (raw_orig.startswith("X") and len(raw_orig) > 2) else res.base
        resolved.append(res.base)      # للتحليل (GOOGL)
        resolutions.append(res)
        display_syms.append(display_sym)  # للعرض (XGOOGL)
    return resolved, resolutions, display_syms


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

def _calc_rsi(candles: list, period: int = 14) -> float:
    """حساب RSI دقيق."""
    if len(candles) < period + 1:
        return 50.0
    try:
        px = [float(c.get("close", c.get("price", 0))) for c in candles[-(period+1):]]
        if any(p <= 0 for p in px):
            return 50.0
        gs = [max(0.0, px[i] - px[i-1]) for i in range(1, len(px))]
        ls = [max(0.0, px[i-1] - px[i]) for i in range(1, len(px))]
        ag = sum(gs) / period
        al = sum(ls) / period
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        return round(100.0 - (100.0 / (1.0 + ag / al)), 1)
    except Exception:
        return 50.0



def _calc_price_forecast(candles: list, days: int = 30,
                           fear_greed: int = 50,
                           btc_dominance: float = 50.0,
                           market_regime: str = "neutral") -> dict:
    """
    النقطة 12: تنبؤ متقدم للسعر بـ N يوم.
    المدارس المستخدمة:
    - Elliott Wave تقريبي (ATR channels + Fibonacci extensions)
    - Wyckoff (حجم + regime)
    - Dow Theory (EMA20/50/200 alignment)
    - Momentum (RSI + MACD تقديري)
    - Sentiment (Fear&Greed + BTC Dominance)
    """
    if len(candles) < 30:
        return {}
    try:
        closes  = [float(c.get("close", 0)) for c in candles if c.get("close")]
        highs   = [float(c.get("high",  0)) for c in candles if c.get("high")]
        lows    = [float(c.get("low",   0)) for c in candles if c.get("low")]
        volumes = [float(c.get("volume", 0)) for c in candles if c.get("volume")]
        if not closes: return {}

        price    = closes[-1]
        atr      = _calc_atr(candles)
        # إصلاح #144: _calc_atr تُعيد % بالفعل — لا قسمة إضافية
        atr_pct  = atr if atr > 0 else 3.0
        rsi      = _calc_rsi(candles)

        # ── Dow Theory: EMA Alignment ─────────────────────────────
        ema20  = sum(closes[-20:]) / 20
        ema50  = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
        ema200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else ema50
        dow_bull  = price > ema20 > ema50 > ema200   # اتجاه صاعد كامل
        dow_bear  = price < ema20 < ema50 < ema200   # اتجاه هابط كامل
        trend     = "bullish" if dow_bull else "bearish" if dow_bear else                     "bullish" if ema20 > ema50 else "bearish" if ema20 < ema50 else "neutral"

        # ── Wyckoff: تحليل الحجم ─────────────────────────────────
        vol_avg = sum(volumes[-20:]) / 20 if volumes else 1
        vol_now = volumes[-1] if volumes else vol_avg
        vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1.0
        # ارتفاع السعر مع حجم عالٍ = تأكيد (Wyckoff markup)
        wyckoff_confirm = vol_ratio > 1.2 and trend == "bullish"

        # ── RSI Momentum ─────────────────────────────────────────
        rsi_momentum = 1.0
        if rsi < 25:   rsi_momentum = 1.25  # ذروة بيع شديدة → انتعاش قوي
        elif rsi < 35: rsi_momentum = 1.10  # ذروة بيع → انتعاش
        elif rsi > 75: rsi_momentum = 0.75  # ذروة شراء شديدة → تصحيح
        elif rsi > 65: rsi_momentum = 0.90  # ذروة شراء → حذر
        elif trend == "bullish": rsi_momentum = 1.05
        elif trend == "bearish": rsi_momentum = 0.95

        # ── Sentiment: Fear&Greed + BTC Dominance ────────────────
        sentiment_mult = 1.0
        if fear_greed < 20:   sentiment_mult = 1.15  # خوف شديد = فرصة
        elif fear_greed < 35: sentiment_mult = 1.05  # خوف = محتاط إيجابي
        elif fear_greed > 75: sentiment_mult = 0.90  # جشع شديد = خطر
        elif fear_greed > 60: sentiment_mult = 0.95  # جشع = حذر
        # BTC Dominance: ارتفاع Dominance = Alt Coins تنخفض
        if btc_dominance > 60 and trend == "bullish":
            sentiment_mult *= 0.95  # Alt season ضعيف
        elif btc_dominance < 45 and trend == "bullish":
            sentiment_mult *= 1.05  # Alt season قوي

        # ── Market Regime ─────────────────────────────────────────
        regime_mult = 1.0
        if "هابط" in market_regime or "bear" in market_regime.lower():
            regime_mult = 0.85
        elif "صاعد" in market_regime or "bull" in market_regime.lower():
            regime_mult = 1.10

        # ── Elliott Wave تقريبي: Fibonacci Extensions ─────────────
        recent_high = max(highs[-30:])
        recent_low  = min(lows[-30:])
        diff        = recent_high - recent_low
        # XPCY_fib_fix: إذا diff=0 (بيانات محدودة) → استخدم ATR للتقدير
        if diff < price * 0.001:  # diff أقل من 0.1% من السعر
            diff = price * atr_pct / 100 * 5  # تقدير من ATR
            recent_high = price * (1 + atr_pct / 100 * 2.5)
            recent_low  = price * (1 - atr_pct / 100 * 2.5)
        # موجة 3 و 5 (الأكثر شيوعاً في Elliott)
        fib_target1 = recent_low + diff * 1.272  # موجة 3
        fib_target2 = recent_low + diff * 1.618  # موجة 5
        fib_target3 = recent_low + diff * 2.618  # هدف موجة C
        # دعم فيبوناتشي (retracement)
        fib_support1 = recent_high - diff * 0.382
        fib_support2 = recent_high - diff * 0.618

        # ── الحساب النهائي المُرجَّح ──────────────────────────────
        # إصلاح #182: Random Walk — التقلب يتناسب مع sqrt(days) لا days
        # هذا مبدأ إحصائي صحيح ويمنع التضخيم المبالغ فيه
        combined_mult = rsi_momentum * sentiment_mult * regime_mult
        wyckoff_boost = 1.02 if wyckoff_confirm else 1.0
        daily_move    = atr_pct / 100
        time_factor   = (days ** 0.5)   # sqrt(30) ≈ 5.5 بدلاً من 30

        bull_case_raw = price * (1 + daily_move * time_factor * 0.6 * combined_mult * wyckoff_boost)
        base_case_raw = price * (1 + daily_move * time_factor * 0.25 * combined_mult)
        bear_case_raw = price * (1 - daily_move * time_factor * 0.4 / combined_mult)

        # حد أقصى للتغيير: ±50% لـ 30 يوم (واقعي حتى لـ BTC)
        max_change = 0.50
        bull_case = min(bull_case_raw, price * (1 + max_change))
        base_case = max(min(base_case_raw, price * (1 + max_change * 0.5)),
                        price * (1 - max_change * 0.3))
        bear_case = max(bear_case_raw, price * (1 - max_change))

        # ── حساب الثقة ───────────────────────────────────────────
        confidence_factors = []
        confidence_factors.append(0.3 if dow_bull or dow_bear else 0.15)
        confidence_factors.append(0.2 if vol_ratio > 1.2 else 0.1)
        confidence_factors.append(abs(rsi - 50) / 50 * 0.25)
        confidence_factors.append(abs(fear_greed - 50) / 50 * 0.15)
        confidence_factors.append(0.1 if market_regime != "neutral" else 0.05)
        confidence = min(sum(confidence_factors), 0.95)

        return {
            "trend":        trend,
            "rsi":          round(rsi, 1),
            "atr_pct":      round(atr_pct, 2),
            "fear_greed":   fear_greed,
            "btc_dom":      btc_dominance,
            "vol_ratio":    round(vol_ratio, 2),
            "wyckoff":      wyckoff_confirm,
            "bull_case":    round(bull_case, 8),
            "base_case":    round(base_case, 8),
            "bear_case":    round(bear_case, 8),
            "fib_t1":       round(fib_target1, 8),
            "fib_t2":       round(fib_target2, 8),
            "fib_t3":       round(fib_target3, 8),
            "fib_s1":       round(fib_support1, 8),
            "fib_s2":       round(fib_support2, 8),
            "confidence":   round(confidence, 2),
            "combined_mult":round(combined_mult, 3),
            # إصلاح #984: trend_dir لإظهار Elliott بشكل صحيح
            "trend_dir":    trend,  # "bullish" | "bearish" | "neutral"
        }
    except Exception:
        return {}


def _format_forecast_ar(symbol: str, price: float, fc: dict, days: int = 30) -> str:
    """
    تنسيق تنبؤ السعر المتقدم للعرض.
    يشمل: Elliott + Wyckoff + Dow + Sentiment + Fibonacci
    """
    if not fc or price <= 0:
        return ""
    p_fmt    = _fmt_price
    trend_ar = {"bullish": "📈 صاعد", "bearish": "📉 هابط", "neutral": "↔️ جانبي"}.get(fc.get("trend",""), "↔️")
    conf     = fc.get("confidence", 0)
    conf_bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))

    bull = fc.get("bull_case", price)
    base = fc.get("base_case", price)
    bear = fc.get("bear_case", price)

    # T2: zero check قبل القسمة
    if not price or price <= 0:
        bull_pct = base_pct = bear_pct = 0.0
    else:
        bull_pct = (bull/price - 1)*100
        base_pct = (base/price - 1)*100
        bear_pct = (bear/price - 1)*100

    lines = [
        "",
        f"🔮 *تنبؤ {symbol} — {days} يوم القادمة*",
        "━━━━━━━━━━━━━━━━━━",
        f"• الاتجاه: {trend_ar} | الثقة: {conf_bar} {conf:.0%}",
        f"• RSI: {fc.get('rsi',50):.0f} | ATR: {fc.get('atr_pct',0):.1f}%",
        f"• Fear&Greed: {fc.get('fear_greed',50)} | BTC Dom: {fc.get('btc_dom',50):.0f}%",
        f"• {'🔊 حجم مرتفع (Wyckoff ✅)' if fc.get('wyckoff') else '🔇 حجم عادي'}",
        "",
        f"📊 *السيناريوهات الـ {days} يوم:*",
        f"  🟢 متفائل (Elliott 5): {p_fmt(bull)} ({bull_pct:+.1f}%)",
        f"  🟡 محتمل (Base):       {p_fmt(base)} ({base_pct:+.1f}%)",
        f"  🔴 متحفظ (Bear):       {p_fmt(bear)} ({bear_pct:+.1f}%)",
        "",
        # إصلاح #818: في السوق الهابط نُظهر فقط مستويات الدعم
        *(
            [
                f"📐 *أهداف فيبوناتشي (Elliott):*",
                f"  🎯 هدف 1 (1.272): {p_fmt(fc.get('fib_t1',0))}",
                f"  🎯 هدف 2 (1.618): {p_fmt(fc.get('fib_t2',0))}",
                # إصلاح #1076: لا نُظهر الدعم إذا كان أعلى من السعر الحالي
                *(([f"  🛡️ دعم 1 (0.382): {p_fmt(fc.get('fib_s1',0))}"]
                   if fc.get('fib_s1',0) < _fca_p else []) +
                  ([f"  🛡️ دعم 2 (0.618): {p_fmt(fc.get('fib_s2',0))}"]
                   if fc.get('fib_s2',0) < _fca_p else [])),
            ] if fc.get('trend_dir') not in ('down', 'bearish')
            else [
                f"📐 *مستويات الدعم الرئيسية:*",
                # إصلاح #1076: لا نُظهر الدعم إذا كان أعلى من السعر الحالي
                *(([f"  🛡️ دعم 1 (0.382): {p_fmt(fc.get('fib_s1',0))}"]
                   if fc.get('fib_s1',0) < _fca_p else []) +
                  ([f"  🛡️ دعم 2 (0.618): {p_fmt(fc.get('fib_s2',0))}"]
                   if fc.get('fib_s2',0) < _fca_p else [])),
            ]
        ),
        "",
        f"⚠️ التنبؤ استرشادي — الأسواق غير متوقعة",
    ]
    return "\n".join(lines)

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
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    if context.user_data.get("planmonth_running"):
        await _get_message_p(update, context).reply_text(
            "⏳ جاري بناء الخطة الشهرية بالفعل — يُرجى الانتظار")
        return

    # تطوير #223: سؤال Spot/Futures للماسي+ فقط في الخطة الشهرية
    _uid_pm_pre = update.effective_user.id if update.effective_user else 0
    _tier_pm_pre = _sm.get_tier(_uid_pm_pre)
    _mkttype_pm  = context.user_data.pop("_mkttype_plan", None)
    if _mkttype_pm is None and _tier_pm_pre in ("diamond", "admin"):
        _args_str = " ".join(context.args or [])
        kb_pm = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Spot (كل المستخدمين)", callback_data=f"planmkt_month_spot_{_args_str}"),
             InlineKeyboardButton("📈 Futures + أسهم (ماسي+)", callback_data=f"planmkt_month_futures_{_args_str}")],
        ])
        # FF2 (#2084): None check قبل reply_text
        _msg_pm = _get_message_p(update, context)
        if _msg_pm is None:
            return  # لا يمكن الرد — تجاهل بأمان
        await _msg_pm.reply_text(
            "📅 *الخطة الشهرية — اختر نوع السوق:*\n"
            "_(Spot لجميع الأصول — Futures + الأسهم المُرمَّزة للماسي+)_",
            parse_mode="Markdown", reply_markup=kb_pm)
        return
    _use_futures_pm = (_mkttype_pm == "futures")

    context.user_data["planmonth_running"] = True

    args      = context.args or []
    scan_mode = len(args) == 0
    # إصلاح #964/#971: تعريف مسبق لجميع المتغيرات
    entry_syms = []
    candidates = []
    allocation = None
    _pair_resolutions = {}  # تطوير #188 (Phase 2): symbol -> PairResolution
    if scan_mode:
        # plan_ohlcv_fix: helper لجلب OHLCV حسب نوع الأصل
        # plan_ohlcv_fix: helper لجلب OHLCV حسب نوع الأصل
        def _get_ohlcv_for_sym(sym):
            su = sym.upper()
            if su.startswith("X") and len(su) > 2:
                return engine.data_layer.get_ohlcv(su, "1d", 50, mkttype="spot")
            elif su in {"SPCX","COIN","AAPL","NVDA","TSLA","MSFT","AMZN","GOOGL","META","AMD","OKB"}:
                return engine.data_layer.get_ohlcv_perp(su, 200)
            return engine.data_layer.get_ohlcv(su, "1d", 200)
    # plan_asset_fix: استخدام asset_type المُحدَّد من المستخدم
        _asset_type_pm = context.user_data.pop("_plan_asset_type", "crypto")
        _tier_pm_s = _sm.get_tier(update.effective_user.id) or "silver"
        _lim_c_pm = _get_plan_limit(_tier_pm_s, "crypto")
        _lim_t_pm = _get_plan_limit(_tier_pm_s, "tokenized")

        _def_crypto = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "HYPE", "DOGE", "AVAX", "LINK"]
        _def_token  = ["XSPY", "XSPCX", "XQQQ", "XXLE", "XAAPL", "XNVDA", "XMSFT", "XAMZN", "XGOOGL", "XTSLA"]

        if _asset_type_pm == "tokenized":
            symbols    = _def_token[:_lim_t_pm]
            plan_label = f"خطة أصول مُرمَّزة — Top {len(symbols)}"
        elif _asset_type_pm == "both":
            _c = _def_crypto[:max(_lim_c_pm // 2, 4)]
            _t = _def_token[:max(_lim_t_pm // 2, 4)]
            symbols    = _c + _t
            plan_label = f"خطة شاملة — رقمية + مُرمَّزة ({len(symbols)} أصل)"
        else:
            symbols    = _def_crypto[:_lim_c_pm]
            plan_label = f"مسح شامل للسوق"

        sym_str    = ", ".join(symbols) if symbols else "مسح شامل"
        _MAX_SCAN_SYMS = len(symbols) or 6
    else:
        _raw_syms  = [a.upper() for a in args[:7]]  # إصلاح #833: حد 7 عملات
        # تطوير #188 (Phase 2): دعم أزواج BTC/ETH (ماسي+ فقط)
        _tier_pm   = _sm.get_tier(update.effective_user.id)
        symbols, _resols_pm, _display_syms_pm = await _resolve_custom_symbols(
            _raw_syms, _tier_pm, engine.data_layer)
        _pair_resolutions = dict(zip(symbols, _resols_pm))
        _display_map_pm = dict(zip(symbols, _display_syms_pm))
        sym_str    = ", ".join(symbols)
        # PLAN_HDR_fix: استخدام display names في عنوان plan_month
        _disp_str_pm = ", ".join(_display_syms_pm) if "_display_syms_pm" in dir() and _display_syms_pm else sym_str
        plan_label = f"خطة مخصصة لـ {_disp_str_pm}"

    # إصلاح #776: دعم msg من callback
    _msg_ov = context.user_data.pop("_plan_msg_override", None)
    if _msg_ov:
        msg = _msg_ov
        try:
            await msg.edit_text(f"📋 جاري بناء {plan_label}...")
        except Exception:
            pass
    else:
        msg = await _get_message_p(update, context).reply_text(
            f"📋 جاري بناء {plan_label}...\n"
            + ("⏳ المسح الشامل قد يستغرق 5-15 دقيقة — يُرجى الانتظار" if scan_mode
               else "⏳ قد يستغرق 1-3 دقائق — يُرجى عدم تكرار الأمر")
        )
    # إصلاح #178: semaphore للأوامر الثقيلة
    _heavy_sem_w = await engine.acquire_heavy()
    await _heavy_sem_w.acquire()
    try:

        # ── 1. بيانات أساسية متوازية ──────────────────────────
        # إصلاح #1003: timeout 8 دقائق للمسح الشامل
        import asyncio as _aio_pm
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
        # إصلاح #983: BTC Dom من onchain الحقيقي
        _btc_dom_real = float((onchain or {}).get("btc_dominance") or
                              (onchain or {}).get("btc_dom") or 56)

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        # ── 2. OHLCV لجميع العملات متوازية ───────────────────
        # إصلاح #123: timeout صريح — يمنع تجمد كامل الطلب إذا عملة واحدة
        # (مثل OKB/BGB/CRO) تأخرت في الاستجابة من كل مصادر البيانات
        try:
            ohlcv_all = await asyncio.wait_for(
                asyncio.gather(
                    *[_get_ohlcv_for_sym(sym) for sym in symbols],
                    return_exceptions=True,
                ), timeout=90.0
            )
        except asyncio.TimeoutError:
            logger.warning("planmonth (مخصص): timeout في جلب OHLCV")
            ohlcv_all = [[] for _ in symbols]

        # ── 3. أخبار مرة واحدة لجميع العملات ─────────────────
        news_raw = await engine.data_layer.get_news(
            currencies=",".join(symbols), limit=10) or []
        try:
            news_an = await engine.news_engine.analyze(news_raw, symbols)
            news_an = news_an or {}
        except Exception:
            news_an = {}
        news_sentiment = float(news_an.get("sentiment_score") or 0)

        # ── 4. بناء المرشحين ──────────────────────────────────
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
                    and "_" not in (c.get("symbol") or "")
                    and len((c.get("symbol") or "")) <= 10
                )
            ][:10]   # أقصى 10 لتجنب timeout   # أقصى 10 في المسح لتجنب timeout

            # إصلاح #254: timeout صارم لجلب OHLCV + تقليل العملات
            symbols = symbols[:8]   # أقصى 8 بدلاً من 10

            # إصلاح P3 (#945/#951): Futures → إضافة الأصول المُرمَّزة
            if _use_futures_pm:
                _tokenized_defaults = ["SPCX", "COIN", "AAPL", "NVDA", "TSLA"]
                for _tk in _tokenized_defaults:
                    if _tk not in symbols and len(symbols) < 10:
                        symbols.append(_tk)
                logger.info(f"plan_month Futures symbols: {symbols}")
            try:
                ohlcv_all = await asyncio.wait_for(
                    asyncio.gather(
                        *[_get_ohlcv_for_sym(sym) for sym in symbols],
                        return_exceptions=True,
                    ), timeout=60.0
                )
            except asyncio.TimeoutError:
                logger.warning("planmonth scan: timeout في جلب OHLCV")
                ohlcv_all = [[] for _ in symbols]

        MIN_CONFIDENCE_SCAN = 0.42  # فلترة في scan_mode — يحذف العملات ضعيفة الإشارة

        # إصلاح #255: جلب الأسعار بالتوازي مسبقاً
        try:
            _prices_all = await asyncio.wait_for(
                asyncio.gather(
                    *[engine.data_layer.get_price_perp(sym)
               if sym.upper() in {"SPCX","COIN","AAPL","NVDA","TSLA","MSFT","AMZN","GOOGL","META","MSTR","OPENAI","ANTHROPIC","AMD","OKB"}
               else engine.data_layer.get_price(sym)
               for sym in symbols],
                    return_exceptions=True,
                ), timeout=30.0
            )
        except asyncio.TimeoutError:
            _prices_all = [{} for _ in symbols]

        # إصلاح #280/#287: معالجة أوسع — microstructure اختيارية
        candidates = []
        for i, sym in enumerate(symbols):
            # TK5_fix: تحقق من توفر الأصل في Spot في plan_month
            if not _use_futures_pm:  # إصلاح: كان _use_futures_pw خطأ
                try:
                    _sp_chk = await engine.data_layer.check_spot_available(sym)
                    if not _sp_chk.get("available", True):
                        continue  # تجاهل الأصل غير المتاح في Spot
                except Exception:
                    pass
            candles = ohlcv_all[i] if isinstance(ohlcv_all[i], list) else []
            # إصلاح #307: إذا فشل OHLCV → retry بـ 50 شمعة
            if len(candles) < 30:
                try:
                    _retry = await asyncio.wait_for(
                        engine.data_layer.get_ohlcv(sym, "1d", 50),
                        timeout=10.0
                    )
                    candles = _retry if isinstance(_retry, list) else []
                except Exception:
                    pass
            if len(candles) < 30:
                continue
            try:
                # إصلاح #321: تنظيف candles من القيم المعطوبة
                candles_clean = [
                    c for c in candles
                    if (float(c.get("close", 0) or 0) > 0 and
                        float(c.get("high",  0) or 0) > 0 and
                        float(c.get("low",   0) or 0) > 0)
                ]
                if len(candles_clean) < 30:
                    candles_clean = candles  # نستخدم الأصلية إذا بعد التنظيف قليلة
                # إصلاح #34: whale_ratio/funding خاص بكل عملة بدل TVL عالمي ثابت
                _onchain_e = await engine.data_layer.get_signal_enrichment(sym, onchain)
                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles_clean, onchain_data=_onchain_e,
                    news_sentiment=news_sentiment,
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                # microstructure اختيارية — لا توقف العملية
                liq_score = 0.7
                try:
                    liq = await asyncio.wait_for(
                        engine.microstructure.analyze(sym, 1000), timeout=8.0
                    )
                    liq_score = liq.liquidity_score if liq else 0.7
                except Exception:
                    pass  # نكمل بدون سيولة

                # السعر من الجلب المتوازي
                price_d = _prices_all[i] if not isinstance(_prices_all[i], Exception) else {}
                price   = float((price_d or {}).get("price") or 0)
                candidates.append({
                    "symbol":          sym,
                    "confidence":      signal.confidence,
                    "direction":       signal.direction,
                    "atr_pct":         _calc_atr(candles),
                    "liquidity_score": liq_score,
                    "expected_return": _est_return(signal, regime),
                    "price":           price,
                })
            except Exception as e:
                logger.warning(f"plan_month {sym}: {e}")
                # إصلاح #321: fallback بدلاً من تخطي العملة
                try:
                    price_d2 = _prices_all[i] if not isinstance(_prices_all[i], Exception) else {}
                    price2   = float((price_d2 or {}).get("price") or 0)
                    if price2 > 0 and len(candles) >= 14:
                        # إشارة بسيطة من RSI فقط
                        from core.signal_layer import TradingSignal
                        _rsi_fb = _calc_rsi(candles)
                        _dir_fb = "long" if _rsi_fb < 35 else "short" if _rsi_fb > 70 else "neutral"
                        _conf_fb = 0.40 + abs(_rsi_fb - 50) / 200
                        candidates.append({
                            "symbol":          sym,
                            "confidence":      round(_conf_fb, 2),
                            "direction":       _dir_fb,
                            "atr_pct":         _calc_atr(candles),
                            "liquidity_score": 0.6,
                            "expected_return": 0.03 if _dir_fb == "long" else -0.02,
                            "price":           price2,
                        })
                        logger.info(f"plan_month {sym}: fallback signal RSI={_rsi_fb:.0f}")
                except Exception as _fe:
                    logger.debug(f"plan_month {sym} fallback failed: {_fe}")

        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        # إصلاح #12: قيمة المحفظة الفعلية للمستخدم بدلاً من القيمة الثابتة
        from core.state_manager import state_manager as _sm_pm
        from core.virtual_wallet import VirtualWallet as _VW_pm
        _uid_pm   = update.effective_user.id
        _vw_pm_d  = _sm_pm.get_virtual_wallet(_uid_pm) or {}
        _vw_pm    = _VW_pm(_vw_pm_d) if _vw_pm_d else None
        portfolio_val = _vw_pm.total_value if _vw_pm else float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, event_multiplier=ev_mult)

        lines = [
            f"📋 *الخطة الشهرية — {plan_label}*",
            "━━━━━━━━━━━━━━━━━━",
            # T7_fix: استخدام display names (XGOOGL وليس GOOGL)
            f"العملات: {', '.join(_display_syms_pm if '_display_syms_pm' in dir() else symbols)}",
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
            # إذا لم يكن في candidates, جلب السعر مباشرة
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
            # T2_eq_fix: "دون حد" يعتمد على confidence فقط
            _tier_pm_w = _sm.get_tier(update.effective_user.id) if update.effective_user else "silver"
            _t_entry_pm = _TIER_CONF_PLAN.get(_tier_pm_w, 55)
            conf_warn = (" ⚠️ دون حد الدخول"
                         if (cand and conf < _t_entry_pm / 100)
                         else "")
            # تنسيق السعر الصحيح حسب حجمه
            price_str = _fmt_price(price_v) if price_v > 0 else "🔄 جاري الجلب"
            # M#99: نوع الصفقة planmonth — لا نُكرر "⚪ انتظار" مع dir_ar
            _d4 = (cand or {}).get("direction","neutral")
            _t4 = "📈 Spot/Long" if _d4=="long" else "📉 Short" if _d4=="short" else ""
            # PLAN_NAME_fix: استخدام الرمز الأصلي للعرض (XGOOGL وليس GOOGL)
            _disp_sym_pm = _display_map_pm.get(sym_p, sym_p) if "_display_map_pm" in dir() else sym_p
            line = f"💎 *{_disp_sym_pm}* — {price_str}"
            if cand:
                line += f" | {dir_ar} | ثقة: {conf:.0%}{conf_warn}"
                if _t4:
                    line += f" | {_t4}"
            # تطوير #188 (Phase 2): فقرة الزوج المُكثَّفة إن وُجدت
            _res_p = _pair_resolutions.get(sym_p)
            if _res_p:
                line += await build_pair_addon_inline(_res_p, engine.data_layer)
            price_lines.append(line)

        lines += ["", "💰 *العملات المُحلَّلة*"] + price_lines
        lines += ["", btc_ref, ""]
        lines.append(_clean(engine.capital_engine.format_ar(allocation, regime, candidates)))
        # بناء جدول الشهر بناءً على حالة السوق الفعلية
        from core.regime_detector import Regime
        # إصلاح #20: استخدام نفس portfolio_val الديناميكي من #12 لتجنب تناقض
        # $10,022 (أعلاه) مقابل $10,000 (هنا) — كلاهما يجب أن يطابق المحفظة الفعلية
        user_portfolio = portfolio_val
        cash_pct       = 1.0 if regime.regime == Regime.BEAR_TREND else 0.3
        invest_pct     = 1.0 - cash_pct
        cash_amount    = user_portfolio * cash_pct
        invest_amount  = user_portfolio * invest_pct

        # إصلاح #373: week_plan يعكس قرار capital_engine الفعلي
        _fg_now    = fear_val
        # إصلاح #1072: لا "محقق ✅" — نص وصفي فقط
        _fg_label  = f"Fear = {_fg_now} < 25 ✓" if _fg_now < 25 else f"حالياً {_fg_now} — انتظر < 25"
        _rsi_btc   = float((regime.metrics or {}).get("rsi", 50) or 50)
        _rsi_label = f"RSI = {_rsi_btc:.0f} > 30 ✓" if _rsi_btc > 30 else f"حالياً {_rsi_btc:.0f} — انتظر > 30"

        # قراءة قرار allocation الفعلي
        _deployed    = getattr(allocation, "deployed_usd", 0) or 0
        _positions   = getattr(allocation, "positions",    []) or []
        _cash        = getattr(allocation, "cash_reserve", user_portfolio) or user_portfolio
        _deploy_pct  = _deployed / max(user_portfolio, 1)
        _pos_names   = " و".join(p.symbol for p in _positions[:3]) if _positions else ""

        if regime.regime in (Regime.BEAR_TREND, Regime.DISTRIBUTION):
            if _deployed > 0 and _positions:
                # capital_engine قرر الدخول رغم الهبوط (إشارات RSI extreme)
                week_plan = [
                    f"• أسبوع 1: دخول تكتيكي محدود {_deploy_pct:.0%} (${_deployed:,.0f}) في {_pos_names} — ذروة بيع تاريخية",
                    f"• أسبوع 2: مراقبة — وقف خسارة صارم إذا كسر الدعم | RSI {_rsi_label}",
                    f"• أسبوع 3: Fear & Greed < 25 → {'زيادة تدريجية' if candidates else 'انتظر تأكيد ارتداد'} ({_fg_label})",
                    f"• أسبوع 4: مراجعة المراكز — احتفظ بـ ${_cash:,.0f} سيولة احتياطية",
                ]
            else:
                # capital_engine قرر عدم الدخول
                week_plan = [
                    # إصلاح #422/#665/#674 (M5): شروط تُفعَّل إجراءات حقيقية
                    (f"• أسبوع 1: 🟡 تجميع محدود 3-5% عند الدعم — Fear={fear_val} < 20 (خوف شديد = فرصة)"
                     if fear_val < 20
                     else f"• أسبوع 1: احتفظ بـ 100% سيولة (${user_portfolio:,.0f}) — RSI يرتد فوق 30 ({_rsi_label})"),
                    # إصلاح N1: rsi_w قد لا يكون مُعرَّفاً في plan_month → fallback
                    (f"• أسبوع 2: 🟢 دخول تدريجي 5-10% — RSI في ذروة بيع (< 30)"
                     if locals().get("rsi_w", 50) < 30
                     else f"• أسبوع 2: مراقبة مستويات الدعم ودخول عند ارتداد RSI فوق 35"),
                    f"• أسبوع 3: Fear & Greed < 25 → {'🟢 ابدأ التجميع التدريجي' if fear_val < 25 else 'انتظر تأكيد ارتداد'} ({_fg_label})",
                    "• أسبوع 4: تقييم: هل تشكّل قاع؟ قرار الدخول الكامل",
                ]
        elif regime.regime in (Regime.BULL_TREND, Regime.ACCUMULATION):
            week_plan = [
                f"• أسبوع 1: دخول مبكر — {invest_pct:.0%} من المحفظة (${invest_amount:,.0f})",
                "• أسبوع 2: مضاعفة المراكز الرابحة عند تأكيد الاتجاه",
                "• أسبوع 3: رفع وقف الخسارة للتعادل على جميع المراكز",
                "• أسبوع 4: جني جزء من الأرباح (30-50%) والاحتفاظ بالباقي",
            ]
        else:
            week_plan = [
                "• أسبوع 1: مراقبة — لا دخول حتى تتضح الإشارات",
                "• أسبوع 2: دخول جزئي (25%) إذا RSI > 35 وFear < 35",
                "• أسبوع 3: مراجعة وتعديل وقف الخسارة",
                "• أسبوع 4: تقييم النتائج وقرار الاستمرار",
            ]

        # تنبؤ 30 يوم في planmonth (#139/#213)
        try:
            _fca_idx = 0
            _fca_c   = ohlcv_all[_fca_idx] if (ohlcv_all and
                       not isinstance(ohlcv_all[_fca_idx], Exception)) else []
            _fca_cand = next((c for c in candidates
                              if c.get("symbol") == (symbols[0] if symbols else "BTC")), {})
            _fca_p   = float(_fca_cand.get("price", 0) or 0)
            if len(_fca_c) >= 30 and _fca_p > 0:
                _fca = _calc_price_forecast(
                    _fca_c, days=30, fear_greed=fear_val,
                    # إصلاح #1070: _btc_dom_real من onchain يُقدَّم على regime
                    btc_dominance=float(_btc_dom_real if _btc_dom_real != 56 else
                        ((regime.metrics or {}).get("btc_dominance") or 56)),
                    market_regime=regime.description_ar,
                )
                if _fca:
                    lines.append(_format_forecast_ar(
                        symbols[0] if symbols else "BTC", _fca_p, _fca, days=30))
        except Exception as _fcae:
            logger.debug(f"planmonth forecast30: {_fcae}")

        # T23_fix: مصفوفة قرار بناءً على السيناريوهات
        _pm_matrix = []
        if fear_val <= 25 and "هابط" in regime.description_ar:
            _pm_matrix = [
                "🟢 إذا Fear > 35 + كسر مقاومة → دخول 30%",
                "⚪ إذا Fear 25-35 + تذبذب → انتظار + سيولة",
                "🔴 إذا Fear < 20 + كسر دعم → خروج كامل",
            ]
        elif "صاعد" in regime.description_ar:
            _pm_matrix = [
                "🟢 إذا RSI < 60 + Vol > 1x → دخول 50%",
                "⚪ إذا RSI > 70 → انتظار تصحيح 10-15%",
                "🔴 إذا كسر EMA50 → خروج 50%",
            ]
        else:
            _pm_matrix = [
                "🟢 إذا Fear < 30 + ADX > 20 → دخول محدود 25%",
                "⚪ إذا ADX < 15 → انتظار اتجاه واضح",
                "🔴 إذا Fear > 70 → تحقيق أرباح جزئي",
            ]

        if _pm_matrix:
            lines += ["", "🎯 *مصفوفة القرار الشهري*"]
            lines += [f"  {s}" for s in _pm_matrix]

        lines += ["", "📅 *جدول الشهر المقترح*"] + week_plan + [
            "",
            # T38_plan: إشارة لأمر /market_outlook
            "📊 *رؤية المؤسسات:* للاطلاع على توقعات BlackRock وVanguard وMorningstar: /outlook",
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]
        # plan_execute_v2: أزرار من _sym_prices_pw (بدون طلبات جديدة)
        _exec_buttons = []
        for _sym_pw in symbols[:10]:
            _p_pw = _sym_prices_pw.get(_sym_pw, 0)
            if _p_pw > 0:
                _exec_buttons.append([
                    InlineKeyboardButton(
                        f"⚡ تنفيذ {_sym_pw}",
                        callback_data=f"plan_exec:{_sym_pw}:{_p_pw:.4f}"
                    )
                ])
        _exec_buttons.append([
            InlineKeyboardButton("✅ أتفق مع الخطة", callback_data="plan_agree"),
            InlineKeyboardButton("💬 لدي ملاحظة",   callback_data="plan_comment"),
        ])
        _fb_kb = InlineKeyboardMarkup(_exec_buttons)
        await msg.edit_text(
            _clean("\n".join(lines)),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_fb_kb)

    except Exception as e:
        logger.error(f"cmd_plan_month: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ خطأ في بناء الخطة: {str(e)[:100]}")
        except Exception:
            await _get_message_p(update, context).reply_text("❌ خطأ في بناء الخطة الشهرية")
    finally:
        context.user_data["planmonth_running"] = False
        # إصلاح #178/#123: تحرير semaphore — كان يُحرَّر متغير خاطئ/غير
        # معرَّف (_heavy_sem_m) فيُفشَل بصمت (NameError مُعتَرَضة)، فيُسرَّب
        # permit دائماً من Semaphore(3) المشترك بين /monthly و/weekly
        # لكل المستخدمين — بعد 3 استدعاءات يتجمد كل النظام للأبد حتى الريستارت
        try:
            _heavy_sem_w.release()
        except Exception:
            pass


@require_tier("planweek")
# ══ Plan Start — سؤال أولاً (T3) ══════════════════════════════════════════════

async def _run_planweek(update, context, msg=None):
    """تشغيل الخطة الأسبوعية — يستقبل msg اختيارياً من callback."""
    # نُعيد توجيه context.user_data لـ cmd_plan_week
    context.user_data["_plan_msg_override"] = msg
    await cmd_plan_week(update, context)


async def _run_planmonth(update, context, msg=None):
    """تشغيل الخطة الشهرية — يستقبل msg اختيارياً من callback."""
    context.user_data["_plan_msg_override"] = msg
    await cmd_plan_month(update, context)


@require_tier("planweek")
async def cmd_planweek_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نقطة دخول /planweek — يسأل نوع الأصول أولاً ثم عام/محدد."""
    _uid_e  = update.effective_user.id if update.effective_user else 0
    _tier_e = _sm.get_tier(_uid_e) or "silver"
    _lim_c  = _get_plan_limit(_tier_e, "crypto")
    _lim_t  = _get_plan_limit(_tier_e, "tokenized")
    _info   = (
        f"ℹ️ حدود باقتك ({_tier_e}):\n"
        f"• عملات رقمية: حتى {_lim_c}\n"
        + (f"• أصول مُرمَّزة: حتى {_lim_t}" if _lim_t > 0 else "• أصول مُرمَّزة: غير متاحة")
    )
    _btns = [[InlineKeyboardButton("💰 عملات رقمية", callback_data="plan_w_asset:crypto")]]
    if _lim_t > 0:
        _btns[0].append(InlineKeyboardButton("📈 أصول مُرمَّزة", callback_data="plan_w_asset:tokenized"))
        _btns.append([InlineKeyboardButton("🔀 الاثنان معاً", callback_data="plan_w_asset:both")])
    await _get_message_p(update, context).reply_text(
        f"📅 *الخطة الأسبوعية*\n\n{_info}\n\nما نوع الأصول التي تريد تحليلها؟",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(_btns))


@require_tier("planmonth")
async def cmd_planmonth_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نقطة دخول /planmonth — يسأل نوع الأصول أولاً ثم عام/محدد."""
    _uid_e  = update.effective_user.id if update.effective_user else 0
    _tier_e = _sm.get_tier(_uid_e) or "silver"
    _lim_c  = _get_plan_limit(_tier_e, "crypto")
    _lim_t  = _get_plan_limit(_tier_e, "tokenized")
    _info   = (
        f"ℹ️ حدود باقتك ({_tier_e}):\n"
        f"• عملات رقمية: حتى {_lim_c}\n"
        + (f"• أصول مُرمَّزة: حتى {_lim_t}" if _lim_t > 0 else "• أصول مُرمَّزة: غير متاحة")
    )
    _btns = [[InlineKeyboardButton("💰 عملات رقمية", callback_data="plan_m_asset:crypto")]]
    if _lim_t > 0:
        _btns[0].append(InlineKeyboardButton("📈 أصول مُرمَّزة", callback_data="plan_m_asset:tokenized"))
        _btns.append([InlineKeyboardButton("🔀 الاثنان معاً", callback_data="plan_m_asset:both")])
    await _get_message_p(update, context).reply_text(
        f"📅 *الخطة الشهرية*\n\n{_info}\n\nما نوع الأصول التي تريد تحليلها؟",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(_btns))


async def cmd_plan_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    # تطوير #223: سؤال Spot/Futures للماسي+ فقط في الخطة الأسبوعية
    _uid_pw_pre  = update.effective_user.id if update.effective_user else 0
    _tier_pw_pre = _sm.get_tier(_uid_pw_pre)
    _mkttype_pw  = context.user_data.pop("_mkttype_plan", None)
    if _mkttype_pw is None and _tier_pw_pre in ("diamond", "admin"):
        _args_str = " ".join(context.args or [])
        kb_pw = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Spot (كل المستخدمين)", callback_data=f"planmkt_week_spot_{_args_str}"),
             InlineKeyboardButton("📈 Futures + أسهم (ماسي+)", callback_data=f"planmkt_week_futures_{_args_str}")],
        ])
        # FF2 (#2085): None check قبل reply_text
        _msg_pw = _get_message_p(update, context)
        if _msg_pw is None:
            return
        await _msg_pw.reply_text(
            "📅 *الخطة الأسبوعية — اختر نوع السوق:*\n"
            "_(Spot لجميع الأصول — Futures + الأسهم المُرمَّزة للماسي+)_",
            parse_mode="Markdown", reply_markup=kb_pw)
        return
    _use_futures_pw = (_mkttype_pw == "futures")

    args    = context.args or []
    # tier_pw_fix: تعريف _tier_pw مسبقاً لتجنب UnboundLocalError
    _tier_pw = _sm.get_tier(update.effective_user.id) or "silver"
    # إصلاح #971: تعريف مسبق لجميع المتغيرات
    entry_syms = []
    candidates = []
    allocation = None
    _pair_resolutions = {}
    if args:
        # plan_limits: حد حسب الباقة
        _limit_w = _get_plan_limit(_tier_pw, "crypto")
        _raw_syms2 = [a.upper() for a in args[:_limit_w]]
        # تطوير #188 (Phase 2): دعم أزواج BTC/ETH (ماسي+ فقط)
        _tier_pw   = _sm.get_tier(update.effective_user.id) or "silver"
        symbols, _resols_pw, _display_syms_pw = await _resolve_custom_symbols(
            _raw_syms2, _tier_pw, engine.data_layer)
        _pair_resolutions = dict(zip(symbols, _resols_pw))
        _display_map_pw = dict(zip(symbols, _display_syms_pw))
        sym_str2 = ", ".join(symbols)
        # PLAN_HDR_fix: استخدام display names (XGOOGL وليس GOOGL)
        _display_sym_str2 = ", ".join(_display_syms_pw) if _display_syms_pw else sym_str2
        plan_label = f"خطة مخصصة لـ {_display_sym_str2}"
    else:
        # plan_asset_fix: استخدام asset_type المُحدَّد من المستخدم
        _asset_type_pw = context.user_data.pop("_plan_asset_type", "crypto")
        _lim_c_pw = _get_plan_limit(_tier_pw, "crypto")
        _lim_t_pw = _get_plan_limit(_tier_pw, "tokenized")

        _default_crypto = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "LINK", "DOT", "TRX"]
        _default_token  = ["XSPY", "XSPCX", "XQQQ", "XXLE", "XAAPL", "XNVDA", "XMSFT", "XAMZN", "XGOOGL", "XTSLA"]

        if _asset_type_pw == "tokenized":
            symbols    = _default_token[:_lim_t_pw]
            plan_label = f"خطة أصول مُرمَّزة — Top {len(symbols)}"
        elif _asset_type_pw == "both":
            _c_syms = _default_crypto[:_lim_c_pw // 2 or 4]
            _t_syms = _default_token[:_lim_t_pw // 2 or 4]
            symbols    = _c_syms + _t_syms
            plan_label = f"خطة شاملة — رقمية + مُرمَّزة ({len(symbols)} أصل)"
        else:  # crypto
            symbols    = _default_crypto[:_lim_c_pw]
            plan_label = f"خطة عملات رقمية — Top {len(symbols)}"

        sym_str2 = ", ".join(symbols)
    # إصلاح #875: دعم msg من callback
    _msg_ov_w = context.user_data.pop("_plan_msg_override", None)
    if _msg_ov_w:
        msg = _msg_ov_w
        try:
            await msg.edit_text("📅 جاري بناء الخطة الأسبوعية...")
        except Exception:
            pass
    else:
        msg = await _get_message_p(update, context).reply_text(
        f"📅 جاري بناء {plan_label}...\n"
        "⏳ قد يستغرق 1-3 دقائق — يُرجى الانتظار"
    )
    # إصلاح #178: semaphore للأوامر الثقيلة
    _heavy_sem_m = await engine.acquire_heavy()
    await _heavy_sem_m.acquire()
    try:

        # ── 1. بيانات أساسية + OHLCV — كلها متوازية ─────────
        gathered = await asyncio.gather(
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_ohlcv("BTC", "1d", 200),
            # XAMZN_EMA50_fix v2: X-prefix → OKX Spot مباشرة (XAMZN/XGOOGL/XSPCX)
            *[engine.data_layer.get_ohlcv(sym.upper(), "1d", 50,
                  mkttype="spot", _cache_hint=f"pw2_{sym.upper()}")  # pw2: force cache refresh
               if (sym.upper().startswith("X") and len(sym) > 2)
               else engine.data_layer.get_ohlcv_perp(sym, 100)
               if sym.upper() in {"SPCX","COIN","AAPL","NVDA","TSLA","MSFT","AMZN",
                                   "GOOGL","META","MSTR","OPENAI","ANTHROPIC","AMD","OKB"}
               else engine.data_layer.get_ohlcv(sym, "1d", 100)
               for sym in symbols],
            engine.data_layer.get_news(currencies=",".join(symbols), limit=10),
            return_exceptions=True,
        )
        # retry لأي عملة فشل جلب بياناتها
        ohlcv_sym_raw = [gathered[3+i] if isinstance(gathered[3+i], list) else []
                         for i in range(len(symbols))]
        for i, sym in enumerate(symbols):
            if len(ohlcv_sym_raw[i]) < 20:
                logger.warning(f"planweek: retry OHLCV for {sym}")
                await asyncio.sleep(1)
                # XAMZN_EMA50_fix: retry بـ X-prefix الكامل
                _retry_sym = sym.upper()
                retry = await engine.data_layer.get_ohlcv(
                    _retry_sym, "1d", 100, _cache_hint=_retry_sym)
                if isinstance(retry, list) and len(retry) >= 20:
                    ohlcv_sym_raw[i] = retry
        fear      = gathered[0] if isinstance(gathered[0], dict)  else {"value": 50, "label_ar": "محايد"}
        onchain   = gathered[1] if isinstance(gathered[1], dict)  else {}
        btc_c     = (gathered[2] if isinstance(gathered[2], list) else []) or []
        ohlcv_sym = ohlcv_sym_raw  # بعد retry
        news_raw  = gathered[3+len(symbols)] if isinstance(gathered[3+len(symbols)], list) else []
        fear_val  = int((fear or {}).get("value") or 50)
        _btc_dom_real = float((onchain or {}).get('btc_dominance') or
                               (onchain or {}).get('btc_dom') or 56)  # #1017

        from core.regime_detector import Regime, RegimeResult
        if len(btc_c) >= 30:
            regime = engine.regime_detector.detect(btc_c, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        # ── 2. تحليل الأخبار مرة واحدة ────────────────────────
        try:
            news_an = await engine.news_engine.analyze(news_raw, symbols)
            news_an = news_an or {}
        except Exception:
            news_an = {}
        news_sentiment = float(news_an.get("sentiment_score") or 0)

        # ── 3. أسعار لجميع العملات — متوازية ─────────────────
        # plan_exec_price_fix: نحتفظ بالأسعار لأزرار التنفيذ
        _sym_prices_pw = {}
        price_results = await asyncio.gather(
            *[engine.data_layer.get_price_perp(sym)
               if sym.upper() in {"SPCX","COIN","AAPL","NVDA","TSLA","MSFT","AMZN","GOOGL","META","MSTR","OPENAI","ANTHROPIC","AMD","OKB"}
               else engine.data_layer.get_price(sym)
               for sym in symbols],
            return_exceptions=True,
        )

        # T22_fix: قسم السياق الكلي + قرار واضح
        _fear_label = fear.get("label_ar", "محايد")
        _regime_ar  = regime.description_ar

        # قرار موقفي بناءً على السوق
        if fear_val <= 20 and "هابط" in _regime_ar:
            _pw_decision = "🚨 *لا دخول* — خوف شديد + سوق هابط"
        elif fear_val <= 30 and "هابط" in _regime_ar:
            _pw_decision = "⚠️ *تقليل التعرض* — سوق هابط + خوف"
        elif fear_val <= 40:
            _pw_decision = "🟡 *انتظار* — شروط الدخول غير مكتملة"
        elif "صاعد" in _regime_ar and fear_val >= 50:
            _pw_decision = "✅ *يمكن الدخول بحجم طبيعي* — شروط مناسبة"
        else:
            _pw_decision = "🟡 *دخول محدود* — انتظر تأكيداً إضافياً"

        lines = [
            "📅 *الخطة الأسبوعية — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"السوق: {_regime_ar}",
            f"Fear & Greed: {fear_val} — {_fear_label}",
            "",
            f"🎯 *القرار الأسبوعي:* {_pw_decision}",
            "",
        ]

        _buy_signals = []  # إصلاح #382: تجميع إشارات الشراء
        for i, sym in enumerate(symbols):
            # TK6_fix: تحقق من توفر الأصل في Spot في plan_week
            if not _use_futures_pw:  # إصلاح: كان _use_futures_pm خطأ
                try:
                    _sp_chk_pw = await engine.data_layer.check_spot_available(sym)
                    if not _sp_chk_pw.get("available", True):
                        continue  # تجاهل الأصل غير المتاح في Spot
                except Exception:
                    pass
            candles = ohlcv_sym[i]
            # GG4 (#1478): retry فوري عند بيانات غير كافية بدلاً من التخطي
            if len(candles) < 20:
                try:
                    _retry_c = await asyncio.wait_for(
                        engine.data_layer.get_ohlcv(sym, "1d", 50),
                        timeout=10.0)
                    if isinstance(_retry_c, list) and len(_retry_c) >= 20:
                        candles = _retry_c
                    else:
                        lines.append(f"⚠️ {sym}: بيانات غير كافية")
                        lines.append("")
                        continue
                except Exception:
                    lines.append(f"⚠️ {sym}: بيانات غير كافية")
                    lines.append("")
                    continue
            try:
                # إصلاح #34: whale_ratio/funding خاص بكل عملة بدل TVL عالمي ثابت
                _onchain_e = await engine.data_layer.get_signal_enrichment(sym, onchain)
                signal   = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=_onchain_e,
                    news_sentiment=news_sentiment,
                    backtest_win_rate=0.55,
                    macro_data={"fear_greed": fear_val},
                    regime=regime,
                )
                strat, _ = engine.strategy_router.select(regime, signal)
                price_d  = price_results[i] if not isinstance(price_results[i], Exception) else None
                price    = float((price_d or {}).get("price") or 0)
                if price > 0: _sym_prices_pw[sym] = price

                dir_ar = ("🟢 شراء" if signal.direction == "long"
                          else "🔴 بيع" if signal.direction == "short"
                          else "⚪ انتظار")
                # إصلاح #382: تسجيل إشارات الشراء القوية
                if signal.direction == "long" and signal.confidence >= 0.65:
                    _buy_signals.append(sym)
                strat_name = strat.value.replace("_", " ")

                # حساب التغيير 24h
                change_24h = float((price_d or {}).get("change_24h") or 0)
                change_str = f" ({change_24h:+.2f}%)" if change_24h != 0 else ""

                # مستويات الدخول/الخروج المقترحة
                # حساب ATR ومستويات دقيقة
                # SOL_EMA50_fix: reset جميع المتغيرات المحسوبة من candles لكل عملة
                atr_v        = 0.03  # default
                closes_w     = []
                ema20_w      = price
                ema50_w      = price
                _ema50_w_raw = price
                rsi_w        = 50.0
                if candles and len(candles) > 14:
                    atr_v = _calc_atr(candles) / 100
                if candles:
                    closes_w = [float(c.get("close", 0)) for c in candles if c.get("close")]
                if len(closes_w) >= 20:
                    ema20_w = sum(closes_w[-20:]) / 20
                if len(closes_w) >= 50:
                    _ema50_w_raw = sum(closes_w[-50:]) / 50
                    ema50_w = _ema50_w_raw if _ema50_w_raw <= price * 3.0 else price
                rsi_w    = _calc_rsi(candles) if len(candles) >= 15 else 50.0
                # EMA50_leak_fix: حساب _ema_cond و_rsi_cond هنا (قبل جميع الكتل)
                _rsi_t_w = 35 if "هابط" in regime.description_ar else 45
                # rsi_plan_fix: RSI>=70 → تحذير لا تأكيد
                if rsi_w >= 70:
                    _rsi_cond = f"  • ⚠️ RSI = {rsi_w:.0f} ذروة شراء — انتظر تصحيح تحت 60"
                else:
                    _rsi_warn_w = ""
                    _rsi_cond = (f"  • ✅ RSI فوق {_rsi_t_w} (حالياً {rsi_w:.0f}){_rsi_warn_w} — مُستوفى"
                             if rsi_w > _rsi_t_w else
                             f"  • RSI يرتفع فوق {_rsi_t_w} (حالياً {rsi_w:.0f})")
                # BGB_fix: إذا ema50_w=0 → استخدم السعر
                if ema50_w <= 0:
                    ema50_w = price
                _ema50_dist_w = abs(price - ema50_w) / max(ema50_w, 1) * 100
                _ema_cond = (f"  • ✅ السعر فوق EMA50 ({_fmt_price(ema50_w)}) — مُستوفى"
                             if price > ema50_w else
                             f"  • إغلاق فوق EMA50 ({_fmt_price(ema50_w)} — بُعد {_ema50_dist_w:.1f}%)")

                # Fibonacci سريع
                _fib_candles = candles[-30:] if len(candles) >= 30 else candles
                swing_h = max([float(c.get("high", price)) for c in _fib_candles]) if _fib_candles else price
                swing_l = min([float(c.get("low",  price)) for c in _fib_candles]) if _fib_candles else price
                # XPCY_fib_fix v2: guard إذا swing_h == swing_l (بيانات محدودة)
                if swing_h <= swing_l or (swing_h - swing_l) < price * 0.001:
                    _atr_est = atr_v if atr_v > 0 else 0.03
                    swing_h = price * (1 + _atr_est * 3)
                    swing_l = price * (1 - _atr_est * 3)
                fib_382 = swing_l + (swing_h - swing_l) * 0.382
                fib_618 = swing_l + (swing_h - swing_l) * 0.618

                # إصلاح #116: "دعم/مقاومة" المعروضة يجب أن تُحسَب ديناميكياً
                # حسب موضع السعر الحالي ضمن مستويات السوينغ — لا نفترض دائماً
                # أن fib_382=دعم وfib_618=مقاومة (قد يكون السعر هبط تحتهما)
                _levels = sorted([swing_l, fib_382, fib_618, swing_h])
                _below  = [lv for lv in _levels if lv < price]
                _above  = [lv for lv in _levels if lv > price]
                _disp_support    = max(_below) if _below else swing_l
                _disp_resistance = min(_above) if _above else swing_h

                entry_lines = []
                if price > 0:
                    # إصلاح #366: R/R ديناميكي حسب RSI — نفس منطق analysis.py
                    if rsi_w < 15:
                        _tp_m, _sl_m = 4.0, 0.8
                    elif rsi_w < 25:
                        _tp_m, _sl_m = 3.0, 1.0
                    elif rsi_w < 35:
                        _tp_m, _sl_m = 2.5, 1.0
                    elif rsi_w > 70:
                        _tp_m, _sl_m = 1.5, 1.5
                    else:
                        _tp_m, _sl_m = 2.0, 1.2

                    # إصلاح #11: فصل short عن long بشكل صحيح (كان elif غير قابل للوصول)
                    if signal.direction == "short" and signal.confidence >= 0.40:
                        entry = min(fib_618, price * (1 + atr_v * 0.3))
                        tp1   = price * (1 - atr_v * 1.5)
                        sl    = entry * (1 + atr_v * 1.2)
                        rr    = (entry - tp1) / max(sl - entry, 1e-9)
                        if rr >= 1.0:
                            entry_lines = [
                                f"  📍 Short Limit: {_fmt_price(entry)} | وقف: {_fmt_price(sl)} (+{atr_v*120:.1f}%)",
                                f"  🎯 هدف: {_fmt_price(tp1)} (-{atr_v*150:.1f}%) | R/R: 1:{rr:.1f}",
                            ]
                    elif signal.confidence >= 0.40:
                        entry = min(max(fib_382, price * (1 - atr_v * 0.5)), price * 0.999)
                        # إصلاح #827/#870: TP وR/R صحيح
                        _tp1_pct = min(0.06, atr_v * _tp_m)   # max 6%
                        _tp2_pct = min(0.09, atr_v * _tp_m * 1.4)  # max 9%
                        tp1   = price * (1 + _tp1_pct)
                        tp2   = price * (1 + _tp2_pct)
                        sl    = entry * (1 - min(atr_v * _sl_m, 0.07))
                        _risk = max(price - sl, 1e-9)
                        _rew  = max(tp1 - price, 1e-9)
                        rr    = min(_rew / _risk, 4.0)
                        # إصلاح #871/#11: إذا R/R < 1.2 → لا نُخفي السطر بالكامل
                        # بل نقع للأسفل إلى كتلة "شروط الدخول" (entry_lines يبقى فارغاً)
                        if rr >= 1.2:
                            entry_lines = [
                                f"  📍 دخول: {_fmt_price(entry)} | وقف: {_fmt_price(sl)} ({abs(price-sl)/max(price,1e-9)*100:.1f}%-)",
                                f"  🎯 هدف1: {_fmt_price(tp1)} (+{_tp1_pct*100:.1f}%) | هدف2: {_fmt_price(tp2)} (+{_tp2_pct*100:.1f}%)",
                                f"  📊 R/R: 1:{rr:.1f} | ATR: {atr_v*100:.1f}%",
                            ]

                    # T2_plan_bug_fix_v2: تعريف _t_entry_plan هنا (يشمل جميع المسارات)
                    _t_entry_plan = _TIER_CONF_PLAN.get(_tier_pw, 65)
                    # SOL_bug_fix: reset pro_* في بداية كل iteration لمنع تسرب بيانات العملة السابقة
                    pro_entry_w = price * 0.99
                    pro_tp_w    = price * 1.05
                    pro_sl_w    = price * 0.93
                    rr_w        = 1.5
                    # إصلاح #11: fallback موحَّد لأي حالة بدون entry_lines
                    # (ثقة < 40% أو R/R < 1.2) — يضمن أن كل عملة تُعرض ببيانات كاملة
                    if not entry_lines:
                        rsi_t = 35 if "هابط" in regime.description_ar else 45
                        pro_entry_w = max(fib_382, price * (1 - atr_v * 0.5)) if fib_382 < price else price * (1 - atr_v * 0.4)
                        pro_tp_w    = price * (1 + atr_v * 1.8)
                        pro_sl_w    = pro_entry_w * (1 - atr_v * 1.2)
                        rr_w        = abs(pro_tp_w - pro_entry_w) / max(abs(pro_sl_w - pro_entry_w), 1e-9)
                        # EMA50_leak_fix: _rsi_cond و_ema_cond مُحسَّبان أعلاه
                        # نُعيَّن rsi_t للاتساق مع _rsi_cond أعلاه
                        rsi_t = _rsi_t_w
                        # T2_plan_bug_fix: تعريف _t_entry_plan قبل استخدامه
                _t_entry_plan = _TIER_CONF_PLAN.get(_tier_pw, 65)
                entry_lines = [
                            f"  ⏳ شروط الدخول:",
                            _rsi_cond,
                            _ema_cond,
                            # T2_plan_fix: عتبة حسب الباقة
                            f"  • الثقة ≥ {_t_entry_plan}% (حالياً {signal.confidence:.0%})",
                            # plan_pro_fix: label واضح حسب حالة الإشارة
                            (f"  🛡️ خيار المحترف: Limit @ {_fmt_price(pro_entry_w)} | وقف: {_fmt_price(pro_sl_w)} | هدف: {_fmt_price(pro_tp_w)} | R/R: 1:{rr_w:.1f}"
                             if signal.confidence > _t_entry_plan / 100
                             else f"  🔒 خيار المحترف: غير مُفعَّل (ثقة {signal.confidence*100:.0f}% ≤ {_t_entry_plan}%)"),
                            f"  📊 Fib دعم: {_fmt_price(_disp_support)} | مقاومة: {_fmt_price(_disp_resistance)}",
                        ]

                # T2_eq_fix: "دون حد" يعتمد على confidence فقط وليس direction
                conf_warning = (" ⚠️ دون حد الدخول"
                                if signal.confidence < _t_entry_plan / 100
                                else "")
                price_str = f" — {_fmt_price(price)}{change_str}" if price > 0 else " — 🔄 جاري جلب السعر"
                # تطوير #188 (Phase 2): فقرة الزوج المُكثَّفة إن وُجدت
                _res_w = _pair_resolutions.get(sym)
                _pair_inline = await build_pair_addon_inline(_res_w, engine.data_layer) if _res_w else ""
                # PLAN_NAME_fix: استخدام الرمز الأصلي (XGOOGL وليس GOOGL)
                _disp_sym_pw = _display_map_pw.get(sym, sym) if "_display_map_pw" in dir() else sym
                lines += [
                    f"💎 *{_disp_sym_pw}*{price_str}{_pair_inline}",
                    f"  الإشارة: {dir_ar} | الثقة: {signal.confidence:.0%}{conf_warning}",
                    f"  الاستراتيجية: {strat_name}",
                ] + entry_lines + [""]
            except Exception as e:
                logger.warning(f"plan_week {sym}: {e}")
                lines.append(f"⚠️ {sym}: {str(e)[:50]}")
                lines.append("")

        # ── 4. تنبؤ 30 يوم (#139/#208) ───────────────────────
        try:
            _fc_sym    = symbols[0] if symbols else "BTC"
            _fc_idx    = 0
            _fc_candles = ohlcv_sym[_fc_idx] if (ohlcv_sym and
                          not isinstance(ohlcv_sym[_fc_idx], Exception)) else []
            _fc_price   = 0.0
            if not isinstance(price_results[_fc_idx], Exception):
                _fc_price = float((price_results[_fc_idx] or {}).get("price", 0) or 0)
            if len(_fc_candles) >= 30 and _fc_price > 0:
                _fc = _calc_price_forecast(
                    _fc_candles, days=30,
                    fear_greed=fear_val,
                    # إصلاح #1075: _btc_dom_real من onchain
                    btc_dominance=float(_btc_dom_real if _btc_dom_real != 56 else
                        ((getattr(regime, "metrics", {}) or {}).get("btc_dominance") or 56)),
                    market_regime=regime.description_ar,
                )
                if _fc:
                    lines.append(_format_forecast_ar(_fc_sym, _fc_price, _fc, days=30))
        except Exception as _fce:
            logger.debug(f"planweek forecast30: {_fce}")

        # ── 5. الأحداث والجدول ────────────────────────────────
        events_text = engine.event_risk.format_upcoming_ar(hours=168)

        # events_groq_fix: تحديث الأحداث عبر Groq إذا كانت قديمة
        try:
            from datetime import datetime as _dt_gr, timezone as _tz_gr
            _now_gr = _dt_gr.now(_tz_gr.utc)
            _current_date_str = _now_gr.strftime("%Y-%m-%d")
            # إذا كانت events_text تحتوي FOMC يوليو 2026 أو أحداث قديمة → استبدلها
            _has_old = (
                ("2026-07" in events_text) or
                ("2026-06" in events_text) or
                not events_text.strip()
            )
            if _has_old and hasattr(engine.news_engine, "groq_key") and engine.news_engine.groq_key:
                import urllib.request, urllib.error, json as _jg, ssl as _slg
                _body_gr = _jg.dumps({
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{
                        "role": "system",
                        "content": "أجب بالعربية فقط. أعطِ قائمة بالأحداث الاقتصادية المهمة القادمة خلال 7 أيام."
                    }, {
                        "role": "user",
                        "content": (
                            f"اليوم هو {_current_date_str}. "
                            "اذكر أهم 3-4 أحداث اقتصادية قادمة خلال 7 أيام (FOMC، CPI، NFP، إلخ) "
                            "بهذا التنسيق لكل حدث في سطر: "
                            "⏰ [اسم الحدث] — بعد [X] ساعة ([تأثير: 🔴/🟠/🟡])"
                        )
                    }],
                    "max_tokens": 300,
                    "temperature": 0.1
                }).encode()
                _req_gr = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=_body_gr,
                    headers={"Authorization": f"Bearer {engine.news_engine.groq_key}",
                             "Content-Type": "application/json"},
                    method="POST")
                _ctx_gr = _slg.create_default_context()
                with urllib.request.urlopen(_req_gr, context=_ctx_gr, timeout=10) as _rg:
                    _dg = _jg.loads(_rg.read())
                    _gr_events = _dg["choices"][0]["message"]["content"].strip()
                    if _gr_events and len(_gr_events) > 20:
                        events_text = _gr_events
        except Exception as _eg:
            logger.debug(f"events_groq_fix: {_eg}")
        # إصلاح تنسيق "بعد 20ساعة" → "بعد 20 ساعة"
        events_text = re.sub(r'(بعد\s*)(\d+)(ساعة)', r'بعد  ساعة', events_text)
        events_text = re.sub(r'(بعد\s*)(\d+)(يوم)', r'بعد  يوم', events_text)
        # events_filter_fix v2: فلتر متقدم للأحداث المنتهية
        try:
            from datetime import datetime as _dt_ev, timezone as _tz_ev
            _now_ev = _dt_ev.now(_tz_ev.utc)
            _ev_raw = events_text.split("\n")
            _ev_ok = []
            for _el in _ev_raw:
                import re as _re_ev
                # حذف أحداث بساعات سالبة
                _m_hr = _re_ev.search(r'بعد\s*(-?\d+)\s*ساعة', _el)
                if _m_hr and int(_m_hr.group(1)) < 0:
                    continue
                # حذف FOMC إذا كان في يوليو 2026 أو قبله (انتهى 29 يوليو)
                if ("الفيدرالي" in _el or "FOMC" in _el):
                    _m_date = _re_ev.search(r'20(\d\d)-0?(\d+)-0?(\d+)', _el)
                    if _m_date:
                        _yr = int("20" + _m_date.group(1))
                        _mo = int(_m_date.group(2))
                        _dy = int(_m_date.group(3))
                        from datetime import date as _date_ev
                        if _date_ev(_yr, _mo, _dy) < _now_ev.date():
                            continue  # حدث منتهٍ
                _ev_ok.append(_el)
            events_text = "\n".join(_ev_ok)
        except Exception:
            pass
        sched_text  = engine.scheduler.next_weekly_ar() if engine.scheduler else ""

        # إصلاح #293/#252: week_plan ديناميكي في planweek
        _rsi_pw  = float((getattr(regime,"metrics",{}) or {}).get("rsi", 50) or 50)
        _fg_pw   = fear_val
        if regime.regime.value in ("bear_trend", "distribution"):
            _rsi_lbl  = f"حالياً {_rsi_pw:.0f} — انتظر > 30" if _rsi_pw < 30 else f"RSI = {_rsi_pw:.0f} > 30 ✓"
            _fg_lbl   = f"Fear = {_fg_pw} < 25 ✓" if _fg_pw < 25 else f"حالياً {_fg_pw} — انتظر < 25"
            _buy_names = " و".join(_buy_signals[:3]) if _buy_signals else ""

            # إصلاح #382: week_lines تعكس إشارات الشراء الفعلية
            if _buy_signals and _rsi_pw < 20:
                # ذروة بيع تاريخية + إشارات شراء → دخول تكتيكي
                week_lines = [
                    f"• أسبوع 1: دخول تكتيكي محدود (25%) في {_buy_names} — RSI={_rsi_pw:.0f} ذروة بيع تاريخية",
                    f"• أسبوع 2: مراقبة — وقف صارم إذا كسر الدعم | زيادة عند RSI > 25",
                    f"• أسبوع 3: Fear & Greed < 25 → زيادة تدريجية عند تأكيد القاع ({_fg_lbl})",
                    "• أسبوع 4: مراجعة المراكز وقرار الاستمرار",
                ]
            elif _buy_signals and _rsi_pw < 30:
                week_lines = [
                    f"• أسبوع 1: انتظار تأكيد — RSI يرتد فوق 30 ({_rsi_lbl})",
                    f"• أسبوع 2: دخول تدريجي في {_buy_names} عند ارتداد RSI فوق 35",
                    f"• أسبوع 3: Fear & Greed < 25 → {'🟢 ابدأ التجميع التدريجي' if _fg_pw < 25 else 'انتظر تأكيد ارتداد'} ({_fg_lbl})",
                    "• أسبوع 4: تقييم القاع — قرار الدخول الكامل",
                ]
            else:
                # إصلاح #819 (N3): Fear<20 يُفعَّل تجميع محدود في أسبوع 1
                week_lines = [
                    (f"• أسبوع 1: 🟡 تجميع محدود 3-5% عند الدعم — Fear={_fg_pw} < 20 (خوف شديد = فرصة)"
                     if _fg_pw < 20
                     else f"• أسبوع 1: لا دخول — RSI يرتد فوق 30 ({_rsi_lbl})"),
                    "• أسبوع 2: دخول تدريجي عند ارتداد RSI فوق 35",
                    f"• أسبوع 3: Fear & Greed < 25 → {'🟢 ابدأ التجميع التدريجي' if _fg_pw < 25 else 'انتظر تأكيد ارتداد'} ({_fg_lbl})",
                    "• أسبوع 4: تقييم القاع — قرار الدخول الكامل",
                ]
        elif regime.regime.value in ("bull_trend", "accumulation"):
            week_lines = [
                "• أسبوع 1: دخول مبكر عند أول تراجع",
                "• أسبوع 2: مضاعفة المراكز الرابحة",
                "• أسبوع 3: رفع وقف الخسارة للتعادل",
                "• أسبوع 4: جني 30-50% من الأرباح",
            ]
        else:
            week_lines = [
                f"• أسبوع 1: مراقبة — RSI حالياً {_rsi_pw:.0f}",
                "• أسبوع 2: دخول جزئي (25%) إذا RSI > 35 وFear < 35",
                "• أسبوع 3: مراجعة وتعديل وقف الخسارة",
                "• أسبوع 4: تقييم النتائج وقرار الاستمرار",
            ]
        lines += ["", "📅 *خطة الأسبوع المقترحة*"] + week_lines + [""]

        lines += ["📅 *أحداث الأسبوع*"]
        # نُزيل العنوان المكرر من format_upcoming_ar
        ev_lines = events_text.split("\n")
        ev_body  = "\n".join(ev_lines[2:] if len(ev_lines) > 2 and "━━" in ev_lines[1] else ev_lines)
        lines.append(ev_body)

        if sched_text:
            lines += ["", f"⏰ {sched_text}"]
        lines += [
            "",
            # T38_plan: إشارة لأمر /market_outlook في plan_week
            "📊 *رؤية المؤسسات:* BlackRock · Vanguard · Morningstar → /outlook",
            "",
            "⚠️ خطة استرشادية — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]

        # T3: أزرار تفاعل
        _fb_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ أتفق مع الخطة", callback_data="plan_agree"),
            InlineKeyboardButton("💬 لدي ملاحظة",   callback_data="plan_comment"),
        ]])
        # auditing_agent: تدقيق /plan_week قبل الإرسال
        _pw_text = _clean("\n".join(lines))
        _pw_approved, _pw_text = audit_financial_content(_pw_text, source="plan")
        if not _pw_approved:
            logger.warning("auditing_agent: /plan_week مرفوض → fallback")
        await msg.edit_text(_pw_text, parse_mode=ParseMode.MARKDOWN, reply_markup=_fb_kb)

    except Exception as e:
        logger.error(f"cmd_plan_week: {e}")
        await msg.edit_text(f"❌ خطأ في بناء الخطة الأسبوعية: {str(e)[:100]}")
    finally:
        try:
            _heavy_sem_w.release()
        except Exception:
            pass


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    # منع الطلبات المتكررة
    if context.user_data.get("portfolio_running"):
        await _get_message_p(update, context).reply_text(
            "⏳ جاري تحليل المحفظة بالفعل — يُرجى الانتظار")
        return
    context.user_data["portfolio_running"] = True

    msg = await _get_message_p(update, context).reply_text(
        "💼 جاري تحليل المحفظة...\n"
        "⏳ قد يستغرق 1-3 دقائق — يُرجى الانتظار"
    )
    try:

        # ── 1. طلبات البيانات الأساسية — متوازية ─────────────
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

        # ── 2. جلب الأخبار العامة مرة واحدة (لا داخل الحلقة) ─
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

        # ── 3. OHLCV لجميع العملات — متوازية ─────────────────
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

        # ── 4. بناء المرشحين ──────────────────────────────────
        candidates = []
        for i, coin in enumerate(valid_coins):
            sym     = (coin.get("symbol") or "").upper()
            candles = ohlcv_results[i] if isinstance(ohlcv_results[i], list) else []
            liq     = liq_results[i]   if not isinstance(liq_results[i], Exception) else None

            if len(candles) < 30:
                continue
            try:
                # إصلاح #34: whale_ratio/funding خاص بكل عملة بدل TVL عالمي ثابت
                _onchain_e = await engine.data_layer.get_signal_enrichment(sym, onchain)
                signal = engine.signal_layer.generate(
                    symbol=sym, candles=candles, onchain_data=_onchain_e,
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

        # ── 5. توزيع المحفظة ──────────────────────────────────
        # إصلاح ملاحظة #4: استخدام قيمة المحفظة الفعلية للمستخدم
        from core.state_manager import state_manager as _sm_p
        from core.virtual_wallet import VirtualWallet as _VW_p
        _uid_p    = update.effective_user.id
        _vw_p_d   = _sm_p.get_virtual_wallet(_uid_p) or {}
        _vw_p     = _VW_p(_vw_p_d) if _vw_p_d else None
        _vw_total = _vw_p.total_value if _vw_p else float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        portfolio_val = _vw_total   # القيمة الفعلية من المحفظة الافتراضية
        allocation    = engine.capital_engine.allocate(
            candidates, portfolio_val, regime, ev_mult)
        risk_st       = engine.risk_engine.status_report(portfolio_val)

        # حساب PnL الحي
        _open_p     = len(_vw_p.positions) if _vw_p else 0
        _live_pnl_p = 0.0
        if _vw_p and _vw_p.positions:
            for _sym_p, _pos_p in _vw_p.positions.items():
                try:
                    _pd_p = await engine.data_layer.get_price(_sym_p.replace("USDT",""))
                    if _pd_p and _pd_p.get("price"):
                        _live_pnl_p += (float(_pd_p["price"]) - _pos_p["avg_price"]) * _pos_p["quantity"]
                except Exception:
                    pass
        _dd_p = max(0, (portfolio_val - _vw_total) / portfolio_val * 100)

        # #757: تحديث إجمالي المحفظة ليشمل PnL الحي
        _real_total = _vw_total + _live_pnl_p
        _real_invested = _vw_p.invested if _vw_p else 0
        text = _clean(engine.capital_engine.format_ar(allocation, regime, candidates))
        # استبدال إجمالي المحفظة في النص بالقيمة الحقيقية
        text = text.replace(
            f"إجمالي المحفظة:  ${portfolio_val:,.0f}",
            f"إجمالي المحفظة:  ${_real_total:,.0f}"
        )
        text += (
            f"\n\n⚖️ *حالة المخاطر*\n"
            f"• Drawdown: {_dd_p:.1f}%\n"
            f"• PnL الحي: ${_live_pnl_p:+,.2f}\n"
            f"• صفقات مفتوحة: {_open_p}"
        )
        # #759: أزرار التنقل
        _uid_port = update.effective_user.id
        _has_live_port = False
        try:
            _eng_port = context.bot_data.get("raed_engine")
            if _eng_port and hasattr(_eng_port, "get_user_exchange"):
                _has_live_port = bool(_eng_port.get_user_exchange(_uid_port))
        except Exception:
            pass
        _port_btns = [[InlineKeyboardButton("🎮 الصفقات الافتراضية", callback_data="goto_vtrades")]]
        if _has_live_port:
            _port_btns[0].append(InlineKeyboardButton("💱 الصفقات الحقيقية", callback_data="goto_real_trades"))
        await msg.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(_port_btns))

    except Exception as e:
        logger.error(f"cmd_portfolio: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ خطأ في تحليل المحفظة: {str(e)[:100]}")
        except Exception:
            await _get_message_p(update, context).reply_text("❌ خطأ في تحليل المحفظة — أعد المحاولة")
    finally:
        context.user_data["portfolio_running"] = False


@require_tier("stats")
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

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
        # إصلاح #301/#350 (I9): تنظيف "يوم X" خارج f-string
        import re as _re_plan
        # إصلاح #389 (J7): استخدام lambda بدل backreference لتجنب 
        _sched_m_str = _clean(sched_m).replace(' يوم ', ' ')
        _sched_m_match = _re_plan.search(r'(\d{4}-\d{2}-\d{2})', _sched_m_str)
        if _sched_m_match:
            _date_part = _sched_m_match.group(1)
            # احتفظ بالتاريخ + ما بعد الرقم الزائد
            _sched_m_clean = _re_plan.sub(
                r'\d{4}-\d{2}-\d{2} \d+ ',
                _date_part + ' ',
                _sched_m_str
            ).replace('  ', ' ')
        else:
            _sched_m_clean = _sched_m_str
        sched_scan = engine.scheduler.next_scan_ar()    if engine.scheduler else ""

        # ── قراءة Virtual Wallet الحقيقي من state_manager (Redis) ──────
        from core.virtual_wallet import VirtualWallet as _VW_stats
        from core.state_manager  import state_manager as _sm_stats
        user_id   = update.effective_user.id
        _vw_data  = _sm_stats.get_virtual_wallet(user_id) or {}
        _vw       = _VW_stats(_vw_data) if _vw_data else None

        # حساب القيم الحقيقية من virtual wallet
        _vw_balance    = _vw.balance   if _vw else portfolio_val
        _vw_invested   = _vw.invested  if _vw else 0.0
        # إصلاح #703: القيمة الكلية = balance + قيمة المراكز الحالية
        _positions_value = 0.0
        if _vw and _vw.positions:
            for sym, pos in _vw.positions.items():
                _positions_value += pos["cost"]  # نستخدم cost كتقدير
        _vw_total = (_vw.balance + _positions_value) if _vw else portfolio_val
        _vw_positions  = _vw.positions  if _vw else {}
        _vw_history    = _vw.history    if _vw else []
        _open_count    = len(_vw_positions)
        _sells         = [t for t in _vw_history if t.get("type") == "sell"]
        _wins          = [t for t in _sells if t.get("pnl", 0) > 0]
        _total_trades  = len(_sells)
        _win_rate      = (_wins.__len__() / max(_total_trades, 1) * 100) if _total_trades else 0
        _net_pnl       = sum(t.get("pnl", 0) for t in _sells)
        _avg_win       = (sum(t["pnl"] for t in _wins) / max(len(_wins), 1)) if _wins else 0
        _losses        = [t for t in _sells if t.get("pnl", 0) <= 0]
        _avg_loss      = (sum(t["pnl"] for t in _losses) / max(len(_losses), 1)) if _losses else 0
        # إصلاح #1083: _live_pnl يُعرَّف أولاً ثم يُحسب ثم يُستخدم في Drawdown
        _live_pnl = 0.0
        try:
            for sym, pos in _vw_positions.items():
                pd = await engine.data_layer.get_price(sym.replace("USDT",""))
                if pd and pd.get("price"):
                    cur = float(pd["price"])
                    _live_pnl += (cur - pos["avg_price"]) * pos["quantity"]
        except Exception:
            pass
        _today_pnl = _live_pnl

        # إصلاح #707: Drawdown يشمل PnL الحي (بعد حسابه)
        _current_total = _vw_total + _live_pnl if _vw else portfolio_val
        _drawdown_pct  = max(0, (portfolio_val - _current_total) / portfolio_val * 100)

        lines = [
            "📊 *إحصائيات رائد الفورية*",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "💰 *المحفظة الافتراضية*",
            f"• القيمة الكلية: ${_vw_total:,.2f}",
            f"• رصيد نقدي: ${_vw_balance:,.2f}",
            f"• مُستثمر: ${_vw_invested:,.2f}",
            f"• Drawdown: {_drawdown_pct:.1f}%",
            f"• PnL الحي (مراكز مفتوحة): ${_live_pnl:+,.2f}",
            f"• صفقات مفتوحة: {_open_count}",
            "",
            "📈 *الأداء الإجمالي (صفقات مغلقة)*",
            f"• إجمالي الصفقات المغلقة: {_total_trades}",
            f"• صافي الربح: ${_net_pnl:+,.2f}",
            f"• نسبة الفوز: {_win_rate:.1f}%",
            f"• متوسط الربح: ${_avg_win:,.2f}",
            f"• متوسط الخسارة: ${abs(_avg_loss):,.2f}",
            "",
            "🔬 *حالة النموذج*",
            # إصلاح #299 (H9): معدل الفوز من virtual_wallet (المصدر الحقيقي)
            f"• معدل فوز: {_win_rate:.1f}% ({len(_wins)}/{_total_trades} صفقة)",
            f"• الانحراف: {drift_st.drift_pct:.1f}%",
            f"• {drift_st.recommendation_ar}",
            "",
            "📅 *الأحداث*",
            f"• تعرض الأحداث: {ev_mult:.0%}" + (f" — {_clean(ev_r)}" if ev_r else ""),
            "",
            "⏰ *التقارير التلقائية*",
            f"• {_clean(sched_w)}",
            # إصلاح #301/#350 (I9): تنظيف "يوم X" والرقم الزائد في التاريخ الشهري
            f"• {_sched_m_clean}",
            "",
            kill_st,
            "",
            override_st,
            "",
            "📊 *أداء رائد vs BTC*",
        ]

        # مقارنة أداء رائد vs BTC — يستخدم virtual wallet الحقيقي
        try:
            btc_price_now = await engine.data_layer.get_price("BTC")
            if btc_price_now and btc_price_now.get("price", 0) > 0:
                btc_change   = btc_price_now.get("change_24h", 0)
                has_activity = _open_count > 0 or _total_trades > 0
                if has_activity:
                    raed_pnl_pct = ((_net_pnl + _live_pnl) / max(portfolio_val, 1)) * 100
                    lines += [
                        f"• رائد (كلي): {raed_pnl_pct:+.2f}%",
                        f"• BTC  (24h): {btc_change:+.2f}%",
                        f"• الفارق: {raed_pnl_pct - btc_change:+.2f}% {'✅ رائد أفضل' if raed_pnl_pct > btc_change else '📊 BTC أفضل'}",
                        f"• صفقات مفتوحة: {_open_count} | مغلقة: {_total_trades}",
                    ]
                else:
                    lines += [
                        f"• لا توجد صفقات بعد — انتظر المسح التالي",
                        f"• BTC (24h): {btc_change:+.2f}%",
                        f"• 💡 تأكد من /autotrade on",
                    ]
        except Exception:
            lines.append("• بيانات المقارنة غير متاحة")

        await _get_message_p(update, context).reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_stats: {e}")
        await _get_message_p(update, context).reply_text(f"❌ خطأ في الإحصائيات: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /approve و /reject
# ════════════════════════════════════════════════════════════════
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    args = context.args or []
    if not args:
        await _get_message_p(update, context).reply_text("⚠️ الاستخدام: /approve [رمز]"); return
    try:
        ok = await engine.human_override.approve(args[0], "user")
        await _get_message_p(update, context).reply_text(
            "✅ تمت الموافقة وجاري التنفيذ" if ok
            else "⚠️ رمز غير موجود أو انتهت صلاحيته")
    except Exception as e:
        logger.error(f"cmd_approve: {e}")
        await _get_message_p(update, context).reply_text("❌ خطأ في معالجة الموافقة")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message_p(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    args = context.args or []
    if not args:
        await _get_message_p(update, context).reply_text("⚠️ الاستخدام: /reject [رمز]"); return
    try:
        ok = await engine.human_override.reject(args[0], "user")
        await _get_message_p(update, context).reply_text(
            "🚫 تم الرفض" if ok
            else "⚠️ رمز غير موجود أو انتهت صلاحيته")
    except Exception as e:
        logger.error(f"cmd_reject: {e}")
        await _get_message_p(update, context).reply_text("❌ خطأ في معالجة الرفض")


def register(app):
    from telegram.ext import CallbackQueryHandler as _CQH
    # أوامر الخطط — entry point جديد (T3)
    app.add_handler(CommandHandler("planmonth",  cmd_planmonth_entry))
    app.add_handler(CommandHandler("planweek",   cmd_planweek_entry))
    # الأوامر الكاملة (تُستدعى من callbacks)
    app.add_handler(CommandHandler("planmonth_full", cmd_plan_month))
    app.add_handler(CommandHandler("planweek_full",  cmd_plan_week))
    app.add_handler(CommandHandler("portfolio",  cmd_portfolio))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))
    # Callbacks للخطط
    app.add_handler(_CQH(cb_plan_agree,       pattern=r"^plan_agree$"))
    app.add_handler(_CQH(cb_plan_comment,     pattern=r"^plan_comment$"))
    app.add_handler(_CQH(cb_plan_general,     pattern=r"^plan_(w|m)_general$"))
    app.add_handler(_CQH(cb_plan_custom,      pattern=r"^plan_(w|m)_custom$"))
    # plan_execute_v1: تنفيذ صفقة من الخطة
    app.add_handler(_CQH(callback_plan_exec,  pattern=r"^plan_exec:"))
    app.add_handler(_CQH(callback_planmkt,    pattern=r"^planmkt_"))
    # plan_asset: اختيار نوع الأصول
    app.add_handler(_CQH(callback_plan_asset, pattern=r"^plan_[wm]_asset:"))
    # إصلاح #780: MessageHandler لإدخال العملات
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_plan_symbols_input
    ), group=1)  # group=1 لأولوية أقل

# راسالة انتظار: 📋 الأصول في وضع المراقبة — لم تصل لشروط الدخول بعد


# ══ Callbacks: Plan feedback (T3+T5) ══════════════════════════════════════════

async def cb_plan_agree(update, context):
    """المستخدم يتفق مع الخطة."""
    query = update.callback_query
    await query.answer("✅ تم التسجيل")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "✅ ممتاز! خطتك مُسجَّلة.\n"
        "رائد سيُتابع معك التقدم في التقرير الأسبوعي.")


async def cb_plan_comment(update, context):
    """المستخدم لديه ملاحظة."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    # حفظ state لانتظار الملاحظة
    context.user_data["awaiting_plan_comment"] = True
    await query.message.reply_text(
        "💬 *شاركني ملاحظتك على الخطة*\n\n"
        "اكتب ملاحظتك أو رأيك، وسأأخذها بعين الاعتبار.\n"
        "_رائد سيتذكر رأيك ويستخدمه في التحليلات القادمة_",
        parse_mode="Markdown")


async def cb_plan_general(update, context):
    """تنفيذ خطة عامة — إصلاح #867: معالجة شاملة لـ msg."""
    query = update.callback_query
    try:
        await query.answer("⏳ جاري إعداد الخطة...")
    except Exception:
        pass
    plan_type = "week" if "plan_w" in query.data else "month"
    msg = None
    try:
        msg = await query.message.reply_text("⏳ جاري تحليل السوق وإعداد الخطة...")
    except Exception as _e:
        import logging
        logging.getLogger("plan").error(f"cb_plan_general reply_text: {_e}")
        return
    # تمرير msg بشكل آمن
    context.user_data["_plan_msg_override"] = msg
    try:
        if plan_type == "week":
            await cmd_plan_week(update, context)
        else:
            await cmd_plan_month(update, context)
    except Exception as e:
        import logging
        logging.getLogger("plan").error(f"cb_plan_general: {e}", exc_info=True)
        try:
            if msg:
                await msg.edit_text(f"❌ خطأ في إعداد الخطة: {str(e)[:100]}")
        except Exception:
            pass


async def cb_plan_custom(update, context):
    """طلب إدخال عملات محددة."""
    query = update.callback_query
    await query.answer()
    plan_type = "week" if "plan_w" in query.data else "month"
    context.user_data["awaiting_plan_symbols"] = plan_type
    await query.edit_message_text(
        "🎯 *أدخل رموز العملات*\n\n"
        "مثال: BTC ETH SOL XRP\n"
        "_(مفصولة بمسافة)_",
        parse_mode="Markdown")


async def handle_plan_symbols_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال رموز العملات أو ملاحظات المستخدم — إصلاح #780/#832."""
    # معالجة ملاحظات المستخدم على الخطة
    if context.user_data.get("awaiting_plan_comment"):
        context.user_data.pop("awaiting_plan_comment", None)
        comment = (_get_message_p(update, context).text or "").strip()
        if comment:
            from core.state_manager import state_manager as _sm_pc
            _sm_pc.save_user_comment(update.effective_user.id, {
                "text": comment, "type": "plan_comment"})
        await _get_message_p(update, context).reply_text(
            "✅ شكراً! تم حفظ ملاحظتك.\n"
            "رائد سيأخذها بعين الاعتبار في التحليلات القادمة 🎯")
        return

    plan_type = context.user_data.get("awaiting_plan_symbols")
    if not plan_type:
        return  # لا ننتظر إدخال عملات

    # استخراج الرموز
    text = (_get_message_p(update, context).text or "").strip().upper()
    symbols = [s.strip() for s in text.replace(',', ' ').split() if s.strip()]

    if not symbols:
        await _get_message_p(update, context).reply_text("⚠️ لم أتعرف على رموز عملات — جرّب: BTC ETH SOL")
        return

    # مسح انتظار الإدخال
    context.user_data.pop("awaiting_plan_symbols", None)

    # تمرير الرموز كـ args
    context.args = symbols
    if plan_type == "week":
        await cmd_plan_week(update, context)
    else:
        await cmd_plan_month(update, context)

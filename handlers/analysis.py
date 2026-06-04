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
from core.middleware import require_tier
from core.coins_list import is_symbol_allowed, get_tier_message
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from core.state_manager import state_manager as _sm
from core.middleware    import require_tier
from core.user_manager import user_manager as _um
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


def _rsi_label(rsi: float) -> str:
    """تسمية RSI بمناطق احترافية."""
    if rsi >= 70:   return f"🔴 ذروة شراء ({rsi:.0f})"
    elif rsi >= 60: return f"🟠 قريب ذروة شراء ({rsi:.0f})"
    elif rsi >= 45: return f"⚪ محايد ({rsi:.0f})"
    elif rsi >= 35: return f"🟡 قريب ذروة بيع ({rsi:.0f})"
    elif rsi >= 25: return f"🟠 ذروة بيع ({rsi:.0f})"
    else:           return f"🔴 ذروة بيع شديدة ({rsi:.0f})"


def _market_contradiction(rsi: float, fear_greed: int, regime_desc: str) -> str:
    """تحليل تناقض المؤشرات."""
    is_bearish = "هابط" in str(regime_desc)
    oversold   = rsi < 35
    high_fear  = fear_greed < 30

    if high_fear and oversold and is_bearish:
        return "⏳ ذعر + ذروة بيع + هابط = انتظر تأكيد قبل الدخول"
    if high_fear and oversold:
        return "🟢 نمط تجميع: خوف شديد + ذروة بيع = فرصة محتملة"
    if fear_greed > 70 and rsi > 65:
        return "🔴 طمع + ذروة شراء = خطر انعكاس"
    return ""


def _fmt_price(price: float) -> str:
    """تنسيق السعر حسب حجمه — يعرض الأرقام المهمة دائماً."""
    if price <= 0:      return "$0"
    elif price >= 1000: return f"${price:,.2f}"
    elif price >= 1:    return f"${price:,.4f}"
    elif price >= 0.001:return f"${price:.6f}"
    elif price >= 1e-6: return f"${price:.8f}"
    else:               return f"${price:.10f}"


def _calc_fibonacci(candles: list, lookback: int = 60) -> dict:
    """
    إصلاح #326: Fibonacci dynamic يضمن أن السعر بين swing_low و swing_high.
    يجرب نوافذ أصغر حتى يجد swing مناسباً.
    """
    if not candles or len(candles) < 20:
        return {}
    try:
        price_now = float(candles[-1].get("close", 0))
        swing_high = swing_low = 0

        # جرب نوافذ متصاعدة حتى يكون السعر بينهما
        for lb in [21, 30, 45, lookback, len(candles)]:
            recent = candles[-min(lb, len(candles)):]
            highs  = [float(c.get("high",  c.get("close", 0))) for c in recent]
            lows   = [float(c.get("low",   c.get("close", 0))) for c in recent]
            sh, sl = max(highs), min(lows)
            if sl < price_now < sh:
                swing_high, swing_low = sh, sl
                break

        # إذا لم نجد swing مناسباً — نبني حول السعر الحالي
        if swing_high == 0 or swing_low == 0 or swing_high <= swing_low:
            atr_est    = price_now * 0.05   # تقدير ATR 5%
            swing_high = price_now * 1.15
            swing_low  = price_now * 0.85

        diff = swing_high - swing_low
        if diff <= 0:
            return {}
        levels = {
            "0.0":   round(swing_low,  8),
            "0.236": round(swing_low  + diff * 0.236, 8),
            "0.382": round(swing_low  + diff * 0.382, 8),
            "0.500": round(swing_low  + diff * 0.500, 8),
            "0.618": round(swing_low  + diff * 0.618, 8),
            "0.786": round(swing_low  + diff * 0.786, 8),
            "1.0":   round(swing_high, 8),
            "1.272": round(swing_high + diff * 0.272, 8),
            "1.618": round(swing_high + diff * 0.618, 8),
        }
        price             = float(candles[-1].get("close", 0))
        below             = {k: v for k, v in levels.items() if v < price and float(k) <= 1.0}
        above             = {k: v for k, v in levels.items() if v > price and float(k) <= 1.0}
        nearest_support   = max(below.values()) if below else swing_low
        nearest_resistance= min(above.values()) if above else swing_high
        return {
            "levels": levels,
            "swing_high": swing_high, "swing_low": swing_low,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "price": price,
        }
    except Exception:
        return {}


def _fmt_fib_lines(fib: dict, price: float) -> list:
    """يُعيد قائمة سطور Fibonacci للدمج مع التقرير."""
    if not fib or not fib.get("levels"):
        return []
    lines = ["", "📐 *مستويات Fibonacci*"]
    ordered = sorted(
        [(float(k), v, k) for k, v in fib["levels"].items() if float(k) <= 1.0],
        reverse=True
    )
    # إيجاد المستوى الأقرب فقط — مستوى واحد
    closest_lbl = None
    closest_dst = float("inf")
    for _, val, label in ordered:
        d = abs(val - price) / max(price, 0.0001) * 100
        if d < closest_dst:
            closest_dst = d
            closest_lbl = label

    for _, val, label in ordered:
        icon    = "🟢" if val < price else "🔴"
        # "أنت هنا" فقط للمستوى الأقرب وبشرط < 2%
        is_here = (label == closest_lbl and closest_dst < 2.0)
        mark    = " ◀ أنت هنا" if is_here else ""
        lines.append(f"  {icon} {label:>5} — {_fmt_price(val)}{mark}")
    ns = fib.get("nearest_support", 0)
    nr = fib.get("nearest_resistance", 0)
    if ns > 0: lines.append(f"• أقرب دعم فيبو:    {_fmt_price(ns)}")
    if nr > 0: lines.append(f"• أقرب مقاومة فيبو: {_fmt_price(nr)}")
    return lines


def _calc_adx(candles: list, period: int = 14) -> float:
    """
    حساب ADX — إصلاح #243.
    Simple average يُعطي 87-94 بسبب H/L المحسوبة من CoinGecko.
    الحل: كشف artifact + ATR-based trend strength بديل.
    """
    if len(candles) < period + 2:
        return 0.0
    try:
        highs  = [float(c.get("high",  0)) for c in candles]
        lows   = [float(c.get("low",   0)) for c in candles]
        closes = [float(c.get("close", 0)) for c in candles]
        pdms, ndms, trs = [], [], []
        for i in range(1, len(candles)):
            h, l, pc = highs[i], lows[i], closes[i-1]
            h_diff = h - highs[i-1]
            l_diff = lows[i-1] - l
            pdms.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
            ndms.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))

        # كشف artifact: H/L محسوبة تُعطي +DM أو -DM = 0 دائماً
        total      = len(pdms) or 1
        pdm_nz     = sum(1 for x in pdms if x > 0)
        ndm_nz     = sum(1 for x in ndms if x > 0)
        if pdm_nz / total < 0.05 or ndm_nz / total < 0.05:
            # بيانات أحادية — trend strength من EMA
            p      = closes[-1] if closes[-1] > 0 else 1
            avg_tr = sum(trs[-period:]) / min(period, len(trs)) if trs else 0
            atr_p  = avg_tr / p * 100
            ema_f  = sum(closes[-period:])      / period if len(closes) >= period    else closes[-1]
            ema_s  = sum(closes[-period*2:])    / (period*2) if len(closes) >= period*2 else ema_f
            trend_s = abs(ema_f - ema_s) / max(ema_s, 1) * 100
            return round(min(15 + trend_s * 8 + atr_p * 1.5, 65.0), 1)

        # Wilder's smoothing الصحيح
        def _wilder(data):
            val = sum(data[:period])
            res = [val]
            for v in data[period:]:
                val = val - val / period + v
                res.append(val)
            return res

        atr_s = _wilder(trs);  pdm_s = _wilder(pdms);  ndm_s = _wilder(ndms)
        dx_list = []
        for i in range(len(atr_s)):
            av = atr_s[i]
            if av <= 0: continue
            di_p = pdm_s[i] / av * 100
            di_m = ndm_s[i] / av * 100
            d    = di_p + di_m
            if d > 0:
                dx_list.append(abs(di_p - di_m) / d * 100)
        if not dx_list:
            return 0.0
        adx = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx = (adx * (period - 1) + dx) / period
        return round(min(adx, 75.0), 1)
    except Exception:
        return 0.0


def _build_professional_block(
    symbol: str, price: float, signal, regime,
    candles: list, rsi: float, atr_pct: float, fib: dict
) -> str:
    """
    بناء بلوك الإشارة الاحترافية — ملاحظة #33
    يُعرض في /signal و /analyze فقط
    """
    conf      = signal.confidence
    direction = signal.direction
    tech      = getattr(signal, "technicals", {}) or {}
    adx       = _calc_adx(candles) or float(tech.get("adx", 0) or 0)
    macd_hist = float(tech.get("macd_hist", 0) or 0)
    is_bear   = "هابط" in regime.description_ar
    closes    = [float(c.get("close", 0)) for c in candles if c.get("close")]
    ema50_val = sum(closes[-50:]) / 50 if len(closes) >= 50 else price * 1.05
    atr_dec   = atr_pct / 100

    # القرار
    if conf >= 0.65 and direction == "long":
        decision = "✅ BUY — شراء"
    elif conf >= 0.65 and direction == "short":
        decision = "✅ SHORT — بيع"
    else:
        decision = "⚪ ANTICIPATE — انتظار"

    # الأسباب
    reasons = []
    if conf < 0.65:
        reasons.append(f"• الثقة {conf:.0%} أقل من الحد 65%")
    # إصلاح #86/#130: ADX فقط عند الخطورة القصوى
    if adx > 45:
        reasons.append(f"• ADX = {adx:.0f} → اتجاه قوي جداً — خطر الدخول مرتفع")
    if macd_hist < 0:
        reasons.append("• MACD سالب (sellers مسيطرون)")
    elif macd_hist > 0:
        reasons.append("• MACD موجب (زخم شراء)")
    if rsi < 30:
        reasons.append(f"• RSI = {rsi:.0f} → ذروة بيع (فرصة انتعاش)")
    elif rsi > 70:
        reasons.append(f"• RSI = {rsi:.0f} → ذروة شراء (خطر تصحيح)")
    else:
        reasons.append(f"• RSI = {rsi:.0f}")
    ns = fib.get("nearest_support", 0)
    nr = fib.get("nearest_resistance", 0)
    if ns > 0:
        dist = abs(price - ns) / max(price, 0.0001) * 100
        reasons.append(f"• الدعم {_fmt_price(ns)} على بُعد {dist:.1f}%")

    # شروط الدخول (للانتظار فقط)
    entry_conds = []
    if conf < 0.65 or direction == "neutral":
        # M#117: شرط RSI بناءً على القيمة الحالية
        if rsi < 40:
            _rsi_cond = f"1. RSI يتجاوز 40 صعوداً + إغلاق فوق {_fmt_price(ema50_val)}"
        elif rsi < 55:
            _rsi_cond = f"1. RSI يتجاوز 55 + إغلاق فوق {_fmt_price(ema50_val)}"
        else:
            _rsi_cond = f"1. انتظر تصحيح RSI تحت 60 ثم ارتداد"
        # إصلاح #378: شرط دخول يستخدم مقاومة فيبو القريبة بدلاً من EMA50 البعيد
        _fib_res    = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _target_lbl = (f"مقاومة فيبو ({_fmt_price(_fib_res)})"
                       if _fib_res and _fib_res > price
                       else f"EMA50 ({_fmt_price(ema50_val)})")
        entry_conds = [
            _rsi_cond,
            "2. الثقة الإجمالية ≥ 65%",
            f"3. إغلاق فوق {_target_lbl}",
        ]
        if ns > 0:
            entry_conds.append(f"4. وصول Demand Zone {_fmt_price(ns)}")
        if adx > 40:
            entry_conds.append("5. RSI يتجاوز 30 صعوداً (تأكيد انتهاء الهبوط)")

    # إصلاح #325: R/R ديناميكي — ذروة البيع تُعطي هدفاً أوسع
    # منطق مالي: RSI=13 تاريخياً يسبق ارتداداً 10-20%
    # فالهدف 4×ATR منطقي (ليس 1.8×ATR كالمعتاد)
    if direction == "short" and conf >= 0.65:
        pro_entry = price * (1 + atr_dec * 0.2)
        pro_tp    = price * (1 - atr_dec * 2.0)
        pro_sl    = price * (1 + atr_dec * 1.2)
        pro_dir   = "Short"
    else:
        pro_entry = ns if ns > 0 and ns < price * 0.99 else price * (1 - atr_dec * 0.4)
        # ضبط الهدف حسب RSI
        if rsi <= 15:
            _tp_mult, _sl_mult = 4.0, 0.8   # قاع شديد → هدف كبير، وقف ضيق
        elif rsi <= 25:
            _tp_mult, _sl_mult = 3.0, 1.0
        elif rsi <= 35:
            _tp_mult, _sl_mult = 2.5, 1.0
        else:
            _tp_mult, _sl_mult = 1.8, 1.2   # افتراضي
        pro_tp  = price * (1 + atr_dec * _tp_mult)
        pro_sl  = pro_entry * (1 - atr_dec * _sl_mult)
        pro_dir = "Long"

    rr = abs(pro_tp - pro_entry) / max(abs(pro_sl - pro_entry), 0.0001)
    sl_pct  = abs(pro_sl - pro_entry) / max(pro_entry, 0.0001) * 100
    tp_pct  = abs(pro_tp - pro_entry) / max(pro_entry, 0.0001) * 100
    hold    = 3 if adx > 40 else 5

    parts = [f"*{decision}*", ""]
    if reasons:
        parts.append("*✅ الأسباب:*")
        parts.extend(reasons)
        parts.append("")
    if entry_conds:
        parts.append("*⏳ متى تدخل؟*")
        parts.extend(entry_conds)
        parts.append("")
    # حساب نسبة الحجم ديناميكياً بناءً على الظروف
    if conf >= 0.65 and not is_bear:
        vol_pct = 50
        vol_reason = "ثقة جيدة + سوق محايد/صاعد"
    elif conf >= 0.65 and is_bear:
        vol_pct = 35
        vol_reason = "ثقة جيدة لكن سوق هابط"
    elif conf >= 0.50:
        vol_pct = 25
        vol_reason = "ثقة متوسطة — تقليل للحماية"
    else:
        vol_pct = 15
        vol_reason = "ثقة منخفضة — حد أدنى"

    # حجم فعلي من المحفظة الافتراضية
    portfolio_est = 10000  # سيُحسب من المحفظة الحقيقية

    parts.extend([
        "*🛡️ خيار المحترف:*",
        f"• الحجم المقترح: {vol_pct}% من رأس المال",
        f"  📌 لماذا {vol_pct}%؟ — {vol_reason}",
        f"• {pro_dir} Limit @ {_fmt_price(pro_entry)}",
        f"• وقف: {_fmt_price(pro_sl)} ({sl_pct:.1f}%-)",
        f"• هدف: {_fmt_price(pro_tp)} (+{tp_pct:.1f}%)",
        f"• R/R: 1:{rr:.1f}",
        f"• أقصى مدة: {hold} أيام",
    ])
    return "\n".join(parts)

DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context):
    return context.bot_data.get("raed_engine")



def _strip_header_duplicates(text: str) -> str:
    """يحذف السطور المكررة (السعر/RSI/السوق) من نص التحليل."""
    if not text: return text
    lines = text.strip().split("\n")
    skip_keys = ("السعر:", "سعر:", "rsi", "fear", "السوق:", "الاتجاه:", "📊 تحليل", "💰 ال", "🌍")
    clean, skip_count = [], 0
    for line in lines:
        if skip_count < 8 and any(k in line.strip().lower() for k in skip_keys):
            skip_count += 1
            continue
        clean.append(line)
    result = "\n".join(clean).strip()
    return result if len(result) > 30 else text

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
        "⏳ قد يستغرق 10-20 ثانية — يُرجى الانتظار"
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
        data, fear, funding, oi, whale = await asyncio.gather(
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_funding_rate("BTC"),
            engine.data_layer.get_open_interest("BTC"),
            engine.data_layer.get_whale_ratio("BTC"),
            return_exceptions=True
        )
        funding = funding if isinstance(funding, dict) else {}
        oi      = oi      if isinstance(oi,      dict) else {}
        whale   = whale   if isinstance(whale,   dict) else {}
        data  = data  if isinstance(data, dict) else {"tvl": 0, "protocols": []}
        fear  = fear  if isinstance(fear, dict) else {"value": 50, "label_ar": "محايد"}
        top_p = (data.get("protocols") or [])[:5]
        tvl        = float(data.get("tvl") or 0)
        tvl_change = float(data.get("tvl_change_1d") or 0)
        fear_val   = int(fear.get("value") or 50)

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
            f"📈 تغيير TVL 24h: {tvl_change:+.2f}%",
        ]

        # بيانات شبكة Bitcoin
        btc_hashrate = data.get("btc_hashrate", 0)
        btc_tx       = data.get("btc_tx_count_24h", 0)
        if btc_hashrate > 0 or btc_tx > 0:
            lines += ["", "⛏️ *شبكة Bitcoin*"]
            if btc_hashrate > 0:
                lines.append(f"• Hashrate: {btc_hashrate/1e9:.1f} EH/s")
            if btc_tx > 0:
                lines.append(f"• معاملات 24h: {btc_tx:,}")

        if top_p:
            lines += ["", "🏆 *أكبر البروتوكولات*"]
            for i, p in enumerate(top_p, 1):
                tvl_b = float(p.get("tvl") or 0) / 1e9
                name  = str(p.get("name", "")).replace("_", " ")
                lines.append(f"{i}. {name} — ${tvl_b:.2f}B")
        else:
            lines += ["", "⚠️ بيانات البروتوكولات غير متاحة حالياً — يُرجى المحاولة لاحقاً"]

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
        candles, fear, btc_dom = await asyncio.gather(
            engine.data_layer.get_ohlcv(symbol, "1d", 250),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_btc_dominance(),
            return_exceptions=True
        )
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}

        if len(candles) < 30:
            await msg.edit_text(
                f"⚠️ بيانات {symbol} غير كافية حالياً\n"
                f"أعد المحاولة بعد دقيقة")
            return

        result = engine.regime_detector.detect(
            candles, btc_dominance=btc_dom,
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
    engine  = _eng(context)
    user_id = update.effective_user.id if update.effective_user else 0
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()

    # فحص الباقة
    user_id2 = update.effective_user.id
    tier2    = _sm.get_tier(user_id2)
    if not is_symbol_allowed(symbol, tier2):
        await update.message.reply_text(
            (
                f"⛔ *{symbol}* غير متاحة لباقتك الحالية\n\n"
                f"باقتك: {_sm.get_tier_name(user_id)}\n"
                f"هذه العملة تتطلب باقة أعلى\n\n"
                f"⬆️ للترقية: /upgrade\n"
                f"📋 لعرض عملاتك المتاحة: /premium"
            ), parse_mode="Markdown"); return

    msg = await update.message.reply_text(
        f"📡 جاري تحليل {symbol} عبر 5 مصادر...\n"
        "⏳ قد يستغرق 20-30 ثانية — يُرجى الانتظار"
    )

    try:
        candles, onchain, fear, news_raw, btc_dom = await asyncio.gather(
            engine.data_layer.get_ohlcv(symbol, "1d", 365),
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_news(currencies=symbol),
            engine.data_layer.get_btc_dominance(),
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
        regime   = engine.regime_detector.detect(candles, btc_dominance=btc_dom, fear_greed=fear_val)

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

        # Fibonacci + Professional Block
        fib        = _calc_fibonacci(candles)
        pro_block  = _build_professional_block(
            symbol, price, signal, regime, candles, rsi, atr_pct, fib)
        fib_lines  = _fmt_fib_lines(fib, price)

        # حذف تقييم المخاطر عند وجود Professional Block (M#51)
        _risk_text = _clean_md(engine.risk_engine.format_assessment_ar(risk, symbol))
        # إظهار تقييم المخاطر فقط عند الموافقة (لا عند الرفض مع وجود pro block)
        show_risk  = risk.decision.value == "approve" or not pro_block
        parts = [
            _clean_md(engine.signal_layer.format_ar(signal)),
            _clean_md(engine.regime_detector.format_ar(regime)),
            _clean_md(engine.strategy_router.format_ar(strategy, params)),
        ]
        if show_risk:
            parts.append(_risk_text)
        parts.append(_clean_md(pro_block))
        if fib_lines:
            parts.append(_clean_md("\n".join(fib_lines)))
        # إشارة Futures إذا مؤهل (ذهبي+ ولديه ربط فعال)
        try:
            user_id_sig = user_id  # M#69: user_id معرَّف مسبقاً
            tt_sig      = getattr(signal, "trade_type", "spot")
            tier_sig    = _sm.get_tier(user_id_sig)
            if tt_sig in ("futures_long", "futures_short") and tier_sig in ("gold","diamond","admin"):
                fut_atr  = _calc_atr(candles) / 100 if candles else 0.03
                fut_dir  = "long" if tt_sig == "futures_long" else "short"
                if fut_dir == "long":
                    fut_tp = price * (1 + fut_atr * 2)
                    fut_sl = price * (1 - fut_atr * 1.2)
                else:
                    fut_tp = price * (1 - fut_atr * 2)
                    fut_sl = price * (1 + fut_atr * 1.2)
                fut_txt = engine.risk_engine.format_futures_signal_ar(
                    symbol, fut_dir, price, fut_tp, fut_sl, leverage=1)
                parts.append(_clean_md(fut_txt))
        except Exception as _fe:
            logger.debug(f"futures display: {_fe}")

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
    valid    = ["trend_following", "mean_reversion", "breakout", "hybrid"]

    if strategy not in valid:
        await update.message.reply_text(
            "⚠️ الاستراتيجيات المتاحة:\n"
            "• trend_following (الاتجاه — افتراضي)\n"
            "• mean_reversion (الارتداد)\n"
            "• breakout (الاختراق)\n"
            "• hybrid (مدمج EMA+RSI)\n\n"
            "مثال: /backtest BTC trend_following")
        return

    strategy_ar = {
        "trend_following": "اتباع الاتجاه",
        "mean_reversion":  "الارتداد للمتوسط",
        "breakout":        "الاختراق",
        "hybrid":          "مدمج EMA+RSI",
    }
    msg = await update.message.reply_text(
        f"⏳ جاري Backtest لـ {symbol} — {strategy_ar[strategy]}\n"
        "🔬 3 سنوات بيانات حقيقية — قد يستغرق 30-60 ثانية"
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

        import re as _re_bt
        def _fix_md_stars(t):
            lines = t.split("\n")
            return "\n".join(
                ln.replace("*","") if ln.count("*") % 2 != 0 else ln
                for ln in lines
            )
        text = _fix_md_stars(_clean_md(engine.backtest_engine.format_ar(result)))
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
        import re as _re
        text_ev = _re.sub(r'(بعد\s*)(\d+)(ساعة)', r'بعد \2 ساعة', text_ev)
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
    symbol = args[0].upper()

    # فحص الباقة للعملة المطلوبة
    tier_an = _sm.get_tier(user_id)
    if not is_symbol_allowed(symbol, tier_an):
        await update.message.reply_text(
            (
                f"⛔ *{symbol}* غير متاحة لباقتك الحالية\n\n"
                f"باقتك: {_sm.get_tier_name(user_id)}\n"
                f"هذه العملة تتطلب باقة أعلى\n\n"
                f"⬆️ للترقية: /upgrade\n"
                f"📋 لعرض عملاتك المتاحة: /premium"
            ), parse_mode="Markdown"); return
    if not symbol:
        await update.message.reply_text(
            "📊 مثال الاستخدام: /analyze BTC\n"
            "أو: /analyze ETH"
        )
        return

    msg = await update.message.reply_text(f"🧠 جاري التحليل العميق لـ {symbol}...\n⏳ قد يستغرق 1-3 دقائق — يُرجى الانتظار")

    # إصلاح #178: semaphore يمنع تزامن أكثر من 3 أوامر ثقيلة
    _heavy_sem = await engine.acquire_heavy()
    await _heavy_sem.acquire()
    try:
        # M#119: timeout صارم لمنع التجمد
        try:
            price_d, candles, fear, btc_dom = await asyncio.wait_for(
                asyncio.gather(
                    engine.data_layer.get_price(symbol),
                    engine.data_layer.get_ohlcv(symbol, "1d", 250),
                    engine.data_layer.get_fear_greed(),
                    engine.data_layer.get_btc_dominance(),
                    return_exceptions=True
                ), timeout=45.0
            )
        except asyncio.TimeoutError:
            await msg.edit_text(
                f"⏱️ انتهت مهلة تحليل *{symbol}*\n\n"
                f"جرّب: /quicksignal {symbol}",
                parse_mode="Markdown")
            return
        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0

        # retry للعملات الصغيرة خارج top100
        if len(candles) < 10:
            logger.info(f"analyze: retry OHLCV for {symbol}")
            await asyncio.sleep(1)
            retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 100)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or
                           price_d.get("price_change_percentage_24h") or 0)
        volume_24h = float(price_d.get("volume_24h") or 0)
        market_cap = float(price_d.get("market_cap") or 0)

        if price <= 0:
            # M#113: بحث تلقائي في CoinGecko
            try:
                new_id = await engine.data_layer._search_coingecko(symbol)
                if new_id:
                    price_d2 = await engine.data_layer.get_price(symbol)
                    price = float((price_d2 or {}).get("price") or 0)
            except Exception:
                pass
            if price <= 0:
                await msg.edit_text(
                    f"⚠️ لم أجد سعراً لـ *{symbol}*\n\n"
                    f"• تأكد من صحة الرمز (مثال: BTC, ETH, SOL)\n"
                    f"• العملة قد تكون جديدة أو غير مدعومة\n"
                    f"• جرّب: /chart {symbol} للتحليل البصري",
                    parse_mode="Markdown")
                return

        rsi = _calc_rsi(candles)

        # حساب regime + EMA (مصدر واحد للحقيقة)
        regime_desc = "⚪ جاري تحديث بيانات السوق"
        is_bearish  = False
        ema_bearish = False
        if len(candles) >= 10:  # خُفِّض: 30 → 10
            try:
                regime_obj  = engine.regime_detector.detect(
                    candles, btc_dominance=btc_dom, fear_greed=fear_val)
                regime_desc = regime_obj.description_ar
                is_bearish  = "هابط" in regime_desc
            except Exception as e:
                logger.warning(f"regime detect: {e}")

        # EMA check
        if len(candles) >= 20:
            try:
                closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
                ema20  = sum(closes[-20:]) / 20
                ema50  = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
                ema_bearish = price < ema20 and price < ema50
            except Exception:
                pass

        # اتجاه العملة
        trend = ("هابط" if is_bearish or ema_bearish
                 else "صاعد" if not is_bearish and not ema_bearish and rsi > 50
                 else "محايد")

        # candles_summary كـ JSON احترافي لـ Groq
        candles_summary = engine.data_layer.build_candles_summary(candles, symbol)
        if not candles_summary:
            # fallback نصي
            try:
                p5 = [float(c.get("close", 0) or 0) for c in candles[-5:] if c]
                p5 = [p for p in p5 if p > 0]
            except Exception:
                p5 = []
            if len(p5) >= 2 and p5[-1] > p5[0]:
                candles_summary = "آخر 5 شموع: اتجاه صاعد"
            elif len(p5) >= 2:
                candles_summary = "آخر 5 شموع: اتجاه هابط"
            else:
                candles_summary = "بيانات الشموع غير كافية"

        try:
            analysis = await engine.news_engine.analyze_symbol(
                symbol=symbol, price=price, price_change_24h=change_24h,
                volume_24h=volume_24h, market_cap=market_cap, rsi=rsi,
                fear_greed=fear_val, regime_desc=regime_desc,
                candles_summary=candles_summary)
            if not analysis or len(analysis.strip()) < 20:
                raise ValueError("تحليل فارغ")
        except Exception as _ae:
            logger.error(f"analyze_symbol ({symbol}): {_ae}")
            analysis = (f"📊 تحليل {symbol}\n"
                       f"السعر: {_fmt_price(price)} ({change_24h:+.2f}%)\n"
                       f"RSI: {rsi:.0f} | السوق: {regime_desc}")

        change_sign = "+" if change_24h >= 0 else ""
        # حساب مستويات دخول/خروج من ATR
        atr_pct = _calc_atr(candles) / 100 if candles else 0.03
        rsi_lbl = _rsi_label(rsi)
        contradiction = _market_contradiction(rsi, fear_val, regime_desc)

        # مستويات ذكية بناءً على ATR واتجاه السوق
        if rsi < 40 and not ("هابط" in regime_desc and rsi > 30):
            entry = price * (1 - atr_pct * 0.3)
            tp1   = price * (1 + atr_pct * 1.5)
            tp2   = price * (1 + atr_pct * 2.5)
            sl    = price * (1 - atr_pct * 1.2)
            levels_lines = [
                "",
                "📍 *مناطق الدخول والخروج*",
                f"• دخول مقترح: {_fmt_price(entry)}",
                f"• هدف 1: {_fmt_price(tp1)} ({atr_pct*150:.1f}%+)",
                f"• هدف 2: {_fmt_price(tp2)} ({atr_pct*250:.1f}%+)",
                f"• وقف الخسارة: {_fmt_price(sl)} ({atr_pct*120:.1f}%-)",
            ]
        elif rsi > 65:
            levels_lines = [
                "",
                "⚠️ *تحذير ذروة شراء*",
                f"• RSI={rsi:.0f} — خطر انعكاس",
                f"• انتظر تراجعاً إلى: {_fmt_price(price * (1 - atr_pct))}",
            ]
        else:
            levels_lines = []

        # إصلاح #375/#390: استخدام signal_layer.generate الحقيقي
        fib_a  = _calc_fibonacci(candles)
        _atr_a = _calc_atr(candles)
        try:
            _sig_a = engine.signal_layer.generate(
                symbol=symbol, candles=candles,
                onchain_data={},
                news_sentiment=float(getattr(engine, "_last_news_sentiment", 0) or 0),
                backtest_win_rate=0.55,
                macro_data={"fear_greed": fear_val},
                regime=engine.regime_detector.detect(
                    candles, btc_dominance=float(btc_dom or 50), fear_greed=fear_val),
            )
        except Exception as _se:
            logger.debug(f"signal_layer in analyze: {_se}")
            # fallback بسيط فقط عند الفشل
            class _AnalyzeSignal:
                confidence = 0.55
                direction  = "neutral"
                technicals = {}
                trade_type = "spot"
            _sig_a = _AnalyzeSignal()
            if rsi < 20:
                _sig_a.direction   = "long"
                _sig_a.confidence  = 0.70
            elif rsi < 35:
                _sig_a.direction   = "long"
                _sig_a.confidence  = 0.60
            elif rsi > 70:
                _sig_a.direction   = "short"
                _sig_a.confidence  = 0.60
        class _AnalyzeRegime:
            description_ar = regime_desc
            class regime:
                value = "bear_trend" if is_bearish else "bull_trend"
        _reg_a = _AnalyzeRegime()
        pro_block_a = _build_professional_block(
            symbol, price, _sig_a, _reg_a, candles, rsi, _atr_a, fib_a)
        fib_lines_a = _fmt_fib_lines(fib_a, price)

        parts = [
            f"🧠 *تحليل {symbol} — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: {_fmt_price(price)} ({change_sign}{change_24h:.2f}%)",
            f"📊 RSI: {rsi_lbl} | Fear & Greed: {fear_val}",
            f"🌍 السوق: {regime_desc}",
            f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'} | حجم: {volume_24h/1e6:.1f}M$" if volume_24h > 0 else f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'}",
            "━━━━━━━━━━━━━━━━━━",
            analysis,
        ]
        if levels_lines:
            parts.extend(levels_lines)
        # إصلاح #140: لا تكرار contradiction
        if contradiction and contradiction not in analysis:
            parts += ["", contradiction]
        # إضافة Fibonacci
        if fib_lines_a:
            parts.extend(fib_lines_a)
        # إضافة Professional Block — مع إزالة التكرار
        parts += ["", "━━━━━━━━━━━━━━━━━━"]
        # تنظيف pro_block من أي سطور تكرر الهيدر
        pro_clean = _clean_md(pro_block_a)
        parts.append(pro_clean)
        parts += ["", "⚠️ هذا التحليل استرشادي — القرار للمستخدم"]
        full = _clean_md("\n".join(parts))

        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await update.message.reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_analyze: {e}", exc_info=True)
        try:
            await msg.edit_text(
                f"⚠️ تعذّر التحليل العميق مؤقتاً\n\n"
                "🔄 بدائل متاحة:\n"
                "• /quicksignal — تحليل سريع\n"
                "• /signal — إشارة + مستويات دخول"
            )
        except Exception:
            pass
    finally:
        # إصلاح #178: تحرير semaphore دائماً
        try:
            _heavy_sem.release()
        except Exception:
            pass


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
        "⚙️ *تحليل الشارت البصري — قيد الصيانة*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🔄 *بدائل متاحة الآن:*\n"
        "• /analyze — تحليل عميق شامل (ذهبي+)\n"
        "• /signal  — إشارة + مستويات دخول\n"
        "• /quicksignal — تحليل سريع مجاني\n\n"
        "⏳ سيعود تحليل الشارت البصري قريباً",
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
        # إضافة header بمعلومات العملة (M#54)
        header_lines = [f"📊 *تحليل الشارت البصري{sym_label}*", "━━━━━━━━━━━━━━━━━━"]
        if symbol:
            try:
                eng3 = context.bot_data.get("raed_engine")
                if eng3:
                    pd3 = await eng3.data_layer.get_price(symbol)
                    if pd3 and pd3.get("price", 0) > 0:
                        p3 = pd3["price"]
                        c3 = pd3.get("change_24h", 0)
                        header_lines += [
                            f"💰 السعر: {_fmt_price(p3)} ({c3:+.2f}%)",
                            f"⏱️ الإطار الزمني: يومي (1D)",
                            "━━━━━━━━━━━━━━━━━━",
                        ]
            except Exception:
                pass
        full = _clean_md(
            "\n".join(header_lines) + "\n\n" +
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
        await msg.edit_text(
            "⚙️ *تحليل الشارت البصري — قيد الصيانة*\n\n"
            "🔄 *بدائل متاحة الآن:*\n"
            "• /analyze — تحليل عميق شامل\n"
            "• /signal  — إشارة + مستويات دخول\n"
            "• /quicksignal — تحليل سريع",
            parse_mode=ParseMode.MARKDOWN
        )


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
        # M#119: timeout صارم لمنع التجمد
        try:
            price_d, candles, fear, btc_dom = await asyncio.wait_for(
                asyncio.gather(
                    engine.data_layer.get_price(symbol),
                    engine.data_layer.get_ohlcv(symbol, "1d", 250),
                    engine.data_layer.get_fear_greed(),
                    engine.data_layer.get_btc_dominance(),
                    return_exceptions=True
                ), timeout=45.0
            )
        except asyncio.TimeoutError:
            await msg.edit_text(
                f"⏱️ انتهت مهلة تحليل *{symbol}*\n\n"
                f"جرّب: /quicksignal {symbol}",
                parse_mode="Markdown")
            return

        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0

        # retry للعملات الصغيرة خارج top100
        if len(candles) < 10:
            logger.info(f"analyze: retry OHLCV for {symbol}")
            await asyncio.sleep(1)
            retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 100)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or price_d.get("price_change_percentage_24h") or 0)

        if price <= 0:
            await msg.edit_text(
                f"❌ لم أجد سعراً لـ {symbol}.\n"
                f"تحقق من الرمز وأعد المحاولة")
            return

        rsi = _calc_rsi(candles)

        # حساب regime أولاً — يؤثر على التوصية
        regime_desc = "⚪ جاري تحديث بيانات السوق"
        regime_obj  = None
        is_bearish  = False
        ema_bearish = False   # السعر تحت EMA الرئيسية

        if len(candles) >= 10:  # خُفِّض: 30 → 10
            try:
                regime_obj  = engine.regime_detector.detect(
                    candles, btc_dominance=btc_dom, fear_greed=fear_val)
                regime_desc = regime_obj.description_ar
                is_bearish  = "هابط" in regime_desc
            except Exception as e:
                logger.warning(f"regime detect: {e}")

        # فحص EMA — إذا السعر تحت EMA20 → إشارة هبوطية
        if len(candles) >= 20:
            try:
                closes  = [float(c.get("close", 0)) for c in candles if c.get("close")]
                ema5    = sum(closes[-5:])  / 5  if len(closes) >= 5  else closes[-1]
                ema10   = sum(closes[-10:]) / 10 if len(closes) >= 10 else closes[-1]
                ema20   = sum(closes[-20:]) / 20
                ema50   = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
                # هابط حقيقي: السعر تحت معظم EMAs
                ema_below_count = sum([price < ema5, price < ema10, price < ema20, price < ema50])
                ema_bearish = ema_below_count >= 3  # تحت 3 من 4 EMAs = هابط
            except Exception:
                pass

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

        # توصية شاملة: RSI + Fear + Regime + EMA
        # مبدأ الحذر: إذا regime غير محدد → لا توصية شراء
        regime_unknown = regime_desc in ("⚪ جاري تحديث بيانات السوق", "", None)

        if rsi < 30 and fear_val < 40 and not ema_bearish and not is_bearish and not regime_unknown:
            # ذروة بيع + خوف + EMA صاعد + سوق غير هابط → شراء محتمل
            direction = "🟢 شراء محتمل"
            entry = price * 0.99; tp1 = price * 1.05; tp2 = price * 1.10; sl = price * 0.95
        elif rsi < 30 and (is_bearish or ema_bearish or regime_unknown):
            # ذروة بيع لكن في سوق هابط أو غير محدد → انتظار ارتداد فقط
            direction = "⏳ انتظار ارتداد"
            entry = price * 0.98; tp1 = price * 1.03; tp2 = price * 1.06; sl = price * 0.95
        elif rsi > 70 and fear_val > 60:
            direction = "🔴 بيع محتمل"
            entry = price * 1.01; tp1 = price * 0.95; tp2 = price * 0.90; sl = price * 1.05
        elif rsi > 70 and ema_bearish:
            # ذروة شراء + سوق هابط → بيع قوي
            direction = "🔴 بيع قوي"
            entry = price * 1.005; tp1 = price * 0.94; tp2 = price * 0.88; sl = price * 1.04
        elif 30 <= rsi <= 45 and fear_val < 50 and not is_bearish:
            direction = "🟡 شراء محتاط"
            entry = price * 0.99; tp1 = price * 1.04; tp2 = price * 1.08; sl = price * 0.96
        elif 55 <= rsi <= 70 and fear_val > 50:
            direction = "🟠 بيع محتاط"
            entry = price * 1.01; tp1 = price * 0.96; tp2 = price * 0.92; sl = price * 1.04
        else:
            direction = "⚪ انتظار"
            entry = price * 0.985; tp1 = price * 1.05; tp2 = price * 1.08; sl = price * 0.96  # M#109

        # regime_desc محسوبة أعلاه

        # تحذيرات متعددة
        bear_buy_warning = ""
        if direction.startswith("🟢") and regime_desc and "هابط" in regime_desc:
            bear_buy_warning = "\n⚠️ *تنبيه:* إشارة شراء في سوق هابط — تحقق من تأكيد الاتجاه قبل الدخول"
        if abs(change_24h) > 15:
            sign_ar = "ارتفاع" if change_24h > 0 else "انخفاض"
            bear_buy_warning += f"\n🚨 *تحذير:* {sign_ar} حاد {abs(change_24h):.1f}% في 24 ساعة — خطر الدخول مرتفع"

        change_sign = "+" if change_24h >= 0 else ""
        lines = [
            f"📊 *التحليل الأولي — {symbol}*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: {_fmt_price(price)} ({change_sign}{change_24h:.2f}%)",
            f"🌍 السوق: {regime_desc}",
            f"📈 RSI: {rsi:.0f} | Fear & Greed: {fear_val}",
            "",
            f"🎯 *التوصية: {direction}*",
            "",
            "📍 *مناطق الدخول والخروج*",
            f"• نقطة الدخول: {_fmt_price(entry)}",
            f"• هدف 1:       {_fmt_price(tp1)} ({(tp1/price-1)*100:+.1f}%)",
            f"• هدف 2:       {_fmt_price(tp2)} ({(tp2/price-1)*100:+.1f}%)",
            f"• وقف الخسارة: {_fmt_price(sl)} ({(sl/price-1)*100:+.1f}%)",
        ]
        if support > 0 and resistance > 0:
            lines += [
                "",
                "🏗️ *المستويات الرئيسية*",
                f"• دعم:    {_fmt_price(support)}",
                f"• مقاومة: {_fmt_price(resistance)}",
            ]
        if bear_buy_warning:
            lines.append(bear_buy_warning)
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
        "• تداول آلي مجاني 30 يوم",
        "• مسح تلقائي للفرص",
        "• 15 عملة في المسح",
        "",
        "🥈 *فضي — $9/شهر*",
        "• كل المجاني +",
        "• /signal — إشارة شاملة 5 مصادر",
        "• /news — تحليل الأخبار بالذكاء الاصطناعي",
        "• /regime — حالة السوق",
        "• /backtest — اختبار تاريخي 3 سنوات",
        "• 35 عملة في المسح",
        "",
        "🥇 *ذهبي — $29/شهر ⭐ الأكثر طلباً*",
        "• كل الفضي +",
        "• /analyze — تحليل عميق بالذكاء الاصطناعي",
        "• /liquidity — تحليل السيولة المتقدم",
        "• /onchain — تحليل On-Chain",
        "• /planweek و /planmonth — تخطيط ذكي",
        "• 100 عملة في المسح",
        "",
        "💎 *ماسي — $99/شهر*",
        "• كل الذهبي +",
        "• /chart — تحليل شارت بصري",
        "• تحليل كمي متقدم",
        "• 300 عملة في المسح",
        "• دعم مباشر 24/7",
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

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
from core.pair_resolver import resolve_symbol, build_pair_addon_lines, build_usdt_addendum
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


def _fmt_price(price: float, quote: str = "USDT") -> str:
    """تنسيق السعر حسب حجمه ووحدة التسعير (تطوير #188).
    quote="USDT" (افتراضي): السلوك الأصلي تماماً — "$X"."""
    if quote == "USDT":
        if price <= 0:      return "$0"
        elif price >= 1000: return f"${price:,.2f}"
        elif price >= 1:    return f"${price:,.4f}"
        elif price >= 0.001:return f"${price:.6f}"
        elif price >= 1e-6: return f"${price:.8f}"
        else:               return f"${price:.10f}"
    # أزواج BTC/ETH المباشرة (تطوير #188) — بدون "$"، مع لاحقة الوحدة
    if price <= 0:      return f"0 {quote}"
    elif price >= 1:    return f"{price:,.4f} {quote}"
    elif price >= 0.001:return f"{price:.6f} {quote}"
    elif price >= 1e-6: return f"{price:.8f} {quote}"
    else:               return f"{price:.10f} {quote}"


def _price_decimals(value: float) -> int:
    """عدد المنازل العشرية حسب فئة _fmt_price لقيمة واحدة (إصلاح #195)."""
    v = abs(value)
    if v <= 0:       return 2
    elif v >= 1000:  return 2
    elif v >= 1:     return 4
    elif v >= 0.001: return 6
    elif v >= 1e-6:  return 8
    else:            return 10


def _fmt_price_pair(a: float, b: float, quote: str = "USDT") -> tuple:
    """إصلاح #195: تنسيق زوج قيم مرتبطة (دعم/مقاومة) بنفس عدد
    المنازل العشرية — يُستخدَم عدد منازل أصغرهما (الأكثر دقة)
    لكليهما، لتفادي تفاوت بصري عند عبور حد 0.001 (مثل
    0.00099812 [8 منازل] مقابل 0.001160 [6 منازل] → كلاهما 8)."""
    dec = max(_price_decimals(a), _price_decimals(b))
    if quote == "USDT":
        return f"${a:,.{dec}f}", f"${b:,.{dec}f}"
    return f"{a:,.{dec}f} {quote}", f"{b:,.{dec}f} {quote}"


# ════════════════════════════════════════════════════════════════
# #221 — helper: سؤال نوع السوق (Spot / Futures) عند التحليل
# ════════════════════════════════════════════════════════════════
_GOLD_TIERS = ("gold", "diamond", "admin")

async def _ask_market_type(update, context, cmd: str, symbol: str, tier: str) -> bool:
    """تطوير #221: يعرض سؤال Spot/Futures لكل المستخدمين.
    - ذهبي+: يختار فعلاً (Spot أو Futures).
    - أقل من ذهبي: يرى السؤال ← إذا اختار Futures → رسالة ترقية (تحفيز).
    يُعيد True إذا أُرسِل السؤال (الأمر يجب أن يُعيد return وينتظر callback).
    يُعيد False إذا اختار المستخدم Spot تلقائياً (لا سؤال، تابع مباشرة)."""
    # بناء callback_data يحمل الأمر + الرمز
    def _cb(mtype):
        return f"mkttype_{cmd}_{mtype}_{symbol}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ سوق Spot", callback_data=_cb("spot")),
         InlineKeyboardButton("📈 سوق Futures", callback_data=_cb("futures"))],
    ])
    tier_note = "" if tier in _GOLD_TIERS else "\n_(Futures متاح للذهبي وأعلى — /upgrade)_"
    await update.message.reply_text(
        f"📊 تحليل *{symbol}* — اختر نوع السوق:{tier_note}",
        parse_mode="Markdown", reply_markup=kb)
    return True


async def callback_market_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تطوير #221: معالجة اختيار Spot/Futures قبل التحليل.
    صيغة: mkttype_{cmd}_{mtype}_{symbol}
    cmd: signal | analyze | quicksignal
    mtype: spot | futures"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) < 4:
        await query.edit_message_text("❌ بيانات غير صالحة"); return

    cmd    = parts[1]   # signal | analyze | quicksignal
    mtype  = parts[2]   # spot | futures
    symbol = parts[3].upper()

    user_id = update.effective_user.id
    tier    = _sm.get_tier(user_id)

    if mtype == "futures" and tier not in _GOLD_TIERS:
        await query.edit_message_text(
            f"⬆️ *تحليل Futures — للذهبي وأعلى*\n\n"
            f"للترقية والحصول على:\n"
            f"• تحليل سوق Futures المنفصل\n"
            f"• بيانات Funding Rate / OI / Liquidations\n"
            f"• تخطيط أسبوعي/شهري Futures (ماسي+)\n\n"
            f"⬆️ /upgrade",
            parse_mode="Markdown")
        return

    # تخزين اختيار نوع السوق في user_data ليستخدمه الأمر
    context.user_data["_mkttype"] = mtype
    context.user_data["_mkttype_symbol"] = symbol

    label = "⚡ Spot" if mtype == "spot" else "📈 Futures"
    await query.edit_message_text(
        f"{label} — جاري تحليل *{symbol}*...",
        parse_mode="Markdown")

    # استدعاء الأمر المناسب مع symbol محدد
    context.args = [symbol]
    engine = context.bot_data.get("raed_engine")
    if not engine:
        await query.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    try:
        if cmd == "signal":
            await cmd_signal(update, context)
        elif cmd == "analyze":
            await cmd_analyze(update, context)
        elif cmd == "quicksignal":
            await cmd_quicksignal(update, context)
    except Exception as e:
        logger.error(f"callback_market_type→{cmd}: {e}")
        await query.message.reply_text(f"❌ خطأ في التحليل. حاول لاحقاً")




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
        d = abs(val - price) / max(price, 1e-9) * 100
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
    # إصلاح #948: rsi كـ int مبكراً — مصدر واحد للحقيقة
    rsi = int(round(rsi))  # ضمان int دائماً
    direction = signal.direction
    tech      = getattr(signal, "technicals", {}) or {}
    # إصلاح #809/#61(ثانوي): _vol_ratio من technicals['vol_ratio'] مباشرة —
    # getattr(signal,'vol_ratio',...) كان يُعيد دائماً 1.0 الافتراضي لأن
    # SignalResult لا يملك حقل vol_ratio على المستوى الأعلى، فكان السطر
    # السابق (#260 لاحقاً) يفشل في رصد vol_ratio<0.8 قبل إعادة التعيين
    # اللاحقة في الكود — الآن مصدر واحد للحقيقة من البداية
    try:
        _vol_ratio = float(tech.get("vol_ratio", 1.0) or 1.0)
    except Exception:
        _vol_ratio = 1.0
    adx       = _calc_adx(candles) or float(tech.get("adx", 0) or 0)
    macd_hist = float(tech.get("macd_hist", 0) or 0)
    is_bear   = "هابط" in regime.description_ar
    closes    = [float(c.get("close", 0)) for c in candles if c.get("close")]
    ema50_val = sum(closes[-50:]) / 50 if len(closes) >= 50 else price * 1.05
    atr_dec   = atr_pct / 100

    # القرار — يعكس السيناريو الفعلي
    _scenario = tech.get("scenario", "")
    if conf >= 0.65 and direction == "long" and _scenario == "counter_trend_bounce":
        decision = "⚡ COUNTER-TREND BOUNCE — ارتداد مؤقت"
    elif conf >= 0.65 and direction == "long" and _scenario == "trend_reversal":
        decision = "🔄 REVERSAL — انعكاس اتجاه"
    elif conf >= 0.65 and direction == "long":
        decision = "✅ BUY — شراء"
    elif conf >= 0.65 and direction == "short":
        decision = "✅ SHORT — بيع"
    else:
        decision = "⚪ ANTICIPATE — انتظار"

    # الأسباب
    reasons = []
    if conf < 0.65:
        reasons.append(f"• الثقة {conf:.0%} أقل من الحد 65%")
    elif _scenario == "counter_trend_bounce" and _vol_ratio < 0.8:
        # إصلاح #61(ثانوي): الثقة تجاوزت 65% لكن الحجم غير مؤكَّد —
        # وضّح السبب الفعلي بدل حذف سطر العتبة بصمت
        reasons.append(
            f"• الثقة {conf:.0%} تجاوزت 65% — لكن الحجم {_vol_ratio:.1f}x "
            f"أقل من 0.8x المطلوب لتأكيد الارتداد")
    # إصلاح #86/#130: ADX فقط عند الخطورة القصوى
    if adx > 45:
        reasons.append(f"• ADX = {adx:.0f} → اتجاه قوي جداً — خطر الدخول مرتفع")
    if macd_hist < 0:
        reasons.append("• MACD سالب (sellers مسيطرون)")
    elif macd_hist > 0:
        reasons.append("• MACD موجب (زخم شراء)")
    if rsi < 30:
        reasons.append(f"• RSI = {int(rsi)} → ذروة بيع (فرصة انتعاش)")
    elif rsi > 70:
        reasons.append(f"• RSI = {int(rsi)} → ذروة شراء (خطر تصحيح)")
    else:
        reasons.append(f"• RSI = {rsi:.0f}")
    ns = fib.get("nearest_support", 0)
    nr = fib.get("nearest_resistance", 0)
    if ns > 0:
        dist = abs(price - ns) / max(price, 1e-9) * 100
        reasons.append(f"• الدعم {_fmt_price(ns)} على بُعد {dist:.1f}%")

    # شروط الدخول (للانتظار فقط)
    entry_conds = []
    if conf < 0.65 or direction == "neutral":
        # M#117: شرط RSI بناءً على القيمة الحالية
        # إصلاح #470/#481: الشرط الأول يستخدم أقرب مستوى حقيقي
        _near_res_c = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _ema20_val  = sum([float(c.get("close",0)) for c in candles[-20:]]) / 20 if len(candles) >= 20 else price * 1.03
        _close_target = _near_res_c if (_near_res_c and price < _near_res_c < price * 1.15) else _ema20_val

        if rsi < 40:
            _rsi_cond = f"1. RSI يتجاوز 30 صعوداً + إغلاق فوق {_fmt_price(_close_target)}"
        elif rsi < 55:
            _rsi_cond = f"1. RSI يتجاوز 55 + إغلاق فوق {_fmt_price(_close_target)}"
        else:
            _rsi_cond = f"1. انتظر تصحيح RSI تحت 60 ثم ارتداد"
        # إصلاح #378: شرط دخول يستخدم مقاومة فيبو القريبة بدلاً من EMA50 البعيد
        _fib_res    = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _target_lbl = (f"مقاومة فيبو ({_fmt_price(_fib_res)})"
                       if _fib_res and _fib_res > price
                       else f"EMA50 ({_fmt_price(ema50_val)})")
        # إصلاح #495: شرط 3 مختلف عن شرط 1
        _fib_res_cond = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _fib_sup_cond = fib.get("nearest_support",    0) if isinstance(fib, dict) else 0

        if _fib_res_cond and _fib_res_cond > price:
            # شرط 3 = Reclaim الدعم (مستوى أدنى من المقاومة)
            _sup_display = _fmt_price(_fib_sup_cond) if _fib_sup_cond else _fmt_price(price * 0.98)
            _cond3 = f"3. Reclaim وثبات فوق {_sup_display} (الدعم القريب)"
        else:
            _cond3 = "3. ظهور شمعة ارتداد قوية (Bullish Engulfing أو Hammer)"
        entry_conds = [
            _rsi_cond,
            "2. الثقة الإجمالية ≥ 60%",
            _cond3,
        ]
        if ns > 0:
            entry_conds.append(f"4. وصول Demand Zone {_fmt_price(ns)}")
        if adx > 40:
            entry_conds.append("5. MACD إيجابي أو تقاطع صاعد")

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

    rr = abs(pro_tp - pro_entry) / max(abs(pro_sl - pro_entry), 1e-9)

    # إضافة تحذير السيناريو
    _scenario_warn = signal.technicals.get("scenario_warn", "") if hasattr(signal, "technicals") else ""
    _scenario_ar   = signal.technicals.get("scenario_ar",   "") if hasattr(signal, "technicals") else ""
    # إصلاح #95 (توحيد القاعدة): SL% من السعر الحالي (نفس قاعدة Worst-Case
    # وR/R) بدل pro_entry — يمنع ظهور "Worst-Case% < SL%" بسبب اختلاف القاعدة
    sl_pct  = abs(price - pro_sl) / max(price, 1e-9) * 100
    tp_pct  = abs(pro_tp - pro_entry) / max(pro_entry, 1e-9) * 100
    hold    = 3 if adx > 40 else 5

    # إضافة تحذير السيناريو في decision
    # إصلاح #414: سطر واحد يجمع السيناريو والتحذير
    scenario_block = []
    if _scenario_warn:
        scenario_block.append(f"📊 *السيناريو:* {_scenario_warn}")
    elif _scenario_ar:
        scenario_block.append(f"📊 *السيناريو:* {_scenario_ar}")

    parts = [f"*{decision}*", ""]
    if scenario_block:
        parts.extend(scenario_block)
        parts.append("")
    if reasons:
        parts.append("*✅ الأسباب:*")
        parts.extend(reasons)
        parts.append("")
    if entry_conds:
        parts.append("*⏳ متى تدخل؟*")
        parts.extend(entry_conds)
        parts.append("")
    # حساب الحجم بناءً على السيناريو — التمييز الثلاثي
    _scenario = tech.get("scenario", "")
    if conf >= 0.65 and not is_bear:
        # سوق صاعد — دخول كامل
        vol_pct    = 50
        vol_reason = "ثقة جيدة + سوق محايد/صاعد"
    elif conf >= 0.65 and is_bear and _scenario == "counter_trend_bounce":
        # ارتداد مؤقت عكس الاتجاه — حجم مقيّد
        vol_pct    = 12
        vol_reason = "⚡ counter-trend scalp — 12% MAX (اتجاه هابط)"
    elif conf >= 0.65 and is_bear and _scenario == "trend_reversal":
        # انعكاس حقيقي مؤكد
        vol_pct    = 30
        vol_reason = "🔄 انعكاس اتجاه مؤكد — دخول تدريجي"
    elif conf >= 0.65 and is_bear:
        # سوق هابط بدون سيناريو محدد — حذر
        vol_pct    = 20
        vol_reason = "سوق هابط — حجم محدود للحماية"
    elif conf >= 0.50:
        vol_pct    = 15
        vol_reason = "ثقة متوسطة — تقليل للحماية"
    else:
        vol_pct    = 10
        vol_reason = "ثقة منخفضة — حد أدنى"

    # حجم فعلي من المحفظة الافتراضية
    portfolio_est = 10000  # سيُحسب من المحفظة الحقيقية

    # (#438) خيار المحترف القديم حُذف — الهيكل الجديد يُغني عنه
    # ══════════════════════════════════════════════════════════
    # الهيكل الاحترافي الكامل (المرحلة 1)
    # ══════════════════════════════════════════════════════════

    # ── المؤشرات الأساسية ──────────────────────────────────
    _mp       = getattr(regime, "market_phase", "") or ""
    _mp_ar    = _get_market_phase_ar(_mp) if _mp else ""
    _rsi_div  = tech.get("rsi_div",   "none")
    _vol_prof = tech.get("vol_profile", "normal")
    # إصلاح #788: حجم ضعيف < 0.8x يُصنَّف دائماً no_demand
    if _vol_ratio < 0.8 and _vol_prof not in ("climax_selling", "climax_buying"):
        _vol_prof = "no_demand"
    _bb_pos_raw = tech.get("bb_pos", None)
    # إصلاح #787/#850: RSI يُعدِّل BB عند الإجماع
    if _bb_pos_raw is None:
        _bb_pos_v = 0.5  # default
    else:
        _bb_pos_v = float(_bb_pos_raw)
    # RSI في ذروة بيع/شراء يُعدِّل BB إذا كان متعارضاً
    # إصلاح #1022: توسيع النطاق — RSI<25 يعني ذروة بيع → BB يجب أن يكون منخفض
    if rsi < 25 and _bb_pos_v > 0.3:
        _bb_pos_v = min(_bb_pos_v, 0.15)
    elif rsi < 30 and _bb_pos_v > 0.5:
        _bb_pos_v = min(_bb_pos_v, 0.30)
    elif rsi > 75 and _bb_pos_v < 0.7:
        _bb_pos_v = max(_bb_pos_v, 0.85)
    elif rsi > 70 and _bb_pos_v < 0.5:
        _bb_pos_v = max(_bb_pos_v, 0.65)
    _scenario = tech.get("scenario",   "")
    _conf_flags = tech.get("conf_flags", [])
    _atr_val  = tech.get("atr_value",  0)

    # ── Derivatives Data ────────────────────────────────────
    _oi    = tech.get("oi_data",    {}) or {}
    _fund  = tech.get("fund_data",  {}) or {}
    _whale = tech.get("whale_data", {}) or {}

    # Funding Rate signal
    _fund_pct = float(_fund.get("rate_pct", 0) or 0)
    # إصلاح #518: signal يعكس الحالة الحقيقية
    if _fund_pct < -0.02:
        _fund_sig = "🟢 سالب جداً (ضغط على Shorts — فرصة Longs)"
    elif _fund_pct < -0.005:
        _fund_sig = "🟢 سالب (فرصة Long)"
    elif _fund_pct > 0.02:
        _fund_sig = "🔴 مرتفع جداً (ضغط على Longs)"
    elif _fund_pct > 0.005:
        _fund_sig = "🟡 إيجابي (محايد)"
    else:
        _fund_sig = _fund.get("signal", "⚪ محايد")
    # Open Interest signal
    _oi_chg   = float(_oi.get("oi_change_pct", 0) or 0)
    _oi_sig   = _oi.get("signal", "")
    # Whale signal
    _whale_sig = _whale.get("signal", "")
    _whale_ratio = float(_whale.get("ratio", 0) or 0)

    # Funding Rate يضيف Confirmation Flag
    if _fund_pct < -0.01:
        _conf_flags = list(_conf_flags) + ["Funding Rate سالب (فرصة Longs) ✓"]
    if _oi_chg < -5:
        _conf_flags = list(_conf_flags) + ["OI انخفض (تصفية Shorts) ✓"]
    # إصلاح #471: فقط إذا ratio حقيقي (> 0) نُضيف flag
    if _whale_ratio > 0 and _whale_ratio < 0.6:
        _conf_flags = list(_conf_flags) + ["الحيتان تتراكم ✓"]
    # إصلاح #619/#651: Volume Spike يُضاف كـ Confirmation Flag
    if _vol_ratio >= 1.5:
        _conf_flags = list(_conf_flags) + [f"Volume Spike {_vol_ratio:.1f}x ✓"]
    # إصلاح #540: RSI extreme + BB
    if rsi < 20:
        _conf_flags = list(_conf_flags) + [f"RSI ذروة بيع ({rsi:.0f}) ✓"]
    elif rsi > 80:
        _conf_flags = list(_conf_flags) + [f"RSI ذروة شراء ({rsi:.0f}) ✓"]
    if _bb_pos_v < 0.15:
        _conf_flags = list(_conf_flags) + ["BB تحت الحد السفلي ✓"]
    elif _bb_pos_v > 0.85:
        _conf_flags = list(_conf_flags) + ["BB فوق الحد العلوي ✓"]

    _flags_found = len(_conf_flags)
    _confirmed   = _flags_found >= 2

    # ── Confidence Score مفصّل ──────────────────────────────
    _tech_score    = round(tech.get("score", 0.5) * 100)
    _oc_score      = round(getattr(signal, "onchain_score", 0.5) * 100)
    _sent_score    = round(getattr(signal, "news_score",    0.5) * 100)
    _macro_score   = round(getattr(signal, "macro_score",   0.5) * 100)
    _conf_score    = round(conf * 100)

    # ── القرار بناءً على Confidence ──────────────────────────
    if _conf_score < 40:
        _decision_label = "[WAIT] — لا صفقة نشطة"
        _pos_size_rule  = "0% — انتظر مؤشرات أقوى"
        # إصلاح #908: action يتوافق مع decision
        if hasattr(regime, 'action') and regime.action == "trade_normal":
            try:
                object.__setattr__(regime, 'action', 'avoid')
                # إصلاح #168/#179-181: شفافية — وضّح أن سبب "تجنب الدخول"
                # هنا هو انخفاض الثقة (<40%)، لا قوة الاتجاه (ADX)
                if hasattr(regime, 'metrics') and isinstance(regime.metrics, dict):
                    regime.metrics['action_basis'] = f" (الثقة {_conf_score}%<40%)"
            except Exception:
                pass
    elif _conf_score < 60:
        _decision_label = "[LOW] — حجم 3–5% فقط"
        _pos_size_rule  = f"5% — ثقة منخفضة"
        if hasattr(regime, 'action') and regime.action == "trade_normal":
            try:
                object.__setattr__(regime, 'action', 'reduce_size')
                if hasattr(regime, 'metrics') and isinstance(regime.metrics, dict):
                    regime.metrics['action_basis'] = f" (الثقة {_conf_score}%<60%)"
            except Exception:
                pass
    elif _conf_score < 80:
        _decision_label = "[NORMAL] — حجم 10–20%"
        _pos_size_rule  = f"12% — ثقة متوسطة"
    else:
        _decision_label = "[HIGH] — حجم 25–35%"
        _pos_size_rule  = f"25% — ثقة عالية"

    # إصلاح #730/#736: WAIT عند volume ضعيف في counter-trend scalp
    if _scenario == "counter_trend_bounce" and _vol_ratio < 0.8:
        _decision_label = "[WAIT] — حجم ضعيف للـ scalp"
        _pos_size_rule  = "0% — انتظر تأكيد الحجم ≥ 0.8x"

    # إصلاح #103: عند "🟢 شراء [HIGH/NORMAL]" في ارتداد مؤقت (الحجم مؤكَّد)،
    # "الإجراء: ⏳ انتظر — RSI ذروة بيع" يتعارض ظاهرياً مع التوصية أعلاه —
    # الارتداد الموصى به *هو* الانعكاس المُنتظر، فنُعدِّل النص ليعكس ذلك
    if (_scenario == "counter_trend_bounce" and direction == "long"
            and not _decision_label.startswith("[WAIT]")
            and hasattr(regime, 'action') and regime.action == "wait_reversal"):
        try: object.__setattr__(regime, 'action', 'bounce_entry_confirmed')
        except: pass

    # تحديث vol_pct من _decision_label
    if _conf_score < 40:
        vol_pct = 0
    elif _conf_score < 60:
        vol_pct = 5
    elif _conf_score < 80:
        vol_pct = 12

    # ── Entry Aggressive + Conservative ──────────────────────
    nr_fib = fib.get("nearest_resistance", price * 1.05) if fib else price * 1.05
    f236   = fib.get("0.236", price * 1.06) if fib else price * 1.06
    f382   = fib.get("0.382", price * 1.10) if fib else price * 1.10
    # إصلاح #439: Aggressive = عند الدعم (أدنى) | Conservative = بعد تأكيد (أعلى)
    # إصلاح #652: Entry Aggressive دائماً < Entry Conservative
    # Aggressive = أدنى سعر نستهدف (عند الدعم)
    # Conservative = بعد تأكيد الارتداد (أقرب للسعر الحالي)
    # إصلاح #133: نطاق أوسع (price*1.01 بدل price) يمنع قفزة entry_agg
    # بنسبة ~8% عند تذبذب السعر حول ns بفارق <1% (تقلب طبيعي بين استدعاءين)
    if ns > 0 and price * 0.94 < ns < price * 1.01:
        entry_agg = min(ns, price)  # عند دعم فيبو (أو أقرب إليه)
    else:
        entry_agg = price * (1 - atr_dec * 0.8)  # أدنى من السعر الحالي
    # Conservative: بين entry_agg والسعر الحالي
    _cons_offset = (price - entry_agg) * 0.4  # 40% من المسافة
    entry_cons = entry_agg + _cons_offset
    entry_cons = min(entry_cons, price * 0.998)  # لا يتجاوز السعر الحالي
    # ضمان: Aggressive < Conservative
    if entry_agg >= entry_cons:
        entry_cons = entry_agg * 1.005

    # ── TP متدرج حسب نوع الصفقة ──────────────────────────────
    if _scenario == "counter_trend_bounce":
        # إصلاح #622/#729: TP من الحساب فقط — لا Fibonacci في counter-trend
        _tp1_pct = min(0.06, atr_dec * 1.2)   # max 6% للـ scalp
        _tp2_pct = min(0.09, atr_dec * 2.0)   # max 9%
        tp1_v = price * (1 + _tp1_pct)
        tp2_v = price * (1 + _tp2_pct)
        # ضمان: TP2 > TP1 بفارق لا يقل عن 2%
        tp2_v = max(tp2_v, tp1_v * 1.02)
        tp3_v = None
        _time_exit = "3 أيام"
        _trade_dur = "ساعات — 3 أيام (Scalp)"
    elif _scenario == "trend_reversal":
        tp1_v = nr_fib; tp2_v = f236; tp3_v = f382
        _time_exit = "14 يوم"
        _trade_dur = "1–3 أسابيع (Swing)"
    else:
        # إصلاح #905: mean_reversion وbullish — TP معقول
        _tp1_mult = 1.2 if _scenario in ("bullish_continuation",) else 0.8
        _tp2_mult = 2.0 if _scenario in ("bullish_continuation",) else 1.5
        tp1_v = price * (1 + min(atr_dec * _tp1_mult, 0.08))   # max 8%
        tp2_v = price * (1 + min(atr_dec * _tp2_mult, 0.12))   # max 12%
        tp3_v = None
        _time_exit = "5 أيام"
        _trade_dur = "2–5 أيام"

    # إصلاح #620/#852/#949: R/R حقيقي
    # SL يُحسب من السعر الحالي للحصول على R/R دقيق
    # إصلاح #95/#102/#103: R/R حقيقي — يجب أن يُحسب من pro_sl المعروض
    # فعلياً (لا من _sl_price داخلي منفصل قد يختلف عن SL المعروض)
    # هذا يضمن أن "R/R الواقعي" يتطابق رياضياً مع TP1/SL المعروضين دائماً
    _sl_price = pro_sl if (pro_sl > 0 and pro_sl < price) else price * (1 - atr_dec * 1.0)
    _risk   = max(price - _sl_price, 1e-9)
    _reward = max(tp1_v - price, 1e-9)
    rr_real = round(min(_reward / _risk, 5.0), 1)
    # إصلاح #19: فرض R/R ≥ 1:1 على مستوى النظام (يطابق منطق risk_engine)
    # بدلاً من عرض R/R<1 وترك القرار للمستخدم — نرفع TP1/TP2 تناسبياً
    _rr_adjusted_note = ""
    if rr_real < 1.0:
        _old_tp1 = tp1_v
        _new_tp1 = price + _risk  # يضمن reward == risk → R/R = 1.0
        if tp2_v and _old_tp1 > price:
            _tp2_ratio = (tp2_v - price) / max(_old_tp1 - price, 1e-9)
            tp2_v = price + (_new_tp1 - price) * _tp2_ratio
        tp1_v   = _new_tp1
        _reward = max(tp1_v - price, 1e-9)
        rr_real = round(min(_reward / _risk, 5.0), 1)
        _rr_adjusted_note = " (مُعدَّل تلقائياً لضمان 1:1)"

    # Worst-Case
    # إصلاح #95: ضمان أن مستويات Worst-Case أعمق من (أو تساوي) SL المعروض،
    # ووصف wc_loss يعكس المستوى الأعمق (wc_bd2) لا pro_sl
    if pro_sl > 0 and pro_sl < price:
        wc_bd1 = min(price * 0.95, pro_sl)
        wc_bd2 = min(price * 0.90, pro_sl * 0.95)
    else:
        wc_bd1 = price * 0.95
        wc_bd2 = price * 0.90
    wc_loss = abs(price - wc_bd2) / max(price, 1e-9) * 100

    sl_type_ar    = _get_sl_type(_scenario, rsi)
    exit_strategy = (
        "TP1 (50%) ← TP2 (30%) ← TP3 (20%) مع Trailing SL"
        if tp3_v else
        "TP1 (60%) ← TP2 (40%) مع Hard SL"
    )

    # ── بناء الأقسام ─────────────────────────────────────────

    # 1. Confidence Score — إصلاح #8: لا نُكرر التفصيل (موجود في "مصادر الإشارة" أعلاه)
    # تطوير #209: الرافعة المقترحة من النظام
    _lev = getattr(signal, "suggested_leverage", 1)
    _lev_note = {
        1: "🛡️ 1x — حماية قصوى",
        2: "⚡ 2x — محافظ",
        3: "⚡ 3x — معتدل",
        5: "🔥 5x — ثقة عالية، سوق صاعد",
    }.get(_lev, f"{_lev}x")
    parts.extend([
        "",
        f"*🎯 Confidence Score: {_conf_score}%*",
        f"• القرار: *{_decision_label}*",
        f"• الرافعة المقترحة: {_lev_note}",
    ])

    # 2. SMC Block
    smc_lines = ["", "*📊 تحليل الهيكلة (SMC)*"]
    if _mp_ar:
        smc_lines.append(f"• Market Phase: {_mp_ar}")
    smc_lines.append(
        "• RSI Divergence: 🟢 Bullish Divergence ✓" if _rsi_div == "bullish" else
        "• RSI Divergence: 🔴 Bearish Divergence ✓" if _rsi_div == "bearish" else
        "• RSI Divergence: ⚪ لا divergence"
    )
    smc_lines.append(f"• Volume Profile: {_get_vol_profile_ar(_vol_prof, _vol_ratio)}")
    smc_lines.append(f"• Bollinger Bands: {_get_bb_status_ar(_bb_pos_v)}")
    if _atr_val:
        smc_lines.append(f"• ATR (تقلب): ${_atr_val:,.0f} يومياً")
    parts.extend(smc_lines)

    # 3. Derivatives (إذا متاحة)
    deriv_lines = []
    if _fund_pct != 0:
        deriv_lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fund_sig}")
    if _oi_chg != 0:
        deriv_lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
    if _whale_sig:  # عرض إذا يوجد signal نصي حتى لو ratio=0
        _wr_txt = f" ({_whale_ratio:.2f})" if _whale_ratio > 0 else ""
        deriv_lines.append(f"• Whale Activity{_wr_txt}: {_whale_sig}")
    # إضافة On-chain من DeFiLlama
    # TVL من On-chain data — نتحقق من بنية الـ dict
    _onchain = tech.get("onchain_data", {}) or {}
    # get_onchain() يُعيد: {"tvl": ..., "tvl_change_24h": ...} أو {"total_tvl": ...}
    _tvl = float(_onchain.get("tvl") or 0)
    if _tvl > 0:
        _tvl_chg = float(_onchain.get("tvl_change_1d", 0) or 0)
        deriv_lines.append(
            f"• TVL الكلي: ${_tvl/1e9:.1f}B ({_tvl_chg:+.1f}% 24h)"
        )
    if deriv_lines:
        parts.extend(["", "*🔗 Derivatives & On-Chain*"])
        parts.extend(deriv_lines)

    # 4. Entry + TP (مع مراعاة #440: لا TP عند WAIT)
    # إصلاح #785: TP% من السعر الحالي للمستخدم وليس من entry
    tp1_pct = abs(tp1_v - price) / max(price, 1e-9) * 100
        # إصلاح #849: tp2_pct من السعر الحالي
    tp2_pct = abs(tp2_v - price) / max(price, 1e-9) * 100 if tp2_v else 0
    # إصلاح #8: تسمية دقيقة — "عند الدعم" فقط إذا entry_agg ≈ مستوى الدعم المعروض
    _agg_label = "عند الدعم" if (ns > 0 and abs(entry_agg - ns) / max(ns, 1e-9) < 0.005) else "سحب فني (Pullback)"
    entry_lines = [
        "",
        "*📍 مناطق الدخول والخروج*",
        f"• Entry 1 (Aggressive): {_fmt_price(entry_agg)} — {_agg_label}",
        f"• Entry 2 (Conservative): {_fmt_price(entry_cons)} — بعد تأكيد الارتداد",
    ]
    if _conf_score >= 40:  # لا أهداف عند WAIT
        entry_lines.extend([
            f"• TP1: {_fmt_price(tp1_v)} (+{tp1_pct:.1f}%)",
            f"• TP2: {_fmt_price(tp2_v)} (+{tp2_pct:.1f}%)",
        ])
        if tp3_v:
            tp3_pct = abs(tp3_v - entry_agg) / max(entry_agg, 1e-9) * 100
            entry_lines.append(f"• TP3 (اختياري): {_fmt_price(tp3_v)} (+{tp3_pct:.1f}%)")
    else:
        entry_lines.append("• الأهداف: متاحة بعد تأكيد 2/4 مؤشرات")
    entry_lines.extend([
        f"• وقف الخسارة: {_fmt_price(pro_sl)} ({sl_pct:.1f}%-)",
        f"• R/R الواقعي: 1:{rr_real}{_rr_adjusted_note}",
        f"• الحجم: {_pos_size_rule}",
    ])
    parts.extend(entry_lines)

    # 5. إدارة المخاطر + Time Exit + Worst-Case
    parts.extend([
        "",
        "*🛡️ إدارة المخاطر*",
        f"• نوع الوقف: {sl_type_ar}",
        f"• استراتيجية الخروج: {exit_strategy}",
        f"• المدة المتوقعة: {_trade_dur}",
        f"• ⏰ Time Exit: إذا لا حركة بعد {_time_exit} → أغلق الموضع",
        f"• ⚠️ Worst-Case: كسر {_fmt_price(wc_bd1)} → {_fmt_price(wc_bd2)} (خسارة ~{wc_loss:.1f}%)",
    ])

    # 6. إصلاح #204/#206: قسمان منفصلان بتسميات واضحة
    # 6-A. تأكيدات الدخول الحاسمة (تؤثر على حجم الصفقة عبر _confirmed/_flags_found)
    parts.extend(["", "*🔑 تأكيدات الدخول (تُحدِّد حجم الصفقة — الحد الأدنى: 2 من 4)*"])
    if _confirmed:
        parts.append(f"✅ {_flags_found}/4 مؤكدة — الإشارة *نشطة*")
    else:
        parts.append(f"⚠️ {_flags_found}/4 — انتظر مؤشر إضافي")
    for flag in _conf_flags:
        parts.append(f"  ✓ {flag}")
    if not _conf_flags:
        parts.append("  • لا تأكيدات بعد — يمكن الدخول بحجم مصغَّر فقط")

    # 6-B. Checklist الاستعداد (مؤشرات داعمة — لا تؤثر على الحجم، تكميلية)
    parts.extend([
        "",
        "*📋 Checklist الاستعداد (مؤشرات داعمة — لا تؤثر على حجم الصفقة)*",
        f"{'☑' if _rsi_div != 'none' else '□'} RSI Divergence"
        + (" 🟢" if _rsi_div == "bullish" else " 🔴" if _rsi_div == "bearish" else ""),
        f"{'☑' if _vol_ratio >= 1.5 else '□'} Volume Spike ≥1.5x (حالياً {_vol_ratio:.1f}x)",
        f"{'☑' if ns > 0 and price >= ns * 0.995 else '□'} Reclaim الدعم {_fmt_price(ns) if ns else 'N/A'}",
        f"{'☑' if tech.get('macd_hist', 0) > 0 else '□'} MACD إيجابي",
        f"{'☑' if _whale_ratio > 0 and _whale_ratio < 0.6 else '□'} On-chain تراكم (Whale Ratio < 0.6)",
        f"{'☑' if _fund_pct < -0.01 else '□'} Funding Rate مناسب",
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


def _get_market_phase_ar(phase: str) -> str:
    """ترجمة Market Phase للعربية."""
    return {
        "Accumulation":   "🔵 تراكم (Accumulation)",
        "Distribution":   "🟠 توزيع (Distribution)",
        "Markup":         "🟢 صعود (Markup)",
        "Markdown":       "🔴 هبوط (Markdown)",
        "Consolidation":  "🟡 تعزيز (Consolidation)",
    }.get(phase, "⚪ غير محدد")


def _get_vol_profile_ar(vol_profile: str, vol_ratio: float) -> str:
    """تفسير Volume Profile."""
    if vol_profile == "climax_selling":
        return f"⚠️ Climax Selling ({vol_ratio:.1f}x) — ضغط بيعي استثنائي قد يشير لقاع"
    elif vol_profile == "climax_buying":
        return f"⚡ Climax Buying ({vol_ratio:.1f}x) — ضغط شرائي قوي"
    elif vol_profile == "above_average":
        return f"📈 حجم فوق المتوسط ({vol_ratio:.1f}x)"
    elif vol_profile == "no_demand" or vol_ratio < 0.8:
        return f"📉 حجم ضعيف ({vol_ratio:.1f}x) — غياب طلب"
    return f"⚪ حجم عادي ({vol_ratio:.1f}x)"


def _get_bb_status_ar(bb_pos: float) -> str:
    """موقع Bollinger Bands."""
    if bb_pos < 0.1:   return "📉 تحت الحد السفلي (Oversold شديد)"
    elif bb_pos < 0.25: return "⬇️ قرب الحد السفلي"
    elif bb_pos > 0.9:  return "📈 فوق الحد العلوي (Overbought شديد)"
    elif bb_pos > 0.75: return "⬆️ قرب الحد العلوي"
    return "⚪ منتصف النطاق"


def _get_sl_type(scenario: str, rsi: float) -> str:
    """نوع وقف الخسارة حسب السيناريو."""
    if scenario == "counter_trend_bounce":
        return "Hard Stop — وقف صارم لا يتحرك"
    elif scenario == "trend_reversal":
        return "Trailing Stop — يتحرك مع الربح"
    return "Hard Stop"


def _build_scenarios_context(
    price: float,
    atr_pct: float,
    fib: dict,
    rsi: float,
    is_bear: bool,
) -> str:
    """
    يبني السيناريوهات الثلاثة من بيانات حقيقية:
    - مستويات الدعم/المقاومة من Fibonacci
    - ATR لحساب الحركة المتوقعة
    يُمرَّر لـ Groq كـ context — Groq يُفسر فقط
    """
    atr = price * atr_pct / 100
    ns  = fib.get("nearest_support",    0) or price * 0.97
    nr  = fib.get("nearest_resistance", 0) or price * 1.05
    f382 = fib.get("0.382", price * 1.06)
    f618 = fib.get("0.618", price * 1.10)
    f786 = fib.get("0.786", price * 1.14)

    # دعوم ومقاومات واقعية
    # إصلاح #94: لا نُقرِّب لمنزلتين — يُصفِّر عملات السعر <$0.01 (مثل SUPRA)
    # _fmt_price يتعامل مع الدقة المناسبة لكل نطاق سعري تلقائياً
    sup1 = ns
    sup2 = ns - atr * 1.5
    sup3 = ns - atr * 3.0
    res1 = nr
    res2 = f382 if f382 > price else nr * 1.04
    res3 = f618 if f618 > price else nr * 1.08

    rsi_note = ""
    if rsi < 20:
        rsi_note = f"RSI={rsi:.0f} (تشبع بيعي تاريخي — يرفع احتمال الارتداد قبل استئناف الاتجاه)."
    elif rsi < 30:
        rsi_note = f"RSI={rsi:.0f} (تشبع بيعي — احتمال ارتداد قصير)."
    elif rsi > 70:
        rsi_note = f"RSI={rsi:.0f} (تشبع شرائي — احتمال تصحيح)."

    if is_bear:
        scenarios = (
            "السيناريوهات المحتملة: "
            + f"أ) هابط: كسر {_fmt_price(sup1)} يفتح {_fmt_price(sup2)} ثم {_fmt_price(sup3)}. "
            + f"ب) ارتداد: ثبات فوق {_fmt_price(sup1)} يستهدف {_fmt_price(res1)} ثم {_fmt_price(res2)}. "
            + f"ج) انعكاس: استعادة {_fmt_price(res2)} تستهدف {_fmt_price(res3)}. "
            + f"للشورت: رفض من {_fmt_price(res1)}-{_fmt_price(res2)} مع ضعف الزخم. "
            + f"للـ scalp long: ثبات فوق {_fmt_price(sup1)} ووقف تحت {_fmt_price(sup2)}. "
            + f"للمحافظ: انتظار إغلاق فوق {_fmt_price(res1)} أو كسر {_fmt_price(sup2)}."
        )
    else:
        scenarios = (
            "السيناريوهات المحتملة: "
            + f"أ) صاعد: ثبات فوق {_fmt_price(ns)} يستهدف {_fmt_price(res1)} ثم {_fmt_price(res2)}. "
            + f"ب) تصحيح: كسر {_fmt_price(ns)} يفتح {_fmt_price(sup2)}. "
            + f"ج) انعكاس هابط: كسر {_fmt_price(sup2)} يعيد النظر في الاتجاه. "
            + f"للـ long: دخول عند {_fmt_price(ns)} ووقف {_fmt_price(sup2)}. "
            + f"للمحافظ: انتظار إغلاق فوق {_fmt_price(res1)}."
        )

    return (rsi_note + "\n" + scenarios).strip() if rsi_note else scenarios.strip()


def _fmt_volume(vol: float) -> str:
    """تنسيق الحجم بوحدة مناسبة: B/M$."""
    if vol <= 0:
        return "N/A"
    # إصلاح #515: vol > 1T يعني contracts وليس USD → نقسم على 1000
    if vol >= 1e13:
        vol = vol / 1000
    if vol >= 1e9:
        return f"{vol/1e9:.1f}B$"
    elif vol >= 1e6:
        return f"{vol/1e6:.1f}M$"
    elif vol >= 1e3:
        return f"{vol/1e3:.1f}K$"
    else:
        return f"{vol:,.0f}$"


def _calc_bb_pos(closes: list, period: int = 20) -> float:
    """موقع السعر في Bollinger Bands: 0=أدنى، 1=أعلى."""
    if len(closes) < period:
        return 0.5
    window = closes[-period:]
    avg = sum(window) / period
    std = (sum((x-avg)**2 for x in window)/period)**0.5
    if std == 0:
        return 0.5
    upper = avg + 2*std
    lower = avg - 2*std
    price = closes[-1]
    return round(max(0, min(1, (price - lower) / (upper - lower))), 3)


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
        # إصلاح #924: timeout صارم + fallback
        import asyncio as _aio_n
        try:
            items = await _aio_n.wait_for(
                engine.data_layer.get_news(
                    currencies=",".join(symbols), limit=20),
                timeout=20.0)
            items = items or []
        except (_aio_n.TimeoutError, Exception) as _ne:
            logger.warning(f"news fetch failed: {_ne}")
            items = []

        try:
            analysis = await _aio_n.wait_for(
                engine.news_engine.analyze(items, symbols),
                timeout=15.0)
            if not analysis or not isinstance(analysis, dict):
                analysis = engine.news_engine._neutral_analysis()
        except Exception as e:
            logger.warning(f"news analyze: {e}")
            analysis = engine.news_engine._neutral_analysis()

        try:
            if items:
                engine.event_risk.ingest_news_events(items)
        except Exception:
            pass

        text = engine.news_engine.format_ar(items, analysis)
        text = _clean_md(text) if text else ""
        if not text:
            text = "📰 لا توجد أخبار متاحة حالياً. حاول لاحقاً."

        # إصلاح #16: تحذير عند تناقض مشاعر الأخبار مع حالة السوق الفعلية
        try:
            _fear_n  = await _aio_n.wait_for(engine.data_layer.get_fear_greed(), timeout=8.0)
            _fear_v  = int((_fear_n or {}).get("value") or 50)
            _btc_c   = await _aio_n.wait_for(engine.data_layer.get_ohlcv("BTC", "1d", 60), timeout=10.0)
            _btc_c   = _btc_c if isinstance(_btc_c, list) else []
            if len(_btc_c) >= 30:
                _regime_n = engine.regime_detector.detect(_btc_c, fear_greed=_fear_v)
                _sent_n   = float(analysis.get("sentiment_score", 0) or 0)
                if _sent_n > 0.3 and "هابط" in _regime_n.description_ar:
                    text += (f"\n\n⚠️ *تنبيه:* مشاعر الأخبار إيجابية لكن السوق "
                             f"{_regime_n.description_ar} (Fear & Greed: {_fear_v}) "
                             f"— لا تعتمد على الأخبار وحدها لاتخاذ القرار")
        except Exception as _ce:
            logger.debug(f"news contradiction check: {_ce}")

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"cmd_news: {e}", exc_info=True)
        try:
            await msg.edit_text("📰 تعذَّر جلب الأخبار حالياً. حاول مجدداً لاحقاً.")
        except Exception:
            pass


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
        data, fear, funding, oi, whale, btc_adv = await asyncio.gather(
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_funding_rate("BTC"),
            engine.data_layer.get_open_interest("BTC"),
            engine.data_layer.get_whale_ratio("BTC"),
            engine.data_layer.get_btc_onchain_advanced(),
            return_exceptions=True
        )
        btc_adv = btc_adv if isinstance(btc_adv, dict) else {"available": False}
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

        # إصلاح #13: قسم المشتقات BTC (Funding/OI/Whale) — بيانات كانت تُجلَب ولا تُعرض
        _fund_pct  = float((funding or {}).get("rate_pct", 0) or 0)
        _oi_chg    = float((oi or {}).get("oi_change_pct", 0) or 0)
        _oi_sig    = (oi or {}).get("signal", "")
        _whale_sig = (whale or {}).get("signal", "")
        _whale_r   = float((whale or {}).get("ratio", 0) or 0)

        if _fund_pct or _oi_chg or _whale_sig:
            lines += ["", "📐 *مشتقات BTC*"]
            if _fund_pct:
                if _fund_pct < -0.02:
                    _fsig = "🟢 سالب جداً — ضغط Shorts (فرصة Long)"
                elif _fund_pct < -0.005:
                    _fsig = "🟢 سالب — فرصة Long"
                elif _fund_pct > 0.02:
                    _fsig = "🔴 مرتفع جداً — ضغط Longs"
                elif _fund_pct > 0.005:
                    _fsig = "🟡 إيجابي — محايد"
                else:
                    _fsig = "⚪ محايد"
                lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fsig}")
            if _oi_chg:
                lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
            if _whale_sig:
                _wr_txt = f" ({_whale_r:.2f})" if _whale_r > 0 else ""
                lines.append(f"• Whale Ratio{_wr_txt}: {_whale_sig}")

        # تطوير جديد: مؤشرات BTC on-chain متقدمة من BGeometrics
        # (MVRV Z-Score, SOPR, Exchange Netflow, Puell Multiple)
        if btc_adv.get("available"):
            lines += ["", "📊 *مؤشرات BTC المتقدمة (BGeometrics)*"]
            if "mvrv_zscore" in btc_adv:
                lines.append(f"• MVRV Z-Score: {btc_adv['mvrv_zscore']} — {btc_adv['mvrv_signal']}")
            if "sopr" in btc_adv:
                lines.append(f"• SOPR: {btc_adv['sopr']} — {btc_adv['sopr_signal']}")
            if "exchange_netflow_btc" in btc_adv:
                lines.append(f"• Exchange Netflow: {btc_adv['exchange_netflow_btc']:+,.1f} BTC — {btc_adv['netflow_signal']}")
            if "puell_multiple" in btc_adv:
                lines.append(f"• Puell Multiple: {btc_adv['puell_multiple']} — {btc_adv['puell_signal']}")

        # تفسير وتوصية إجمالية مختصرة بناءً على البيانات المجمَّعة
        _reco_parts = []
        if fear_val <= 25:
            _reco_parts.append("Fear شديد → فرصة تجميع تدريجي عند التأكيد")
        elif fear_val >= 75:
            _reco_parts.append("Greed مرتفع → حذر من الانعكاس")
        if _fund_pct < -0.01:
            _reco_parts.append("Funding سالب يدعم سيناريو الارتداد")
        if _whale_r and _whale_r < 0.6:
            _reco_parts.append("الحيتان تتراكم")
        if tvl_change < -3:
            _reco_parts.append("خروج سيولة من DeFi")
        if btc_adv.get("mvrv_zscore") is not None:
            if btc_adv["mvrv_zscore"] < 0:
                _reco_parts.append("MVRV Z-Score في منطقة قاع تاريخية")
            elif btc_adv["mvrv_zscore"] > 7:
                _reco_parts.append("MVRV Z-Score في منطقة قمة تاريخية — حذر")
        if btc_adv.get("exchange_netflow_btc") is not None and btc_adv["exchange_netflow_btc"] < 0:
            _reco_parts.append("تدفق صافٍ خارج البورصات (Hodling)")
        if _reco_parts:
            lines += ["", f"💡 *التفسير*: {' · '.join(_reco_parts)}"]

        _src = "📡 المصدر: DeFiLlama + OKX"
        if btc_adv.get("available"):
            _src += " + BGeometrics"
        lines += ["", f"{_src} | 🤖 رائد"]
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

    args    = context.args or ["BTC"]
    raw_arg = args[0].upper()

    # تطوير #221: سؤال نوع السوق (Spot/Futures) — لجميع المستخدمين
    # (يُتجاوَز إذا جاء الطلب من callback_market_type الذي يحدد النوع مسبقاً)
    _mkttype = context.user_data.pop("_mkttype", None)
    if _mkttype is None:
        tier_pre = _sm.get_tier(user_id)
        sent = await _ask_market_type(update, context, "signal", raw_arg, tier_pre)
        if sent:
            return  # ننتظر اختيار المستخدم عبر callback_market_type

    # نوع السوق: "futures" أو "spot" (الافتراضي)
    _use_futures = (_mkttype == "futures")

    # تطوير #188 (Phase 2): دعم أزواج BTC/ETH — فقرة إضافية في نهاية
    # التقرير إن كانت الباقة ماسي+ والزوج متوفر (build_pair_addon_lines)
    user_id2   = update.effective_user.id
    tier2      = _sm.get_tier(user_id2)
    resolution = await resolve_symbol(raw_arg, tier2, engine.data_layer)
    symbol     = resolution.base

    # تطوير #209: اكتشاف تلقائي للأسهم المُرمَّزة (ماسي+ فقط)
    _is_perp_sig = False
    if tier2 in ("diamond", "admin"):
        try:
            _is_perp_sig = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            _is_perp_sig = False

    # إصلاح #1020: عملات كبيرة مُعتمَدة دائماً للذهبي+
    _ALWAYS_ALLOWED = {
        "XLM","STELLAR","ICP","FIL","VET","EOS","XTZ","ALGO",
        "HBAR","EGLD","ONE","ZIL","ICX","WAVES","NEO","QTUM",
        "KAVA","BAND","RSR","NMR","RLC","ANKR","SKL","CKB",
    }
    if tier2 in ("gold","diamond","admin") and symbol.upper() in _ALWAYS_ALLOWED:
        pass  # مسموح
    elif _is_perp_sig and tier2 in ("diamond","admin"):
        pass  # تطوير #209: أسهم مُرمَّزة مسموحة لماسي+
    elif not is_symbol_allowed(symbol, tier2):
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
        _ohlcv_fn = engine.data_layer.get_ohlcv_perp(symbol, 365) if _is_perp_sig else engine.data_layer.get_ohlcv(symbol, "1d", 365)
        candles, onchain, fear, news_raw, btc_dom, _sig_4h = await asyncio.gather(
            _ohlcv_fn,
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_news(currencies=symbol),
            engine.data_layer.get_btc_dominance(),
            engine.data_layer.get_ohlcv_4h(symbol, 50),
            return_exceptions=True
        )
        candles  = candles  if isinstance(candles, list) else []
        onchain  = onchain  if isinstance(onchain, dict) else {}
        fear     = fear     if isinstance(fear, dict)    else {"value": 50}
        news_raw = news_raw if isinstance(news_raw, list) else []
        _sig_4h  = _sig_4h  if isinstance(_sig_4h, list)  else []

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
        regime   = engine.regime_detector.detect(candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=symbol)

        sentiment = 0.0
        if news_an:
            raw_sent = news_an.get("sentiment_score")
            if raw_sent is not None:
                try:
                    sentiment = float(raw_sent)
                except (ValueError, TypeError):
                    sentiment = 0.0

        # إصلاح #34: إضافة whale_ratio/funding الخاصين بالعملة لـ onchain_data
        # (يستخدمها _onchain_signal بدل TVL العالمي الثابت)
        onchain = await engine.data_layer.get_signal_enrichment(symbol, onchain)

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
        # تمرير سقف السيناريو لـ risk_engine
        _scenario_max = float(signal.technicals.get("max_size_pct", 0.35) or 0.35)
        risk    = engine.risk_engine.assess(
            symbol=symbol, direction=signal.direction,
            confidence=signal.confidence, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
            scenario_max_pct=_scenario_max,
        )

        # إصلاح #478: rsi من candles (الحقيقي) وليس من signal.technicals
        rsi = _calc_rsi(candles)
        # تحديث rsi في technicals لضمان التطابق في العرض
        if hasattr(signal, "technicals") and isinstance(signal.technicals, dict):
            signal.technicals["rsi"] = round(rsi, 1)
            # إصلاح #479: BB من 4H إذا متاح
            if len(_sig_4h) >= 20:
                _c4h = [float(c.get("close",0)) for c in _sig_4h]
                signal.technicals["bb_pos"] = _calc_bb_pos(_c4h)
        # تحذير RSI/اتجاه متعارض
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
        # إصلاح #477: تقليل التكرار — pro_block يحتوي معظم المعلومات
        parts = [
            _clean_md(engine.signal_layer.format_ar(signal)),
            # regime وstrategy مدمجان في pro_block — نُضيف حالة السوق فقط
            _clean_md(engine.regime_detector.format_ar(regime)),
        ]
        if show_risk:
            parts.append(_risk_text)
        parts.append(_clean_md(pro_block))
        if fib_lines:
            parts.append(_clean_md("\n".join(fib_lines)))
        # إصلاح #219/#220: إشارة Futures فقط إذا مؤهل + futures_enabled في التداول الآلي
        # وتوضيح نوع الصفقة (شراء Long / بيع Short) بدقة
        try:
            user_id_sig = user_id
            tt_sig      = getattr(signal, "trade_type", "spot")
            tier_sig    = _sm.get_tier(user_id_sig)
            # إصلاح #219: شرط futures_enabled — لا تظهر إلا إن فعَّل المستخدم Futures
            fut_enabled = _sm.get_futures_enabled(user_id_sig)
            if (tt_sig in ("futures_long", "futures_short")
                    and tier_sig in ("gold","diamond","admin")
                    and fut_enabled):
                fut_atr  = _calc_atr(candles) / 100 if candles else 0.03
                fut_dir  = "long" if tt_sig == "futures_long" else "short"
                # إصلاح #220: توضيح نوع الصفقة بدقة (Long=شراء / Short=بيع)
                fut_dir_ar = "📈 شراء (Long)" if fut_dir=="long" else "📉 بيع (Short)"
                if fut_dir == "long":
                    fut_tp = price * (1 + fut_atr * 2)
                    fut_sl = price * (1 - fut_atr * 1.2)
                else:
                    fut_tp = price * (1 - fut_atr * 2)
                    fut_sl = price * (1 + fut_atr * 1.2)
                fut_txt = engine.risk_engine.format_futures_signal_ar(
                    symbol, fut_dir, price, fut_tp, fut_sl, leverage=1)
                parts.append(_clean_md(
                    f"🔮 *توصية Futures: {fut_dir_ar}*\n" + fut_txt))
        except Exception as _fe:
            logger.debug(f"futures display: {_fe}")

        full_text = "\n\n".join(parts) + warning
        # تطوير #188 (Phase 2): إلحاق فقرة الزوج الإضافية إن وُجدت
        _pair_addon = await build_pair_addon_lines(resolution, engine.data_layer)
        if _pair_addon:
            full_text += "\n" + "\n".join(_pair_addon)
        # تطوير #209: ملاحظة Perp للأسهم المُرمَّزة
        if _is_perp_sig:
            full_text += f"\n\n📌 *{symbol}* — سهم مُرمَّز (Perpetual) على OKX"
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
    valid    = ["trend_following", "mean_reversion", "breakout", "hybrid"]

    _auto_strategy_note = ""
    if len(args) > 1:
        strategy = args[1].lower()
        if strategy not in valid:
            await update.message.reply_text(
                "⚠️ الاستراتيجيات المتاحة:\n"
                "• trend_following (الاتجاه — افتراضي)\n"
                "• mean_reversion (الارتداد)\n"
                "• breakout (الاختراق)\n"
                "• hybrid (مدمج EMA+RSI)\n\n"
                "مثال: /backtest BTC trend_following")
            return
    else:
        # إصلاح #17: اختيار الاستراتيجية تلقائياً حسب حالة السوق الحالية
        strategy = "trend_following"  # افتراضي احتياطي
        try:
            from core.regime_detector import Regime
            _rc, _rf = await asyncio.gather(
                engine.data_layer.get_ohlcv(symbol, "1d", 200),
                engine.data_layer.get_fear_greed(),
                return_exceptions=True
            )
            _rc = _rc if isinstance(_rc, list) else []
            _rf_val = int((_rf or {}).get("value") or 50) if isinstance(_rf, dict) else 50
            if len(_rc) >= 30:
                _regime_b = engine.regime_detector.detect(_rc, fear_greed=_rf_val)
                _reg_map = {
                    Regime.BULL_TREND:    "trend_following",
                    Regime.BEAR_TREND:    "mean_reversion",
                    Regime.SIDEWAYS:      "mean_reversion",
                    Regime.ACCUMULATION:  "hybrid",
                    Regime.DISTRIBUTION:  "mean_reversion",
                }
                strategy = _reg_map.get(_regime_b.regime, "trend_following")
                _auto_strategy_note = (f"\n💡 استراتيجية تلقائية بناءً على حالة السوق "
                                        f"({_regime_b.description_ar})")
        except Exception as _re:
            logger.debug(f"backtest auto-strategy: {_re}")

    strategy_ar = {
        "trend_following": "اتباع الاتجاه",
        "mean_reversion":  "الارتداد للمتوسط",
        "breakout":        "الاختراق",
        "hybrid":          "مدمج EMA+RSI",
    }
    msg = await update.message.reply_text(
        f"⏳ جاري Backtest لـ {symbol} — {strategy_ar[strategy]}\n"
        f"🔬 3 سنوات بيانات حقيقية — قد يستغرق 30-60 ثانية"
        f"{_auto_strategy_note}"
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
    # تطبيع symbol على مستوى النظام: BTCUSDT → BTC
    raw_sym = args[0].upper().strip().replace("/", "").replace("-", "")
    for _sfx in ("USDT","BUSD","USDC"):
        if raw_sym.endswith(_sfx) and len(raw_sym) > len(_sfx):
            raw_sym = raw_sym[:-len(_sfx)]
            break
    symbol = raw_sym
    msg = await update.message.reply_text(f"🔬 جاري تحليل السيولة لـ {symbol}...")

    try:
        profile, walls, funding, oi, whale = await asyncio.gather(
            engine.microstructure.analyze(symbol, order_size_usd=1000),
            engine.microstructure.detect_walls(symbol),
            engine.data_layer.get_funding_rate(symbol),
            engine.data_layer.get_open_interest(symbol),
            engine.data_layer.get_whale_ratio(symbol),
            return_exceptions=True
        )

        if not profile or isinstance(profile, Exception):
            await msg.edit_text(f"⚠️ بيانات السيولة لـ {symbol} غير متاحة حالياً")
            return

        # تمرير walls لـ format_ar مباشرة
        walls_safe = walls if not isinstance(walls, Exception) else None
        text = _clean_md(engine.microstructure.format_ar(profile, walls=walls_safe))

        # إصلاح #14: قسم المشتقات (Funding/OI/Whale) — كان مفقوداً تماماً
        funding = funding if isinstance(funding, dict) else {}
        oi      = oi      if isinstance(oi,      dict) else {}
        whale   = whale   if isinstance(whale,   dict) else {}
        _fund_pct  = float(funding.get("rate_pct", 0) or 0)
        _oi_chg    = float(oi.get("oi_change_pct", 0) or 0)
        _oi_sig    = oi.get("signal", "")
        _whale_sig = whale.get("signal", "")
        _whale_r   = float(whale.get("ratio", 0) or 0)

        deriv_lines = []
        if _fund_pct:
            if _fund_pct < -0.02:
                _fsig = "🟢 سالب جداً — ضغط Shorts (فرصة Long)"
            elif _fund_pct < -0.005:
                _fsig = "🟢 سالب — فرصة Long"
            elif _fund_pct > 0.02:
                _fsig = "🔴 مرتفع جداً — ضغط Longs"
            elif _fund_pct > 0.005:
                _fsig = "🟡 إيجابي — محايد"
            else:
                _fsig = "⚪ محايد"
            deriv_lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fsig}")
        if _oi_chg:
            deriv_lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
        if _whale_sig:
            _wr_txt = f" ({_whale_r:.2f})" if _whale_r > 0 else ""
            deriv_lines.append(f"• Whale Ratio (Long/Short){_wr_txt}: {_whale_sig}")

        if deriv_lines:
            text += "\n\n📐 *مشتقات*\n" + "\n".join(deriv_lines)

        # تفسير الاختلال + توصية مختصرة
        _imb = getattr(profile, "imbalance", 0.5)
        _reco = []
        if _imb < 0.40:
            _reco.append("ضغط بيع قوي في الـ Order Book — توخَّ الحذر من شراء فوري")
        elif _imb > 0.60:
            _reco.append("ضغط شراء قوي — دعم محتمل قريب")
        if _fund_pct < -0.01:
            _reco.append("Funding سالب يدعم سيناريو ارتداد Long")
        elif _fund_pct > 0.02:
            _reco.append("Funding مرتفع — خطر تصفية Longs مفرطة")
        if _reco:
            text += f"\n\n💡 *التفسير*: {' · '.join(_reco)}"

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
        # #716: توحيد النافذة الزمنية — نفس ما يستخدمه assess()
        _ev_window = 168  # أسبوع كامل
        text_ev = engine.event_risk.format_upcoming_ar(hours=_ev_window)
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
    raw_arg = args[0].upper()

    # تطوير #221: سؤال نوع السوق (Spot/Futures) إن لم يُحدَّد مسبقاً
    _mkttype_an = context.user_data.pop("_mkttype", None)
    if _mkttype_an is None:
        tier_pre_an = _sm.get_tier(user_id)
        sent_an = await _ask_market_type(update, context, "analyze", raw_arg, tier_pre_an)
        if sent_an:
            return
    _use_futures_an = (_mkttype_an == "futures")

    # تطوير #188 (Phase 2): دعم أزواج BTC/ETH — فقرة إضافية في نهاية
    # التقرير إن كانت الباقة ماسي+ والزوج متوفر (build_pair_addon_lines)
    tier_an    = _sm.get_tier(user_id)
    resolution = await resolve_symbol(raw_arg, tier_an, engine.data_layer)
    symbol     = resolution.base

    # تطوير #209: اكتشاف تلقائي للأسهم المُرمَّزة (ماسي+ فقط)
    _is_perp_an = False
    if tier_an in ("diamond", "admin"):
        try:
            _is_perp_an = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            _is_perp_an = False

    # فحص الباقة للعملة المطلوبة
    _LARGE_CAPS = {"XLM","ICP","FIL","VET","EOS","XTZ","ALGO","HBAR","EGLD","WAVES","NEO","QTUM"}
    if tier_an in ("gold","diamond","admin") and symbol.upper() in _LARGE_CAPS:
        pass  # إصلاح #1020: عملات كبيرة مسموحة للذهبي+
    elif _is_perp_an and tier_an in ("diamond","admin"):
        pass  # تطوير #209: أسهم مُرمَّزة مسموحة لماسي+
    elif not is_symbol_allowed(symbol, tier_an):
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
            if _is_perp_an:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price_perp(symbol),
                        engine.data_layer.get_ohlcv_perp(symbol, 365),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
            else:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price(symbol),
                        engine.data_layer.get_ohlcv(symbol, "1d", 365),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
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
            retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 60)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or
                           price_d.get("price_change_percentage_24h") or 0)
        volume_24h = float(price_d.get("volume_24h") or 0)
        market_cap = float(price_d.get("market_cap") or 0)

        if price <= 0:
            # إصلاح #87/88: fallback لآخر إغلاق من candles قبل البحث في CoinGecko
            # (BGB وعملات مشابهة: get_price فشل لكن get_ohlcv نجح — /signal كان يعمل)
            if len(candles) >= 1:
                try:
                    _last_close = float(candles[-1].get("close") or 0)
                    if _last_close > 0:
                        price = _last_close
                        if change_24h == 0 and len(candles) >= 2:
                            _prev_close = float(candles[-2].get("close") or 0)
                            if _prev_close > 0:
                                change_24h = (price - _prev_close) / _prev_close * 100
                except Exception:
                    pass

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
                    candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=symbol)
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

        # جلب Derivatives + On-chain + 4H بالتوازي (المرحلة 2)
        try:
            _oi_data, _fund_data, _whale_data, _candles_4h, _onchain_an = await asyncio.wait_for(
                asyncio.gather(
                    engine.data_layer.get_open_interest(symbol),
                    engine.data_layer.get_funding_rate(symbol),
                    engine.data_layer.get_whale_ratio(symbol),
                    engine.data_layer.get_ohlcv_4h(symbol, 50),
                    engine.data_layer.get_onchain(),
                    return_exceptions=True,
                ), timeout=15.0
            )
        except Exception:
            _oi_data = _fund_data = _whale_data = _onchain_an = {}
            _candles_4h = []
        _oi_data     = _oi_data     if isinstance(_oi_data, dict)    else {}
        _fund_data   = _fund_data   if isinstance(_fund_data, dict)   else {}
        _whale_data  = _whale_data  if isinstance(_whale_data, dict)  else {}
        _candles_4h  = _candles_4h  if isinstance(_candles_4h, list)  else []
        _onchain_an  = _onchain_an  if isinstance(_onchain_an, dict)  else {}
        _oi_data    = _oi_data    if isinstance(_oi_data, dict)    else {}
        _fund_data  = _fund_data  if isinstance(_fund_data, dict)  else {}
        _whale_data = _whale_data if isinstance(_whale_data, dict) else {}

        # بناء السيناريوهات من البيانات الحقيقية قبل Groq
        _fib_for_ctx  = _calc_fibonacci(candles)
        _atr_for_ctx  = _calc_atr(candles)
        _scenarios_ctx = _build_scenarios_context(
            price    = price,
            atr_pct  = _atr_for_ctx,
            fib      = _fib_for_ctx,
            rsi      = rsi,
            is_bear  = is_bearish,
        )
        # دمج السيناريوهات مع candles_summary
        _full_context = f"{candles_summary}\n{_scenarios_ctx}".strip() if candles_summary else _scenarios_ctx

        try:
            analysis = await engine.news_engine.analyze_symbol(
                symbol=symbol, price=price, price_change_24h=change_24h,
                volume_24h=volume_24h, market_cap=market_cap, rsi=rsi,
                fear_greed=fear_val, regime_desc=regime_desc,
                candles_summary=_full_context)
            if not analysis or len(analysis.strip()) < 20:
                raise ValueError("تحليل فارغ")
        except Exception as _ae:
            logger.error(f"analyze_symbol ({symbol}): {_ae}")
            analysis = (f"📊 تحليل {symbol}\n"
                       f"السعر: {_fmt_price(price)} ({change_24h:+.2f}%)\n"
                       f"RSI: {int(rsi)} | السوق: {regime_desc}")

        change_sign = "+" if change_24h >= 0 else ""
        # حساب مستويات دخول/خروج من ATR
        atr_pct = _calc_atr(candles) / 100 if candles else 0.03
        rsi_lbl = _rsi_label(rsi)
        contradiction = _market_contradiction(rsi, fear_val, regime_desc)

        # levels_lines مُدمجة في الهيكل الاحترافي — لا حاجة لها هنا
        levels_lines = []

        # إصلاح #375/#390: استخدام signal_layer.generate الحقيقي
        fib_a  = _calc_fibonacci(candles)
        _atr_a = _calc_atr(candles)
        # إصلاح #34: onchain_data خاص بالعملة بدل {} الفارغة
        _onchain_a = await engine.data_layer.get_signal_enrichment(symbol, {})
        try:
            _sig_a = engine.signal_layer.generate(
                symbol=symbol, candles=candles,
                onchain_data=_onchain_a,
                news_sentiment=float(getattr(engine, "_last_news_sentiment", 0) or 0),
                backtest_win_rate=0.55,
                macro_data={"fear_greed": fear_val},
                regime=engine.regime_detector.detect(
                    candles, btc_dominance=float(btc_dom or 50), fear_greed=fear_val, symbol=symbol),
            )
            # إضافة بيانات Derivatives + 4H لـ technicals
            if hasattr(_sig_a, "technicals") and isinstance(_sig_a.technicals, dict):
                _sig_a.technicals["oi_data"]     = _oi_data
                _sig_a.technicals["fund_data"]   = _fund_data
                _sig_a.technicals["whale_data"]  = _whale_data
                _sig_a.technicals["atr_value"]   = round(_calc_atr(candles) * price / 100, 2)
                _sig_a.technicals["candles_4h"]   = _candles_4h
                _sig_a.technicals["onchain_data"]  = _onchain_an
                # BB من 4H إذا متاح
                if len(_candles_4h) >= 20:
                    _closes_4h = [float(c.get("close",0)) for c in _candles_4h]
                    _sig_a.technicals["bb_pos"] = _calc_bb_pos(_closes_4h)
        except Exception as _se:
            logger.debug(f"signal_layer in analyze: {_se}")
            class _AnalyzeSignal:
                confidence = 0.55
                direction  = "neutral"
                trade_type = "spot"
                technicals = {}
            _sig_a = _AnalyzeSignal()
            _scenario_fb = (
                "counter_trend_bounce" if rsi < 20 and fear_val < 25
                else "trend_continuation"
            )
            if rsi < 20:
                _sig_a.direction  = "long"
                _sig_a.confidence = 0.70
            elif rsi < 35:
                _sig_a.direction  = "long"
                _sig_a.confidence = 0.60
            elif rsi > 70:
                _sig_a.direction  = "short"
                _sig_a.confidence = 0.60
            # تعيين scenario في technicals للـ fallback
            _sig_a.technicals = {
                "scenario":      _scenario_fb,
                "scenario_ar":   (
                    "⚡ ارتداد مؤقت (Counter-trend)" if _scenario_fb == "counter_trend_bounce"
                    else "📉 استمرار الاتجاه الهابط" if is_bearish
                    else "📈 استمرار الاتجاه الصاعد"
                ),
                "scenario_warn": "⚡ ذروة بيع — scalp فقط، وقف صارم" if _scenario_fb == "counter_trend_bounce" else "📉 الاتجاه هابط",
                "max_size_pct":  0.12 if _scenario_fb == "counter_trend_bounce" else 0.20,
                "target_mult":   1.5  if _scenario_fb == "counter_trend_bounce" else 2.0,
                "oi_data": _oi_data, "fund_data": _fund_data,
                "whale_data": _whale_data, "onchain_data": _onchain_an,
                "atr_value": round(_calc_atr(candles) * price / 100, 2),
            }
        class _AnalyzeRegime:
            description_ar = regime_desc
            market_phase   = getattr(regime_obj, "market_phase", "") if "regime_obj" in dir() else (
                "Markdown" if is_bearish else "Markup")
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
            f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'} | حجم: {_fmt_volume(volume_24h)}" if volume_24h > 0 else f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'}",
            "━━━━━━━━━━━━━━━━━━",
            analysis,
        ]
        if levels_lines:
            parts.extend(levels_lines)
        # إصلاح #415/#448: لا contradiction إذا السيناريو واضح
        _sig_scenario = getattr(_sig_a, "technicals", {}).get("scenario", "")
        _hide_contradiction = _sig_scenario in (
            "counter_trend_bounce",   # السيناريو يوضح الوضع
            "trend_continuation",     # الرسالة واضحة بالفعل
        )
        if contradiction and contradiction not in analysis and not _hide_contradiction:
            parts += ["", contradiction]
        # إضافة Fibonacci
        if fib_lines_a:
            parts.extend(fib_lines_a)
        # إضافة Professional Block — مع إزالة التكرار
        parts += ["", "━━━━━━━━━━━━━━━━━━"]
        # تنظيف pro_block من أي سطور تكرر الهيدر
        pro_clean = _clean_md(pro_block_a)
        parts.append(pro_clean)
        # تطوير #188 (Phase 2): إلحاق فقرة الزوج الإضافية إن وُجدت
        _pair_addon_a = await build_pair_addon_lines(resolution, engine.data_layer)
        if _pair_addon_a:
            parts.extend(_pair_addon_a)
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

        # تطوير #222: اكتشاف تلقائي لنوع السوق من الصورة/الـcaption
        # نبحث عن علامات Futures في نص الـcaption أولاً
        _futures_keywords = (
            "PERP", "SWAP", "FUTURES", "PERPETUAL", "USDT-SWAP",
            "MARK PRICE", "FUNDING RATE", "OVERNIGHT", "-PERP", "USDT-M",
            "COIN-M", "DELIVERY", "QUARTERLY"
        )
        _caption_upper = caption.upper()
        _chart_is_futures = any(kw in _caption_upper for kw in _futures_keywords)
        # "Perp" tag في اسم الرمز نفسه (مثل MSTRUSDT Perp)
        if not _chart_is_futures and symbol:
            _chart_is_futures = any(kw in symbol.upper() for kw in ("PERP","SWAP","FUT"))

        analysis = await engine.news_engine.analyze_chart_image(
            image_data=bytes(image_bytes), symbol=symbol)

        # تطوير #222: إذا لم يُكتشَف النوع من الـcaption، نفحص نص التحليل نفسه
        if not _chart_is_futures:
            _analysis_upper = (analysis or "").upper()
            _chart_is_futures = any(kw in _analysis_upper
                                     for kw in ("PERP", "SWAP", "FUTURES", "PERPETUAL",
                                                 "MARK PRICE", "FUNDING RATE", "OVERNIGHT"))

        _mkt_label = "Futures/Perp" if _chart_is_futures else "Spot"
        sym_label = f" — {symbol}" if symbol else ""
        # إضافة header بمعلومات العملة (M#54)
        header_lines = [
            f"📊 *تحليل الشارت البصري{sym_label}*",
            f"🏪 نوع السوق: {'📈 Futures/Perp' if _chart_is_futures else '⚡ Spot'}",
            "━━━━━━━━━━━━━━━━━━",
        ]
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

    args      = context.args or []
    raw_arg   = args[0].upper() if args else "BTC"
    user_id_q = update.effective_user.id if update.effective_user else 0
    tier_q    = _sm.get_tier(user_id_q)

    # تطوير #221: سؤال نوع السوق (Spot/Futures) إن لم يُحدَّد مسبقاً
    _mkttype_qs = context.user_data.pop("_mkttype", None)
    if _mkttype_qs is None:
        sent_qs = await _ask_market_type(update, context, "quicksignal", raw_arg, tier_q)
        if sent_qs:
            return
    _use_futures_qs = (_mkttype_qs == "futures")

    msg    = await update.message.reply_text(
        f"🔍 جاري التحليل الأولي لـ {raw_arg}...")

    resolution = await resolve_symbol(raw_arg, tier_q, engine.data_layer)
    symbol  = resolution.base

    # تطوير #188 (Phase 2.5): الزوج المطلوب أساسي عند التوفر الكامل
    pair_primary = (resolution.is_pair_request and resolution.eligible_tier
                    and resolution.pair_available)
    quote = resolution.quote if pair_primary else "USDT"
    display_symbol = f"{symbol}/{quote}" if pair_primary else symbol

    # تطوير #209: اكتشاف تلقائي للأسهم المُرمَّزة (ماسي+ فقط)
    is_perp_stock = False
    if not pair_primary and tier_q in ("diamond", "admin"):
        try:
            is_perp_stock = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            is_perp_stock = False
    if is_perp_stock:
        display_symbol = f"{symbol} (Perp)"

    try:
        # M#119: timeout صارم لمنع التجمد
        try:
            if is_perp_stock:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price_perp(symbol),
                        engine.data_layer.get_ohlcv_perp(symbol, 250),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
            else:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price(symbol, quote),
                        engine.data_layer.get_ohlcv(symbol, "1d", 250, quote),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
        except asyncio.TimeoutError:
            await msg.edit_text(
                f"⏱️ انتهت مهلة تحليل *{display_symbol}*\n\n"
                f"جرّب: /quicksignal {raw_arg}",
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
            if is_perp_stock:
                retry_c = await engine.data_layer.get_ohlcv_perp(symbol, 60)
            else:
                retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 60, quote)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or price_d.get("price_change_percentage_24h") or 0)

        if price <= 0:
            await msg.edit_text(
                f"❌ لم أجد سعراً لـ {display_symbol}.\n"
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
                # تطوير #188 (Phase 2.5): مفتاح كاش regime مستقل عند pair_primary
                _regime_key = f"{symbol}{quote}" if pair_primary else symbol
                regime_obj  = engine.regime_detector.detect(
                    candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=_regime_key)
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
            f"📊 *التحليل الأولي — {display_symbol}*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: {_fmt_price(price, quote)} ({change_sign}{change_24h:.2f}%)",
            f"🌍 السوق: {regime_desc}",
            f"📈 RSI: {int(rsi)} | Fear & Greed: {fear_val}",
            "",
            f"🎯 *التوصية: {direction}*",
            "",
            "📍 *مناطق الدخول والخروج*",
            f"• نقطة الدخول: {_fmt_price(entry, quote)}",
            f"• هدف 1:       {_fmt_price(tp1, quote)} ({(tp1/price-1)*100:+.1f}%)",
            f"• هدف 2:       {_fmt_price(tp2, quote)} ({(tp2/price-1)*100:+.1f}%)",
            f"• وقف الخسارة: {_fmt_price(sl, quote)} ({(sl/price-1)*100:+.1f}%)",
        ]
        if support > 0 and resistance > 0:
            _sup_s, _res_s = _fmt_price_pair(support, resistance, quote)
            lines += [
                "",
                "🏗️ *المستويات الرئيسية*",
                f"• دعم:    {_sup_s}",
                f"• مقاومة: {_res_s}",
            ]
        if bear_buy_warning:
            lines.append(bear_buy_warning)
        lines += [
            "",
            "💡 للتحليل العميق الكامل: /analyze (ذهبي+)",
            "⚠️ هذا تحليل استرشادي — القرار للمستخدم",
        ]
        # تطوير #188 (Phase 2.5): فقرة إضافية في نهاية التقرير
        if pair_primary:
            # الزوج هو الأساس -> فقرة مرجعية صغيرة بسعر USDT
            _addon_q = await build_usdt_addendum(resolution, engine.data_layer)
        else:
            # USDT أساسي -> فقرة الزوج (ترقية/غير متوفر/الزوج كبديل مبسّط)
            _addon_q = await build_pair_addon_lines(resolution, engine.data_layer)
        if _addon_q:
            lines = lines[:-1] + _addon_q + ["", lines[-1]]

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

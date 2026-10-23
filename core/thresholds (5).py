"""
عتبات مشتركة بين كل الأوامر (/signal /analyze /quicksignal /planweek /planmonth).

السبب الجذري لكثير من التناقضات الظاهرة بين الأوامر: عتبات حادة بلا تسامح تقلبها فروق
سنتات بين أمرين متتاليين (وقف ضيق/غير ضيق، حجم، ...). كل أمر يستورد من هنا فقط.
"""

# الحد الأدنى للوقف = 1.5 × ATR، مع تسامح نسبي: لا تحذير ولا خصم إلا إذا كان
# الوقف أقل من (1.5 × ATR × SL_ATR_TOLERANCE).
SL_ATR_MIN_MULT = 1.5
SL_ATR_TOLERANCE = 0.97


def sl_below_min(sl_pct: float, atr_pct: float) -> bool:
    """هل الوقف أضيق فعلاً من الحد الأدنى الموصى (بعد التسامح)؟"""
    try:
        return atr_pct > 0 and sl_pct < SL_ATR_MIN_MULT * atr_pct * SL_ATR_TOLERANCE
    except TypeError:
        return False


def sl_multiple_below_min(sl_atr_multiple: float) -> bool:
    try:
        return sl_atr_multiple < SL_ATR_MIN_MULT * SL_ATR_TOLERANCE
    except TypeError:
        return False


# الحجم: مستويات متدرجة بدل قفزة 0.5x (0.47x خصم 5 مقابل 0.53x خصم 2)
VOL_WEAK = 0.5
VOL_VERY_WEAK = 0.3

# الهدف فوق المقاومة: يُحذَّر عند تجاوزه المقاومة بأكثر من 0.5%
TP_ABOVE_RESISTANCE_PCT = 0.5

# Funding المحايد: |f| < 0.01% (وحدة النسبة المئوية)
FUNDING_NEUTRAL_ABS_PCT = 0.01


# مؤشر الخوف والطمع: تصنيف واحد لكل الأوامر (كانت 4 مقاييس مختلفة: 25/45 و24/44/55/75 و60/80 ...)
# <25 خوف شديد | 25–44 خوف | 45–54 محايد | 55–74 جشع | ≥75 جشع شديد
def fear_greed_zone(value) -> tuple:
    """يعيد (التسمية العربية، الإيموجي) لقيمة المؤشر."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return ("غير متاح", "⚪")
    if v >= 75: return ("جشع شديد", "🤑")
    if v >= 55: return ("جشع", "😊")
    if v >= 45: return ("محايد", "😐")
    if v >= 25: return ("خوف", "😨")
    return ("خوف شديد", "😱")


def quote_decimals(value) -> int:
    """منازل عشرية لأسعار الأزواج المقوَّمة بـBTC/ETH: ≥5 أرقام معنوية (مصدر واحد لكل الأوامر)."""
    try:
        v = abs(float(value))
    except (TypeError, ValueError):
        return 6
    if v <= 0:        return 6
    elif v >= 1:      return 4
    elif v >= 0.01:   return 6
    elif v >= 0.001:  return 7
    elif v >= 1e-4:   return 8
    elif v >= 1e-6:   return 9
    else:             return 10


# ── batch30: نصوص موحّدة عبر الأوامر ─────────────────────────────────────
DISCLAIMER_LINE = "⚠️ هذا المحتوى استرشادي — القرار النهائي للمستخدم"


def synthetic_warning(sym: str, n_candles=None) -> str:
    """تحذير الأصل الاصطناعي بصياغة وموضع واحد في /quicksignal و/signal و/analyze.
    n_candles: عدد الشموع اليومية المتاحة؛ أقل من 120 → تنبيه قصر التاريخ."""
    t = (f"⚠️ *تحذير:* {sym} أصل اصطناعي (Synthetic) — "
         "السيولة محدودة ولا توجد حماية للمستثمرين.")
    try:
        if n_candles is not None and 0 < int(n_candles) < 120:
            t += (f"\n⚠️ *ملاحظة:* بيانات محدودة ({int(n_candles)} يوم) — "
                  "المؤشرات الفنية تقديرية وأقل موثوقية.")
    except Exception:
        pass
    return t


# ── batch32: حد توليد إشارة شراء/بيع (يستخدمه المحرك) ──────────────────────
# اتجاه الإشارة يبقى «محايد» ما لم تتجاوز الثقة هذا الحد، بينما «حد الدخول» لكل باقة 45–55%.
# أصل بثقة 55–64% يظهر لذلك «انتظار» بلا خطأ — النصوص توضح السبب بدل ترك الفارق غامضاً.
SIGNAL_DIRECTION_MIN_CONF = 0.65


def neutral_direction_reason(conf) -> str:
    """سبب «الاتجاه محايد» بصيغة موحّدة: الثقة دون حد توليد الإشارة."""
    try:
        c = float(conf)
        c = c * 100 if c <= 1 else c
        return (f"الاتجاه محايد: لا إشارة شراء/بيع قبل ثقة {SIGNAL_DIRECTION_MIN_CONF:.0%} "
                f"(الحالية {c:.0f}%)")
    except (TypeError, ValueError):
        return f"الاتجاه محايد: لا إشارة شراء/بيع قبل ثقة {SIGNAL_DIRECTION_MIN_CONF:.0%}"


# ── batch34: رموز مدعومة بسلعة (ذهب) — ليست عملات كريبتو تقليدية ─────────────
_COMMODITY_TOKENS = {"XAUT": "الذهب", "PAXG": "الذهب"}


def is_commodity_token(sym) -> bool:
    """batch35: هل الرمز مدعوم بسلعة (ذهب)؟ يُستخدم لإخفاء مقارنات الكريبتو (BTC/TVL) عنه."""
    return str(sym or "").upper().replace("-USDT", "") in _COMMODITY_TOKENS


def commodity_note(sym: str) -> str:
    """ملاحظة طبيعة الأصل للرموز المدعومة بسلعة؛ نص فارغ لغيرها."""
    name = _COMMODITY_TOKENS.get(str(sym or "").upper().replace("-USDT", ""))
    if not name:
        return ""
    return (f"ℹ️ {str(sym).upper()} رمز مدعوم ب{name} وليس عملة كريبتو تقليدية: Fear & Greed وTVL وهيمنة BTC "
            f"مرجعية للسوق الرقمي فقط، وحركته مرتبطة بسعر {name}.")

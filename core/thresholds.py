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

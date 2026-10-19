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

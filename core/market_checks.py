"""
core/market_checks.py — فحوص سوق مشتركة بين الواجهات (دفعة 25)

1) sharp_move_warning: تحذير الحركة الحادة 24 ساعة. العتبة 6% (كانت 10%) —
   جولة 46: DOT -6.02% و-8% سابقاً بلا أي تنبيه.
2) breadth_note: اتساع السوق. جولة 46: "اتجاه صاعد قوي" بينما 9 من 10 عملات هابطة.
"""
from typing import Iterable, Optional

SHARP_MOVE_PCT = 6.0
BREADTH_MIN_COINS = 5       # أقل عدد عملات لاعتبار القياس موثوقاً
BREADTH_WEAK_UP = 0.30      # نسبة الصاعدة ≤ هذا مع وصف صاعد → تنبيه
BREADTH_STRONG_UP = 0.70    # نسبة الصاعدة ≥ هذا مع وصف هابط → تنبيه


SHARP_ATR_MULT = 1.5        # batch67 (قرار رحال): الحركة حادة إن تجاوزت 6% أو 1.5×ATR


def sharp_move_level(change_24h, atr_pct=None) -> int:
    """batch67 (قرار رحال 2026-11-06 «الاثنان معاً»): تعريف واحد للحركة الحادة في كل الأوامر.
    0 = عادية · 1 = حادة (|تغير| > 6% أو ≥ 1.5×ATR) · 2 = حادة بالمعيارين معاً → «🚨 خطر الدخول مرتفع».
    بلا ATR معروف: العتبة الثابتة وحدها تعطي 1 (لا 🚨 لأن المعيار الثاني غير قابل للتحقق)."""
    try:
        c = abs(float(change_24h))
    except (TypeError, ValueError):
        return 0
    try:
        a = float(atr_pct or 0)
    except (TypeError, ValueError):
        a = 0.0
    fixed = c > SHARP_MOVE_PCT
    by_atr = a > 0 and c >= SHARP_ATR_MULT * a
    if fixed and by_atr:
        return 2
    return 1 if (fixed or by_atr) else 0


def sharp_move_warning(change_24h, atr_pct=None) -> str:
    """نص تحذير (فارغ إن لا حركة حادة). يُستخدم بنفس الصيغة في كل الواجهات."""
    lvl = sharp_move_level(change_24h, atr_pct)
    if not lvl:
        return ""
    c = float(change_24h)
    kind = "ارتفاع" if c > 0 else "انخفاض"
    try:
        a = float(atr_pct or 0)
    except (TypeError, ValueError):
        a = 0.0
    _x = f" (≈{abs(c) / a:.1f}×ATR)" if a > 0 else ""
    if lvl == 2:
        return f"🚨 {kind} حاد {abs(c):.1f}%{_x} في 24 ساعة — خطر الدخول مرتفع"
    return f"⚠️ {kind} حاد {abs(c):.1f}%{_x} في 24 ساعة — لا مطاردة؛ انتظر استقرار السعر"


def breadth_note(regime_desc: str, changes: Iterable, noun: str = "عملات") -> Optional[str]:
    """
    يُرجع تنبيهاً نصياً إذا ناقض اتساع السوق (نسبة العملات الصاعدة اليوم) وصف الاتجاه
    العام، وإلا None. لا يغيّر التصنيف نفسه — يضيف سياقاً فقط.
    """
    vals = []
    for c in changes or []:
        try:
            vals.append(float(c))
        except (TypeError, ValueError):
            continue
    n = len(vals)
    if n < BREADTH_MIN_COINS:
        return None
    up = sum(1 for v in vals if v > 0)
    frac = up / n
    d = str(regime_desc or "")
    if "صاعد" in d and frac <= BREADTH_WEAK_UP:
        return f"⚠️ اتساع السوق ضعيف: {up} من {n} {noun} فقط صاعدة اليوم رغم وصف الاتجاه — الصعود ضيق"
    if "هابط" in d and frac >= BREADTH_STRONG_UP:
        return f"⚠️ اتساع السوق يخالف الاتجاه: {up} من {n} {noun} صاعدة اليوم رغم وصف الهبوط"
    return None

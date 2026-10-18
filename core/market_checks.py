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


def sharp_move_warning(change_24h) -> str:
    """نص تحذير (فارغ إن لا حركة حادة). يُستخدم بنفس الصيغة في كل الواجهات."""
    try:
        c = float(change_24h)
    except (TypeError, ValueError):
        return ""
    if abs(c) <= SHARP_MOVE_PCT:
        return ""
    kind = "ارتفاع" if c > 0 else "انخفاض"
    return f"🚨 {kind} حاد {abs(c):.1f}% في 24 ساعة — خطر الدخول مرتفع"


def breadth_note(regime_desc: str, changes: Iterable) -> Optional[str]:
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
        return f"⚠️ اتساع السوق ضعيف: {up} من {n} عملات فقط صاعدة اليوم رغم وصف الاتجاه — الصعود ضيق"
    if "هابط" in d and frac >= BREADTH_STRONG_UP:
        return f"⚠️ اتساع السوق يخالف الاتجاه: {up} من {n} عملات صاعدة اليوم رغم وصف الهبوط"
    return None

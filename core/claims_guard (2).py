"""
core/claims_guard.py — حارس ادعاءات نص النموذج اللغوي في /analyze (دفعة 25، بند 5)

يكمل _sanitize_analysis_numbers (الذي يحذف مستويات سعرية غير موجودة في التقرير)
بحذف الجمل التي تُناقض بيانات التقرير نفسه:
  • الحجم: «حجم مرتفع» بينما نسبة الحجم < 0.8x، أو «حجم ضعيف» بينما > 1.5x.
  • التوصية: «ننصح بالشراء/البيع» بينما قرار التقرير انتظار أو عكس الجهة.
  • الاتجاه: «الاتجاه هابط» بينما وصف النظام صاعد (والعكس).
لا يُفرغ النص أبداً: إن حُذفت كل الجمل يُرجَع الأصل.
"""
import re
from typing import Tuple

_NEG = re.compile(r"(?:\bلا\b|\bلن\b|\bعدم\b|\bبدون\b|\bدون\b|\bتجنب\b)\s+(?:\S+\s){0,3}?")

_VOL_HIGH = re.compile(
    r"(?:(?:ارتفاع|زيادة|تزايد|قوة)\s+(?:في\s+)?(?:حجم|الحجم|أحجام)|"
    r"(?:حجم|أحجام)(?:\s+التداول)?\s+(?:\S+\s){0,3}?(?:مرتفع|قوي|عالٍ|عالي|كبير|يرتفع|مرتفعة))")
_VOL_LOW = re.compile(
    r"(?:(?:انخفاض|ضعف|تراجع|نقص)\s+(?:في\s+)?(?:حجم|الحجم|أحجام)|"
    r"(?:حجم|أحجام)(?:\s+التداول)?\s+(?:\S+\s){0,3}?(?:ضعيف|منخفض|متراجع|هزيل|ضئيل|منخفضة))")
_VOL_NORMAL = re.compile(
    r"(?:حجم|أحجام)(?:\s+التداول)?\s+(?:\S+\s){0,3}?(?:عادي|طبيعي|معتدل|متوسط|جيد|كافٍ)")
_LIKELY_UP = re.compile(
    r"(?:السيناريو|سيناريو)\s+(?:ال)?(?:صاعد|ارتداد|الارتداد|صعودي)\s+(?:\S+\s){0,2}?(?:الأرجح|أرجح|أكثر\s+ترجيح)"
    r"|(?:يبدو|يظهر)\s+(?:أن\s+)?(?:السيناريو\s+)?(?:ال)?(?:صاعد|ارتداد|الارتداد)\s+(?:\S+\s){0,2}?(?:الأرجح|أرجح|أكثر\s+ترجيح)")
_CROSS_UP = re.compile(
    r"(?:تجاوز|اخترق|فوق|كسر|يتخطى|تخطي)\s+(?:السعر\s+)?(?:مستوى\s+)?\$?(\d[\d,]*\.?\d*)")
_BUY_REC = re.compile(r"(?:ننصح|نوصي|يُنصح|ينصح|أنصح|أوصي|يفضل|الأفضل)\s+(?:\S+\s){0,3}?(?:بالشراء|بشراء|بالدخول|الدخول\s+شراء|بفتح\s+مركز\s+شراء)|(?:فرصة|إشارة)\s+شراء\s+(?:قوية|ممتازة|واضحة)")
_SELL_REC = re.compile(r"(?:ننصح|نوصي|يُنصح|ينصح|أنصح|أوصي|يفضل|الأفضل)\s+(?:\S+\s){0,3}?(?:بالبيع|ببيع|بالخروج|بفتح\s+مركز\s+بيع)|(?:فرصة|إشارة)\s+بيع\s+(?:قوية|ممتازة|واضحة)")
_TREND_DOWN = re.compile(r"الاتجاه(?:\s+العام)?\s+(?:\S+\s){0,2}?(?:هابط|سلبي|نزولي)")
_TREND_UP = re.compile(r"الاتجاه(?:\s+العام)?\s+(?:\S+\s){0,2}?(?:صاعد|إيجابي|صعودي)")


_FG_MENTION = re.compile(r"(?:الخوف\s*(?:و|ال)?\s*الطمع|Fear\s*(?:&|and)?\s*Greed)", re.I)
_FG_WORDS = ("جشع شديد", "خوف شديد", "جشع", "خوف", "محايد", "حيادي")


def _fg_conflict(sentence: str, zone_label: str) -> bool:
    """جملة تصف مؤشر الخوف والطمع بتصنيف يخالف تصنيف النظام الموحَّد."""
    if not _FG_MENTION.search(sentence):
        return False
    s = sentence
    # نحذف التصنيف الصحيح أولاً حتى لا تُحتسب كلمة «جشع» داخل «جشع شديد» وما شابه
    if zone_label and zone_label in s:
        return False
    return any(w in s for w in _FG_WORDS)


def guard_llm_claims(text: str, *, vol_ratio=None, direction: str = "",
                     decision_label: str = "", regime_desc: str = "",
                     price=None, fear_greed=None) -> Tuple[str, int]:
    """يُرجع (النص بعد الحذف، عدد الجمل المحذوفة)."""
    if not text:
        return text, 0
    try:
        wait = ("[WAIT]" in str(decision_label)) or str(direction).lower() not in ("long", "short")
        d = str(direction).lower()
        rd = str(regime_desc or "")
        try:
            vr = float(vol_ratio) if vol_ratio is not None else None
        except (TypeError, ValueError):
            vr = None
        try:
            px = float(price) if price is not None else None
        except (TypeError, ValueError):
            px = None
        try:
            from core.thresholds import fear_greed_zone
            _fg_zone = fear_greed_zone(fear_greed)[0] if fear_greed is not None else ""
        except Exception:
            _fg_zone = ""
        parts = re.split(r"(?<=[.؟!])\s+|\n+", text.strip())
        kept, dropped = [], 0
        for s in parts:
            if not s.strip():
                continue
            neg = bool(_NEG.search(s))
            bad = False
            if vr is not None:
                if vr < 0.8 and _VOL_HIGH.search(s) and not neg:
                    bad = True
                elif vr > 1.5 and _VOL_LOW.search(s) and not neg:
                    bad = True
            if not bad and not neg and vr is not None and vr < 0.7 and _VOL_NORMAL.search(s):
                bad = True   # حجم ضعيف رقمياً يُوصَف «عادي» → يناقض قسم Volume Profile
            if not bad and not neg and wait and _LIKELY_UP.search(s):
                bad = True   # ترجيح سيناريو صاعد بينما قرار التقرير انتظار
            if not bad and not neg and px:
                for _m in _CROSS_UP.finditer(s):
                    try:
                        _lv = float(_m.group(1).replace(",", ""))
                    except ValueError:
                        continue
                    # ادعاء "تجاوز/فوق مستوى X" بينما السعر الفعلي تحته (≤3%)
                    if px < _lv <= px * 1.03 and abs(_lv - px) / px > 0.0001:
                        bad = True
                        break
            if not bad and not neg:
                if _BUY_REC.search(s) and (wait or d == "short"):
                    bad = True
                elif _SELL_REC.search(s) and (wait or d == "long"):
                    bad = True
            if not bad:
                if "صاعد" in rd and "هابط" not in rd and _TREND_DOWN.search(s) and not neg:
                    bad = True
                elif "هابط" in rd and "صاعد" not in rd and _TREND_UP.search(s) and not neg:
                    bad = True
            if not bad and _fg_zone and _fg_conflict(s, _fg_zone):
                bad = True   # وصف مؤشر الخوف والطمع بتصنيف يخالف التصنيف الموحَّد
            if bad:
                dropped += 1
            else:
                kept.append(s)
        if not kept:
            return text, 0
        # نُعيد الجمل بمسافة واحدة (النص الأصلي كان فقرات؛ لا يهم التنسيق الداخلي هنا)
        return (" ".join(kept) if dropped else text), dropped
    except Exception:
        return text, 0

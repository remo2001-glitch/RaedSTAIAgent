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
_BUY_REC = re.compile(r"(?:ننصح|نوصي|يُنصح|ينصح|أنصح|أوصي|يفضل|الأفضل)\s+(?:\S+\s){0,3}?(?:بالشراء|بشراء|بالدخول|الدخول\s+شراء|بفتح\s+مركز\s+شراء)|(?:فرصة|إشارة)\s+شراء\s+(?:قوية|ممتازة|واضحة)")
_SELL_REC = re.compile(r"(?:ننصح|نوصي|يُنصح|ينصح|أنصح|أوصي|يفضل|الأفضل)\s+(?:\S+\s){0,3}?(?:بالبيع|ببيع|بالخروج|بفتح\s+مركز\s+بيع)|(?:فرصة|إشارة)\s+بيع\s+(?:قوية|ممتازة|واضحة)")
_TREND_DOWN = re.compile(r"الاتجاه(?:\s+العام)?\s+(?:\S+\s){0,2}?(?:هابط|سلبي|نزولي)")
_TREND_UP = re.compile(r"الاتجاه(?:\s+العام)?\s+(?:\S+\s){0,2}?(?:صاعد|إيجابي|صعودي)")


def guard_llm_claims(text: str, *, vol_ratio=None, direction: str = "",
                     decision_label: str = "", regime_desc: str = "") -> Tuple[str, int]:
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

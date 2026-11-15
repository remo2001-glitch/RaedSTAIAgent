"""
core/report_cards_b84.py — بطاقات قصيرة لتقارير الخطط (الدفعة 84، البند Y المرحلة 3 — قرار رحال 2026-11-15/16)

عرض فقط: البطاقة تُستخرج من نص التقرير الكامل نفسه الذي بناه الأمر (القيم المعروضة ذاتها، لا حساب جديد)،
والتقرير الكامل يبقى كما هو خلف زر «📖 التفاصيل الكاملة». دوال خالصة بلا شبكة — تُختبر مباشرة.
"""
import re
from typing import List, Tuple

FULL_WORDS_B84 = ("full", "كامل", "التفاصيل", "تفاصيل", "details")
DISC_B84 = "⚠️ هذا المحتوى استرشادي — القرار النهائي للمستخدم"


def split_full_b84(args) -> Tuple[list, bool]:
    """يفصل كلمة «full» عن بقية الوسائط (كي لا تُقرأ رمزاً): (الوسائط بدونها، هل طُلب التقرير الكامل)."""
    a = [str(x) for x in (args or [])]
    keep = [x for x in a if x.strip().lower() not in FULL_WORDS_B84]
    return keep, len(keep) != len(a)


def _sections(text: str) -> List[List[str]]:
    out, cur = [], []
    for ln in str(text or "").split("\n"):
        if ln.strip() == "":
            if cur:
                out.append(cur)
                cur = []
        else:
            cur.append(ln)
    if cur:
        out.append(cur)
    return out


def _starts(sec: List[str], *prefixes) -> bool:
    return bool(sec) and sec[0].lstrip().startswith(prefixes)


_DROP_SECTION = ("🔮", "📊 *السيناريوهات الـ", "📐 *مستويات الدعم", "⚠️ التنبؤ", "📊 *رؤية المؤسسات", "• الأساس:")


def _asset_line_b84(block: List[str]) -> List[str]:
    """كتلة أصل في /planweek ⇒ سطران: الحالة، ثم المستويات والتقلب. الكتلة «الجاهزة» تبقى كاملة."""
    head = block[0].strip()
    body = "\n".join(block[1:])
    if "جاهزة" in body and "🏷️ الجاهزية:" in body:
        return block
    sig = re.search(r"الإشارة: ([^|\n]+)\| الثقة: (\d+%)", body)
    rd = re.search(r"🏷️ الجاهزية: [^(\n]*\((\d)/4", body)
    atr = re.search(r"🌡️ التقلب: ATR ([\d.]+%)", body)
    fib = re.search(r"📊 Fib دعم: (\S+) \| مقاومة: (\S+)", body)
    l1 = head
    if sig:
        l1 += f" · {sig.group(1).strip()} · ثقة {sig.group(2)}"
    if rd:
        l1 += f" · جاهزية {rd.group(1)}/4"
    if re.search(r"(ارتفاع|انخفاض) حاد", body):
        l1 += " · ⚠️ حركة حادة"
    seg = []
    if fib:
        seg.append(f"دعم {fib.group(1)} · مقاومة {fib.group(2)}")
    if atr:
        seg.append(f"ATR {atr.group(1)}")
    out = [l1]
    if seg:
        out.append("   📊 " + " · ".join(seg))
    return out


def plan_week_card_b84(text: str, cmd: str = "planweek") -> str:
    secs = _sections(text)
    L: List[str] = []
    assets: List[str] = []
    for sec in secs:
        if _starts(sec, "💎 *"):
            assets += _asset_line_b84(sec)
            continue
        if any(_starts(sec, p) for p in _DROP_SECTION):
            continue
        if all(ln.lstrip().startswith("ℹ️") for ln in sec):          # شروح ثابتة ⇒ التفاصيل
            continue
        if _starts(sec, "⚠️ هذا المحتوى استرشادي", "🤖 رائد"):
            continue
        if _starts(sec, "📅 *خطة الأسابيع") or _starts(sec, "📅 *أحداث"):
            if assets:
                L += ["", "📋 *الأصول (الأعلى جاهزية/ثقة أولاً)*"] + assets
                assets = []
            L += [""] + [ln for ln in sec if not ln.lstrip().startswith("ℹ️ «خيار المحترف»")]
            continue
        L += ([""] if L else []) + sec
    if assets:
        L += ["", "📋 *الأصول (الأعلى جاهزية/ثقة أولاً)*"] + assets
    L += ["", "📖 *التفاصيل الكاملة* (لكل أصل: الاستراتيجية وشروط الدخول والتنبؤ): الزر أدناه", DISC_B84]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(L)).strip()


def plan_month_card_b84(text: str, cmd: str = "planmonth") -> str:
    secs = _sections(text)
    L: List[str] = []
    for sec in secs:
        if any(_starts(sec, p) for p in _DROP_SECTION):
            continue
        if _starts(sec, "⚠️ هذا المحتوى استرشادي", "🤖 رائد"):
            continue
        if all(ln.lstrip().startswith("ℹ️") for ln in sec):
            continue
        if _starts(sec, "🎯 *مصفوفة القرار"):
            keep = [sec[0]] + [ln for ln in sec[1:] if ("◀ الوضع الحالي" in ln or "📍 الآن" in ln or "↳ الحكم الحالي" in ln)]
            sec = keep if len(keep) > 1 else sec
        L += ([""] if L else []) + sec
    L += ["", "📖 *التفاصيل الكاملة* (التنبؤ 30 يوماً، مصفوفة القرار كاملة، المستويات): الزر أدناه", DISC_B84]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(L)).strip()

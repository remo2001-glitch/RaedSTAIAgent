"""سبب رفض Short بصيغة الحالة الفعلية (الدفعة 76) — قائمة ✅/❌ في /signal بصيغة المطلوب كما هي."""
from _util import chk
from core.short_eval_b68 import evaluate_short_b68 as ev


def run(**kw):
    base = dict(price=100.0, conf=0.27, rsi=40, atr_pct=3, nr=104.0,
                fib_levels={"a": 96.0, "b": 92.0, "c": 104.0}, regime_desc="🔴 اتجاه هابط",
                flags=["vol", "rsi"])
    base.update(kw)
    return ev(**base)

r = run(rsi=29)
chk("S1 ذروة البيع", r["reason"] == "RSI 29 < 30 — ذروة بيع، لا Short", r["reason"])
chk("S1b قائمة /signal بصيغة المطلوب", any(lbl.startswith("RSI 29 ≥ 30") and not c for lbl, c in r["checks"]))
r = run(price=300.0, nr=296.626)
chk("S2 السعر فوق المقاومة", r["reason"] == "السعر فوق المقاومة المثبتة (296.626) — السيناريو ملغى", r["reason"])
r = run(conf=0.40)
chk("S3 ميل غير كافٍ", r["reason"].startswith("ميل الهبوط 60% < 65%"), r["reason"])
r = run(regime_desc="⚡ تقلب عالٍ")
chk("S4 التصنيف", r["reason"] == "تصنيف السوق ليس هابطاً", r["reason"])
r = run(flags=["vol"])
chk("S5 التأكيدات (صيغة يعتمدها ترتيب «الأقرب»)", r["reason"] == "تأكيدات Short 1/4 (الحد الأدنى 2)", r["reason"])
r = run()
chk("S6 مؤهل بلا سبب", r["eligible"] and r["reason"] == "", r)
r = run(nr=0.0)
chk("S7 بلا مقاومة", r["reason"] == "لا مقاومة معروفة للدخول", r["reason"])

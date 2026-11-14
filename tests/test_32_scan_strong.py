"""تقرير المسح عند «إشارة قوية» في المسح السريع وسبب انتظار الشراء (الدفعة 77)."""
from _util import chk, src
import core.auto_trader_b68 as AT

W = AT._wait_reason_b77
chk("O2a سبب الحجم 0% الفعلي", W({"size_rule": "0% — تأكيدات 1/4 — غير كافية (الحد الأدنى 2)"}, 0.67) == "تأكيدات 1/4 — غير كافية (الحد الأدنى 2)")
chk("O2b وسم القرار عند غياب سبب الحجم", W({"size_rule": "", "decision_label": "⏳ انتظار — سوق هابط قائم"}, 0.6) == "⏳ انتظار — سوق هابط قائم")
chk("O2c بديل محايد بلا «دون حد الدخول»", W({}, 0.67) == "حكم /signal «انتظار» (الثقة 67%)")
chk("O2d لا Markdown في السبب", "*" not in W({"size_rule": "0% — *اتجاه هابط قائم*"}, 0.5))
sm = {"users": 1, "universe": 33, "scanned_syms": ["X"] * 33, "long": 0, "short": 0}
AT._near_add_b73(sm, "SOL", "شراء", 0.67, W({"size_rule": "0% — تأكيدات 1/4 (الحد الأدنى 2)"}, 0.67))
AT._near_add_b73(sm, "XLM", "Short", 0.66, "تأكيدات Short 1/4 (الحد الأدنى 2)")
AT._near_add_b73(sm, "BZ", "Short", 0.75, "تأكيدات Short 0/4 (الحد الأدنى 2)")
t = AT.scan_line_b68(sm).split("🔎")[1]
chk("O2e الشراء يُرتَّب بتأكيداته", t.index("SOL") < t.index("XLM") < t.index("BZ"), t)

e = src("core/raed_engine.py")
chk("O1a لا «autotrade نشط ✅»", "فرص قوية — autotrade نشط" not in e)
chk("O1b لا «/vtrades لمتابعة الصفقات»", "/vtrades لمتابعة الصفقات" not in e)
chk("O1c وسم «للعلم فقط» في فرع الإشارة القوية", "المسح السريع (للعلم فقط، لا ينفّذ): إشارة ≥" in e)
chk("O1d سبب عدم الفتح", "🤖 لم يُفتح — حكم /signal: " in e)
chk("O1e سطر التنبيه في الفرع", "_DISC77" in e)

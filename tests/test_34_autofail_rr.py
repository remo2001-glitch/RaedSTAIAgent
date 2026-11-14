"""سبب AUTO-FAIL الفعلي وتقريب R/R للأسفل دون 1، وعدم قصّ السبب (الدفعة 79)."""
from _util import chk
import core.auto_trader_b68 as AT
from core.signal_scorer import evaluate_signal, SignalInput

R = AT.autofail_reason_b79
chk("Q1a 0.996 لا تُعرض 1.00", R({"rr": 0.996}) == "بنية الصفقة فاشلة (AUTO-FAIL): R/R المرجّح 1:0.99 < 1:1 — الأهداف قريبة مقارنة بالوقف", R({"rr": 0.996}))
chk("Q1b R/R من نص الحكم", "1:0.97 < 1:1" in R({"verdict_reason": "❌ *AUTO-FAIL* | G3: R/R = 1:0.97 < 1:1 — لا حافة إحصائية"}))
chk("Q1c البوابة G2", "خطة الصفقة ناقصة" in R({"verdict_reason": "❌ *AUTO-FAIL* | G2: خطة صفقة ناقصة (entry/SL/invalidation)"}))
chk("Q1d البوابة G5", "حديث الإدراج" in R({"verdict_reason": "❌ *AUTO-FAIL* | G5: أصل حديث الإدراج"}))
chk("Q1e تنسيق R/R", AT.rr_fmt_b79(0.996) == "0.99" and AT.rr_fmt_b79(1.08) == "1.08" and AT.rr_fmt_b79(0.85) == "0.85")
sm = {}
long_why = R({"rr": 0.996})
AT._near_add_b73(sm, "SOL", "شراء", 0.67, long_why)
chk("Q2 السبب كاملاً بلا قص", sm["near"][0]["why"] == long_why and long_why.endswith("مقارنة بالوقف"), sm)
v = evaluate_signal(SignalInput(rr_ratio=0.996))
chk("Q1f تذييل /signal G3 بلا 1.00", v["verdict"] == "AUTO-FAIL" and "1:0.99 < 1:1" in v["header"], v.get("header"))
v2 = evaluate_signal(SignalInput(rr_ratio=1.0))
chk("Q1g R/R = 1.00 تمر البوابة", v2["verdict"] != "AUTO-FAIL")

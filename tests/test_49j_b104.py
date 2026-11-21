"""الدفعة 104 (رحال 2026-11-21 08:46 «1. نعم نفذه 2. نفذه 3. أ»): AF1 تنبيه «فرصة تقترب» = قائمة «ما الذي يغيّر الحكم؟»؛
AF2 «لماذا؟» يذكر الهبوط القائم مع البنية الفاشلة؛ AF3 (أ) نص الهبوط الحاد يطابق ما يحدث فعلاً. عرض/تنبيه فقط."""
from _util import chk, src
import handlers.analysis as A
import core.real_alert_b103 as RA

# ── AF3 (أ) ───────────────────────────────────────────────────
ln = A.deep_drop_line_b86(-27.6)
chk("AF3a الرأس: الحكم «انتظار» كحد أقصى والثقة الأساسية قبل التأكيدات (لا «مقيّدة ≤ 49%» ولا «30 يوماً»)",
    ln == "⚠️ هبوط حاد −27.6% تحت EMA50 — الحكم «انتظار» كحد أقصى؛ الثقة الأساسية ≤ 49% قبل رفع التأكيدات", ln)
bl = A.deep_drop_block_b86(-27.6, 6.8, 18)
chk("AF3b التفاصيل: Fibonacci على نافذته المعتادة (60 يوماً)", "Fibonacci على نافذته المعتادة (آخر 60 يوماً)" in bl
    and "آخر 30 يوماً" not in bl, bl)
s = src("handlers/analysis.py")
chk("AF3c النافذة الفعلية 60 يوماً (الحساب الأخير في /signal)", "def _calc_fibonacci(candles: list, lookback: int = 60" in s
    and "        fib        = _calc_fibonacci(candles)\n" in s)
chk("AF3d سياق النموذج في /analyze بلا «مقيّدة ≤ 49%»", "الثقة مقيّدة ≤ 49%" not in s)

# ── AF2 ───────────────────────────────────────────────────────
chk("AF2 «لماذا؟» يضيف الهبوط القائم للبنية الفاشلة", 'if d.get("bear_confirmed") and "اتجاه هابط قائم" not in why_l:' in s
    and 'why_l += "؛ اتجاه هابط قائم، لا شراء"' in s)

# ── AF1 ───────────────────────────────────────────────────────
L1 = RA.near_one_long_b103
base = {"verdict": "AUTO-FAIL", "verdict_reason": "G3 — R/R = 1:0.96", "direction": "long", "confidence": 0.70, "n_conf": 2}
chk("AF1a كل الشروط مكتملة عدا R/R ⇒ شرط واحد", L1(base, "x", 0).startswith("R/R المرجّح 1:0.96"))
chk("AF1b هبوط قائم ⇒ شرطان (لا تنبيه)", L1({**base, "bear_confirmed": True}, "x", 0) == "")
chk("AF1c تأكيدات 1/4 ⇒ شرطان (لا تنبيه)", L1({**base, "n_conf": 1}, "x", 0) == "")
chk("AF1d حالة ETH 08:41 (ثقة 58%، هبوط قائم) ⇒ لا", L1({**base, "confidence": 0.58, "bear_confirmed": True, "n_conf": 1}, "x", 0) == "")
chk("AF1e الهبوط القائم محفوظ مع قرار /signal", '"bear_confirmed": bool(meta.get("bear_confirmed_b104"))' in s
    and '_sm_sig["bear_confirmed_b104"] = bool(_bear_confirmed_b56(regime)[0])' in s)
rec = A._save_signal_decision_b58(1, "ETH", "futures", {"bear_confirmed_b104": True, "direction": "long"}, {}, save=False)
chk("AF1f السجل يحمل bear_confirmed", rec.get("bear_confirmed") is True)

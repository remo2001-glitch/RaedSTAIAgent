"""BB1-ب (نص الهبوط الحاد بدل «بيانات غير موثوقة») وBB2 (كل ما ينقص Short) — الدفعة 86، عرض فقط."""
from _util import chk, src
import handlers.analysis as A
from core.short_eval_b68 import evaluate_short_b68 as ev

chk("BB1a سطر الهبوط الحاد", A.deep_drop_line_b86(-24.6) == "⚠️ هبوط حاد −24.6% تحت EMA50 — الحكم «انتظار» كحد أقصى؛ الثقة الأساسية ≤ 49% قبل رفع التأكيدات")
b = A.deep_drop_block_b86(-24.6, 6.8, 20)
chk("BB1b كتلة التفاصيل تذكر الحماية", "⚠️ *هبوط حاد تحت EMA50*" in b and "الثقة الأساسية ≤ 49% قبل رفع التأكيدات" in b and "«انتظار» كحد أقصى" in b and "RSI=20" in b)
s = src("handlers/analysis.py")
chk("BB1c فقط حين يكون السبب الوحيد EMA50 من شموع كاملة", "_ema50_sig_corrupted and len(candles or []) >= 50" in s
    and "not (atr_pct > 25 or rsi < 5 or _pve50_chk < -50)" in s and "not (_atr_an > 25 or rsi < 5 or _pve50_an < -50)" in s)
chk("BB1d المنطق كما هو", "_data_corrupted = (atr_pct > 25 or rsi < 5 or _pve50_chk < -50 or _ema50_sig_corrupted)" in s
    and "signal.confidence = min(signal.confidence, 0.49)" in s and "price_cap_mult=1.5" in s)

r = ev(price=71.0, conf=0.41, rsi=20, atr_pct=6.8, nr=75.94, fib_levels={"a": 67.33, "b": 62.76, "c": 75.94},
       regime_desc="🔴 اتجاه هابط", flags=[])
chk("BB2a كل الشروط الفاشلة", r["fails_b86"] == ["ميل الهبوط 59% < 65% — الجهة الهابطة غير مؤكدة", "RSI 20 < 30 — ذروة بيع، لا Short",
                                                 "تأكيدات Short 0/4 (الحد الأدنى 2)"] and r["reason"] == r["fails_b86"][0], r.get("fails_b86"))
meta = {"direction": "neutral", "confidence_pct": 41, "confirmations_met": 0, "short_b68": r, "rsi": 20, "vol_ratio": 1.5,
        "atr_pct": 6.8, "entry_agg": 67.33}
c = A.signal_card_b82({"sym": "SOL", "price": 71.3, "meta": meta, "score": {}, "rec": {"wait": True, "size_rule": "0% — اتجاه هابط قائم، لا شراء"},
                       "regime_desc": "🔴 اتجاه هابط", "bear_confirmed": True, "futures": True, "entry_threshold": 55})
chk("BB2b «لماذا» يذكر كل الموانع", "• Short: ⏳ ميل الهبوط 59% < 65% — الجهة الهابطة غير مؤكدة؛ RSI 20 < 30 — ذروة بيع، لا Short؛ تأكيدات Short 0/4 (الحد الأدنى 2)" in c, c)
chk("BB2c «ما الذي يغيّر» يجمعها", "• Short: ميل هبوط ≥ 65% (جهة هابطة مؤكدة) + خروج RSI من ذروة البيع (≥ 30، حالياً 20) + تأكيدان إضافيان — حجم ≥1.5x" in c, c)
r1 = ev(price=100.0, conf=0.27, rsi=40, atr_pct=3, nr=104.0, fib_levels={"a": 96.0, "b": 92.0, "c": 104.0},
        regime_desc="🔴 اتجاه هابط", flags=["vol"])
chk("BB2d مانع واحد كما كان", r1["fails_b86"] == ["تأكيدات Short 1/4 (الحد الأدنى 2)"])

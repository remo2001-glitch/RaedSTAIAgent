"""الدفعة 105 (رحال 2026-11-21 09:12 «1. سوف ابقى الصفقه المفتوحه للقياس. نعم اختياري أ 2. نفذها»):
AF4 (أ) حكم البنية «انتظار — مراقبة فقط» يمنع الدخول (البطاقة، التداول الآلي، /trade الحقيقي، تنبيه 🔔)؛
AG1 تنبيه الحساب الحقيقي بسعر تنفيذ القياس ووقفه؛ AG2 USDG عملة مستقرة."""
import time
from _util import chk, src
import core.auto_trader_b68 as AT
import handlers.analysis as A

# ── AF4: البوابة ──────────────────────────────────────────────
btc = {"verdict": "WATCH ONLY", "watch_reason": "هبوط حاد تحت EMA50 أو بيانات غير موثوقة/شموع غير كافية",
       "direction": "long", "size_pct": 20, "sl": 51738.6, "tp1": 62314.99, "tp2": 64222.26, "atr_pct": 4.8, "confidence": 0.55}
ok, why, rr = AT.long_gate_b68(btc, 55755.0)
chk("AF4a حالة BTC 09:04 (هبوط حاد، WATCH ONLY) ⇒ لا دخول آلي", not ok and why.startswith("جودة البنية «انتظار — مراقبة فقط»: هبوط حاد"), why)
ok2, _, _ = AT.long_gate_b68(dict(btc, verdict="CONDITIONAL"), 55755.0)
chk("AF4b الحالة نفسها بحكم CONDITIONAL ⇒ الدخول كما كان", ok2)
chk("AF4c لقطة الدخول توسم البوابة الجديدة", AT.entry_snapshot_b80({}, 1.0).get("e_gate_rev") == "b105")

# ── AF4: سبب الحكم من تقييم البنية ───────────────────────────────
sc = {"verdict": "WATCH ONLY", "score": 78, "reasons": ["أ: …", "ط: هبوط حاد تحت EMA50 أو بيانات غير موثوقة/شموع غير كافية — الحكم مُقيَّد بحالة «انتظار»"]}
chk("AF4d السبب من قيد «مُقيَّد بحالة «انتظار»»", A._watch_reason_b105(sc) == "هبوط حاد تحت EMA50 أو بيانات غير موثوقة/شموع غير كافية", A._watch_reason_b105(sc))
chk("AF4e بلا قيد ⇒ الدرجة دون 55", A._watch_reason_b105({"verdict": "WATCH ONLY", "score": 48, "reasons": []}) == "درجة البنية 48/100 دون 55")
chk("AF4f حكم آخر ⇒ لا سبب", A._watch_reason_b105({"verdict": "PASS", "score": 80}) == "")
rec = A._save_signal_decision_b58(1, "BTC", "futures", {"direction": "long"}, sc, save=False)
chk("AF4g السبب محفوظ مع القرار", rec.get("watch_reason", "").startswith("هبوط حاد"))

# ── AF4: البطاقة ───────────────────────────────────────────────
d = {"sym": "BTC", "price": 55803.5, "mkt_tag": " · 📉 Futures", "futures": True, "bear_confirmed": True, "entry_threshold": 55,
     "meta": {"confidence_pct": 55, "confirmations_met": 2, "direction": "long", "entry_agg": 57695.3, "pro_sl": 53450.94,
              "tp1_v": 62314.99, "tp2_v": 64222.26},
     "score": sc, "rec": dict(btc, wait=False)}
card = A.signal_card_b82(d)
chk("AF4h البطاقة: «انتظار» لا «دخول شراء» والسبب ظاهر", "الحكم: انتظار" in card and "دخول شراء" not in card
    and "• شراء: ⏳ جودة البنية «انتظار — مراقبة فقط»: هبوط حاد" in card and "اتجاه هابط قائم" in card, card[:600])
chk("AF4i البطاقة بلا «خطة الشراء» ولا «متابعة الصفقة»", "خطة الشراء" not in card and "متابعة الصفقة" not in card, card)

# ── AF4: /trade الحقيقي ───────────────────────────────────────
import handlers.trade as TR
g = TR._signal_gate_b58(dict(btc, mkt="spot", ts=time.time()), "BTC", 55755.0)
chk("AF4j /trade الحقيقي يرفض «انتظار — مراقبة فقط»", g.startswith("⏳ *BTC* — لا تنفيذ: جودة البنية «انتظار — مراقبة فقط»"), g)

# ── AG1 / AG2 ──────────────────────────────────────────────────
s = src("core/auto_trader_b68.py")
chk("AG1a التنبيه بسعر تنفيذ القياس (السعر + الانزلاق) ووقفه", "_epx105 = px * (1 + AUTO_SLIP)" in s
    and '"px": px * (1 - AUTO_SLIP), "rr": rr2' in s)
chk("AG1b نص الدخول يوضح ذلك", "(السعر الحالي + انزلاق 0.1% — نفس دخول صفقة القياس)" in src("core/real_alert_b103.py"))
epx = 55755.0 * 1.001
_sl = min(53450.94, epx * (1 - 1.5 * 4.8 / 100))   # وقف /signal $53,450.94 وقاعدة 1.5×ATR من سعر التنفيذ
chk("AG1c حالة BTC: الوقف في التنبيه ≈ وقف البطاقة $51,790 (لا $51,738)", abs(_sl / 51790.33 - 1) < 0.0001, _sl)
chk("AG2a USDG عملة مستقرة", "USDG" in AT.STABLES)
chk("AG2b المسح السريع يستعمل القائمة نفسها", "from core.auto_trader_b68 import STABLES as _ST105" in src("core/raed_engine.py"))

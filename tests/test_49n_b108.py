"""الدفعة 108 (رحال 2026-11-21 21:28 «نفذها جميعا الان»): AD1، AE1، AH1، AJ2، AK1 — عرض/تنبيه فقط، لا تغيير في المنطق."""
import asyncio
from _util import chk, src
import handlers.analysis as A
import core.real_alert_b103 as RA
import core.auto_trader_b68 as AT

# ── AE1 ───────────────────────────────────────────────────────
leg = {"raw": {"vwap": "83721.4"}, "levels": {"vwap": 83721.4}}
_m = A.chart_missed_levels_b91("2. الدعم عند VWAP $83,721.4 مهم", {**leg})
chk("AE1a «$83,721.4» في التحليل = VWAP 83721.4 (لا «لم يذكرها»)", "83721.4" not in _m, _m)
s = src("handlers/analysis.py")
chk("AE1b المقارنة بلا فاصلة و$", '_flat108 = analysis.replace(",", "").replace("$", "")' in s)

# ── AH1 ───────────────────────────────────────────────────────
F = AT.size_reason_ar_b108
chk("AH1a NORMAL", F("(NORMAL) — حجم 10–20%") == "حجم عادي (10–20% حسب /signal)", F("(NORMAL) — حجم 10–20%"))
chk("AH1b LOW بالأقواس المربعة", F("[LOW] — حجم 5–10% فقط") == "حجم منخفض (5–10% حسب /signal) فقط", F("[LOW] — حجم 5–10% فقط"))
chk("AH1c HIGH", F("[HIGH] — حجم 25%") == "حجم مرتفع (25% حسب /signal)")
chk("AH1d WAIT", F("[WAIT] — لا دخول").startswith("انتظار"))
chk("AH1e نص غير موسوم يبقى كما هو", F("اختراق مقاومة") == "اختراق مقاومة")
sa = src("core/auto_trader_b68.py")
chk("AH1f بطاقة الفتح «سبب الدخول» بالعربية", 'size_reason_ar_b108(_md_safe_qq1(extra.get("e_decision")))' in sa)

# ── AK1 ───────────────────────────────────────────────────────
_orig = A._get_market_hours_warning
A._get_market_hours_warning = lambda sym, *a, **k: ("⏰ *تنبيه:* NVDA — سوق NYSE/NASDAQ مغلق (عطلة نهاية الأسبوع)\nسطر ثان" if sym == "NVDA" else "")
ln = RA.market_closed_line_b108("NVDA")
chk("AK1a سطر مختصر", ln == "🕒 NVDA — سوق NYSE/NASDAQ مغلق (عطلة نهاية الأسبوع) — سيولة أقل واحتمال فجوة سعرية عند الافتتاح", ln)
chk("AK1b كريبتو ⇒ لا سطر", RA.market_closed_line_b108("BTC") == "")
one = RA.near_combined_text_b108([{"sym": "NVDA", "side": "long", "px": 180.0, "score": 0.6, "missing": "R/R"}], None)
chk("AK1c تنبيه «تقترب» لسهم والسوق مغلق", one.startswith("⏳ *فرصة تقترب* — NVDA") and "🕒 NVDA" in one
    and one.index("🕒") < one.index("🔕"), one)
sr = src("core/real_alert_b103.py")
chk("AK1d تنبيه الفرصة يحمل السطر", '_mh108 = market_closed_line_b108(o.get("sym"))' in sr)

# ── AJ2 ───────────────────────────────────────────────────────
items = [{"sym": "NVDA", "side": "long", "px": 180.0, "score": 0.6, "missing": "R/R 1:0.9"},
         {"sym": "SOL", "side": "short", "px": 130.0, "score": 0.55, "missing": "تأكيد 2/3"}]
txt = RA.near_combined_text_b108(items, {"total": 0, "name": "OKX"})
chk("AJ2a رسالة واحدة بسطر لكل أصل", txt.startswith("⏳ *فرص تقترب (2)*") and "• *NVDA* 🟢 Long" in txt
    and "• *SOL* 🔴 Short" in txt and "/signal SOL" in txt and "$0.00" in txt and "🕒 NVDA" in txt, txt)
A._get_market_hours_warning = _orig

SENT = []
async def _send(t, user_id=None, parse_mode=None):
    SENT.append(t); return True
RA.owner_uid = lambda: 7
class _KV:
    def __init__(self): self.kv = {}
    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = str(v)
_F = _KV()
RA._sm = lambda: _F
async def _bal(e, u): return None
RA.real_balance_b103 = _bal
n = asyncio.run(RA.send_near_b103(None, _send, [dict(i, sym=i["sym"] + "X108") for i in items]))
chk("AJ2b إرسال واحد للدفعة وتسجيل الكل", n == 2 and len(SENT) == 1 and SENT[0].startswith("⏳ *فرص تقترب (2)*"), (n, SENT))
n2 = asyncio.run(RA.send_near_b103(None, _send, [dict(i, sym=i["sym"] + "X108") for i in items]))
chk("AJ2c التبريد لكل أصل بعد الإرسال", n2 == 0 and len(SENT) == 1)
async def _fail(t, user_id=None, parse_mode=None):
    return False
n3 = asyncio.run(RA.send_near_b103(None, _fail, [dict(items[0], sym="ZZY108")]))
n4 = asyncio.run(RA.send_near_b103(None, _send, [dict(items[0], sym="ZZY108")]))
chk("AJ2d فشل الإرسال ⇒ لا تسجيل (AI1)", n3 == 0 and n4 == 1)
ss = src("core/scheduler.py"); se = src("core/raed_engine.py")
chk("AJ2e يُرسل بعد تقرير المسح (finally) والمحرك يؤجّل", "for _pf108 in list(getattr(self, \"_post_scan_fns_b108\"" in ss
    and ss.index("await self._safe_send(report)") < ss.index("_post_scan_fns_b108")
    and "self.scheduler._post_scan_fns_b108 = [self._flush_near_b108]" in se
    and 'engine._near_pending_b108 = (send_fn, _nr)' in sa)

# ── AD1 ───────────────────────────────────────────────────────
st = src("handlers/trading.py")
chk("AD1a السطر بعد «مراكز Futures مفتوحة»", st.index("• مراكز Futures مفتوحة:") < st.index("• إجمالي لو أُغلقت المراكز الآن"))
chk("AD1b الحساب صريح: صافي /vfutures + رسوم الفتح", "= صافي /vfutures" in st and "+ رسوم الفتح ${_fo108:,.2f}" in st
    and "يطابق صافي /vfutures)" not in st)

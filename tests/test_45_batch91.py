"""الدفعة 91 (رحال 2026-11-19 «نفذ جميع الإصلاحات المعلقة»): II1–II5 (/chart)، HH1 (الأقرب للدخول)، GG3 (الاسترجاع)،
EE (خطأ الرصيد الصريح + حفظ سوق /regime)، DD1، DD2 — عرض/بيانات فقط، لا يمس قرار القياس."""
import asyncio
import json
import types
from _util import chk, src
import handlers.analysis as A
import core.chart_vision as CV

ANT = """0- نوع السوق: Spot
2- مستويات الدعم والمقاومة:
الدعم: 85.94 (الحد السفلي للبولنجر)
المقاومة: 109.18 (الحد العلوي للبولنجر)
6- نقطة الدخول المقترحة بالسعر الدقيق (مشروطة بتأكيد):
109.18
7- وقف الخسارة بالسعر الدقيق:
92.91
8- هدف الربح الأول والثاني بالسعر الدقيق:
الهدف الأول: 115.12
الهدف الثاني: 130.80
9- مستوى المخاطرة (نسبة مئوية من رأس المال):
2%"""

# ── II1 ─────────────────────────────────────────────────────────
it = A._chart_item_lines_b51(ANT)
chk("II1a الوقف 92.91 في سطر مستقل لا يُقرأ بنداً", "92.91" in it.get("7", ""), it)
txt, rr1, stop = A._chart_rr_block_b53(ANT, "USDT", 95.67, ())
chk("II1b R/R يُحسب ويُحذَّر دون 1:1", "الهدف 1 = 0.37:1" in txt and "R/R دون 1:1" in txt and abs(rr1 - 0.365) < 0.01, txt)
it2 = A._chart_item_lines_b51("6. 109.18\n7- وقف\n0.99\n8) 1.2\n9 - 2%")
chk("II1c «6. 109.18» و«8) 1.2» بنود؛ «0.99» سعر", it2 == {"6": "109.18", "7": "وقف\n0.99", "8": "1.2"}, it2)

# ── II2/II3 قراءة الشريط ─────────────────────────────────────────
leg = CV.parse_legend("SYMBOL: ANTUSDT\nMARKET: العقود الدائمة\nPRICE: 95.67\nVWAP: 99.83\nBOLL_MID: 109.18\n"
                      "BOLL_UB: 132.42\nBOLL_LB: 85.94\nSUPERTREND_RED: 117.80")
chk("II3a الزوج الملتصق ANTUSDT", leg.get("SYMBOL") == ("ANT", "USDT"), leg.get("SYMBOL"))
chk("II3b BTC-USDT-SWAP و OKB/BTC", CV.parse_legend("SYMBOL: BTC-USDT-SWAP").get("SYMBOL") == ("BTC", "USDT")
    and CV.parse_legend("SYMBOL: OKB/BTC").get("SYMBOL") == ("OKB", "BTC"))
chk("II2a وسم السوق", leg.get("MARKET") == "PERP" and CV.parse_legend("MARKET: التداول الفوري").get("MARKET") == "SPOT")
chk("II2b MARKET في مفاتيح النسخ والتعليمات", "MARKET" in CV.LEGEND_KEYS and "العقود الدائمة" in CV.legend_prompt())
chk("II2c بند «0- نوع السوق» يتبع النوع المعتمد", A.chart_market_line_b91(ANT, True).splitlines()[0] == "0- نوع السوق: Futures/Perp")
s = src("handlers/analysis.py")
chk("II2d كلمات «العقود الدائمة» + أولوية الوسم", '"العقود الدائمة", "العقود الآجلة", "عقد دائم"' in s
    and '_leg_mkt_b91 == "PERP" and not _is_spot_explicit' in s)
chk("II3c التصريح بغياب القراءة الآلية/الرمز", "تعذّرت القراءة الآلية للشريط العلوي" in s and "لم يُقرأ رمز الأصل من الصورة" in s)

# ── II4/II5 ─────────────────────────────────────────────────────
t4, n4 = A.chart_level_labels_b91(ANT, leg)
chk("II4a «109.18 (الحد العلوي)» ⇒ خط الوسط", n4 == 1 and "المقاومة: 109.18 (خط الوسط للبولنجر)" in t4
    and "الدعم: 85.94 (الحد السفلي للبولنجر)" in t4, t4)
chk("II4b بلا شريط لا تغيير", A.chart_level_labels_b91(ANT, {}) == (ANT, 0))
m5 = A.chart_missed_levels_b91(t4, leg)
chk("II5 المستويات غير المذكورة", "الحد العلوي للبولنجر 132.42" in m5 and "VWAP 99.83" in m5 and "Supertrend (أحمر) 117.80" in m5
    and "85.94" not in m5, m5)

# ── HH1 ─────────────────────────────────────────────────────────
import core.auto_trader_b68 as AT
sm = {}
AT._near_add_b73(sm, "BZ", "Short", 0.73, "تأكيدات Short 1/4 (الحد الأدنى 2)", fails=["تأكيدات Short 1/4 (الحد الأدنى 2)"])
AT._near_add_b73(sm, "HYPE", "Short", 0.70, "x", fails=["السعر فوق المقاومة المثبتة ($68.72) — السيناريو ملغى",
                                                        "تأكيدات Short 0/4 (الحد الأدنى 2)", "R/R المرجّح 1:0.80 < 1:1"])
AT._near_add_b73(sm, "TRX", "Short", 0.69, "x", fails=["تأكيدات Short 0/4 (الحد الأدنى 2)", "تصنيف السوق ليس هابطاً",
                                                       "R/R المرجّح 1:0.90 < 1:1"])
nt = AT.near_text_b73(sm)
chk("HH1a الترتيب: الأقل نقصاً ثم الموانع آخراً", nt.index("BZ") < nt.index("TRX") < nt.index("HYPE"), nt)
chk("HH1b كل الشروط: «ينقصه 3 شروط: … و…»", "TRX Short (ميل هبوط 69%) — ينقصه 3 شروط: تأكيدات Short 0/4 (الحد الأدنى 2)، تصنيف السوق ليس هابطاً و…" in nt, nt)
chk("HH1c «مانع:» للسيناريو الملغى", "HYPE Short (ميل هبوط 70%) — مانع: السعر فوق المقاومة المثبتة ($68.72) — السيناريو ملغى — وينقصه 2 شروط" in nt, nt)
chk("HH1d شرط واحد كما كان", "BZ Short (ميل هبوط 73%) — ينقصه: تأكيدات Short 1/4 (الحد الأدنى 2)" in nt, nt)
import core.short_eval_b68 as SE
chk("HH1e السعر بصيغة البطاقات", SE._px_b91(68.7224) == "$68.72" and SE._px_b91(0.0012345) == "$0.0012345")

# ── EE: خطأ الرصيد الصريح + حفظ السوق ────────────────────────────
from core.exchange import Balance
chk("EE4a Balance.error افتراضياً فارغ (توافق)", Balance("USDT", 0, 0, 0).error == "" and Balance("USDT", 0, 0, 0, error="x").error == "x")
import handlers.trading as T


class _Eng:
    def __init__(self, b): self.b = b
    def get_user_exchange(self, uid):
        b = self.b
        class _X:
            async def get_balance(self, a): return b
        return {"exchange": _X()}


T._open_positions_map = lambda e, u, l: {}
chk("EE4b رصيد بخطأ صريح ⇒ fail", asyncio.run(T.risk_equity_state_ee1(_Eng(Balance("USDT", 0, 0, 0, error="401")), 1, True)) == (None, "fail"))
chk("EE4c صفر حقيقي ⇒ zero «رصيد المنصة $0»", asyncio.run(T.risk_equity_state_ee1(_Eng(Balance("USDT", 0, 0, 0)), 1, True)) == (0.0, "zero")
    and T.risk_portfolio_line_ee1("zero", 0, 10000, True).startswith("💰 المحفظة: رصيد المنصة $0 —"))
ex = src("core/exchange.py")
chk("EE4d كل المنصات تُعلِم بالخطأ", ex.count("error=") >= 9 and "_ok_b91" in ex)

KV = {}
A._sm = types.SimpleNamespace(kv_get=lambda k, d=None: KV.get(k, d), kv_set=lambda k, v: KV.__setitem__(k, v))
A.remember_mkt_ee3(types.SimpleNamespace(user_data={}), "BTC", "spot", uid=7)
chk("EE5a التفضيل في Redis", json.loads(KV["regime_mkt_ee3:7"])["sym"] == {"BTC": "spot"})
fresh = {}   # بعد إعادة التشغيل: user_data فارغ
chk("EE5b بعد إعادة التشغيل يُقرأ من Redis", A.regime_market_ee3(fresh, "BTC", uid=7) == ("spot", "sym")
    and A.regime_market_ee3({}, "ETH", uid=7) == ("spot", "last") and A.regime_market_ee3({}, "ETH", uid=8) == ("futures", "default"))

# ── DD1/DD2 ─────────────────────────────────────────────────────
g = A._llm_para_guard_b56("Fear & Greed 50: محايد — ترقب\n\n📊 *تحليل المؤشرات المتقاطعة*\n🟡 a   b")
chk("DD1 العنوان لا يلتصق بسطر F&G", "ترقب\n\n📊 *تحليل" in g and "a b" in g, repr(g))
from core.indicators import pct_signed_b91 as P
chk("DD2 لا «-0.0%»", [P(-0.03), P(0), P(-0.004), P(-12.34), P(0.06)] == ["-0.03%", "0.00%", "0.00%", "-12.3%", "+0.1%"])
chk("DD2b المواضع تستعمله", src("core/regime_detector.py").count("_pct_b91(") == 1 and s.count("_pct_b91(") >= 3)

# ── GG3 الاسترجاع ──────────────────────────────────────────────
import core.cloud_backup_gg2 as B
LED = {}
B._sm = lambda: types.SimpleNamespace(kv_get=lambda k, d=None: LED.get(k, d), kv_set=lambda k, v: LED.__setitem__(k, v))
AT._sm = B._sm
bak = {"kind": "measurement", "generated_at": "2026-11-20T02:00:00+00:00",
       "users": {"1": {"ledger": [{"a": 1}, {"a": 2}, {"a": 3}], "open_positions": {}},
                 "2": {"ledger": [{"b": 1}], "open_positions": {}},
                 "3": {"ledger": [{"c": 1}], "open_positions": {}}}}
LED["autoledger_b68:1"] = json.dumps([{"a": 1}])            # بداية مطابقة ⇒ يُستكمل
LED["autoledger_b68:2"] = json.dumps([{"b": 1}, {"b": 2}])  # أحدث ⇒ لا يُمس
LED["autoledger_b68:3"] = json.dumps([{"x": 9}])            # مختلف ⇒ لا يُمس
plan = B.restore_plan_gg2(bak)
acts = {p["uid"]: p["action"] for p in plan}
chk("GG3a الخطة", acts == {1: "restore", 2: "newer", 3: "skip"}, plan)
chk("GG3b العرض بلا تنفيذ", "للتنفيذ: /backup restore confirm" in B.restore_text_gg2(bak, plan) and len(json.loads(LED["autoledger_b68:1"])) == 1)
n = B.apply_restore_gg2(bak, plan)
chk("GG3c التنفيذ يستكمل فقط ولا يمس الأحدث/المختلف", n == 2 and len(json.loads(LED["autoledger_b68:1"])) == 3
    and json.loads(LED["autoledger_b68:2"]) == [{"b": 1}, {"b": 2}] and json.loads(LED["autoledger_b68:3"]) == [{"x": 9}])
chk("GG3d الأمر في /backup", '_a[0] == "restore"' in src("main.py"))

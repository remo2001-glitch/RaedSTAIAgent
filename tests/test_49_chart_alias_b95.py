"""الدفعة 95 (رحال 2026-11-19 23:56 «جميع الإصلاحات المعلقة دفعة واحدة»): MM1–MM5 (/chart) وNN1–NN3
(ANT ⇒ ANTHROPIC على مستوى النظام، مصدر CoinGecko صريح، Spot لأصل بلا سوق فوري) — عرض/بيانات فقط، لا يمس قرار القياس."""
import asyncio
import types
from _util import chk, src
import handlers.analysis as A
import core.chart_vision as CV
import core.data_layer as DL

# ── MM1: البند الذي يذكر «الإطار الزمني» في عنوانه لا يُحذف ─────────────
LIVE = """0- نوع السوق: Futures
1- الاتجاه العام على الإطار الزمني اليومي: هابط
2- مستويات الدعم والمقاومة:
الدعم: 92.91
3- النماذج الفنية: لا نموذج واضح
4- تحليل الشموع الأخيرة على الإطار الزمني اليومي: شموع حمراء متتالية
5- التوصية: انتظار"""
n1 = A.chart_normalize_items_b93(LIVE)
chk("MM1a البند 4 (الشموع على الإطار الزمني) باقٍ ولا TIMEFRAME مزيف", "4- تحليل الشموع الأخيرة على الإطار الزمني اليومي: شموع حمراء متتالية" in n1
    and "TIMEFRAME:" not in n1 and "1- الاتجاه العام على الإطار الزمني اليومي: هابط" in n1, n1)
chk("MM1b أبكر عنوان يحدد البند", A._item_canon_b93("تحليل الشموع الأخيرة على الإطار الزمني اليومي") == "4"
    and A._item_canon_b93("الاتجاه العام على الإطار الزمني") == "1" and A._item_canon_b93("مؤشر MACD والاتجاه") == "12"
    and A._item_canon_b93("الإطار الزمني المحدد") == "TF")
FIX = src("tests/fixtures/chart_kk_ant_b93.txt")
n2 = A.chart_normalize_items_b93(FIX)
chk("MM1c نص الدفعة 93 (1–16) ما زال يُعاد ترقيمه والإطار يُقرأ", n2.startswith("TIMEFRAME:1 يوم") and "8- هدف الربح" in n2
    and "14- مرحلة السوق" in n2, n2[:300])
n3 = A.chart_normalize_items_b93("1- الإطار الزمني: يتضح هبوط قوي\n2- نوع السوق: Spot\n3- الاتجاه: هابط")
chk("MM1d «الإطار الزمني» بقيمة ليست إطاراً لا يُحذف", "يتضح هبوط قوي" in n3 and "TIMEFRAME:" not in n3, n3)

# ── MM2: حدود البند لا تُقرأ من سعر في سطر مستقل ─────────────────────
T = "6- نقطة الدخول:\n109.18\n7- وقف الخسارة:\n92.91\n8- الأهداف:\n115.12\n9- المخاطرة: 2%"
chk("MM2a _item_span: «92.91» ليس البند 92", CV._item_span(T.split("\n"), 7) == (2, 4), CV._item_span(T.split("\n"), 7))
r = CV.replace_item(T, 7, ["7- وقف الخسارة: 90"])
chk("MM2b replace_item يستبدل كتلة البند كاملة", "92.91" not in r and "8- الأهداف:" in r and "109.18" in r, r)
chk("MM2c لا نمط بنود قديم في النظام", "[-–.)]" not in src("handlers/analysis.py") and "[-–.)]" not in src("core/chart_vision.py"))

# ── MM3: البنود الغائبة تُذكر ───────────────────────────────────────
full = "\n".join(f"{i}- بند {i}: x" for i in range(15))
chk("MM3a كل البنود ⇒ لا سطر", A.chart_missing_items_b95(full) == "")
part = "\n".join(f"{i}- بند {i}: x" for i in range(15) if i != 4)
chk("MM3b الغائب يُسمّى", A.chart_missing_items_b95(part) == "⚠️ لم يرد في قراءة النموذج: البند 4 — الشموع الأخيرة", A.chart_missing_items_b95(part))
chk("MM3c نص غير مرقّم (رسالة خطأ) ⇒ لا سطر", A.chart_missing_items_b95("⚠️ تعذّر التحليل") == "")
chk("MM3d موصول في /chart", "_mi95 = chart_missing_items_b95(_analysis_clean)" in src("handlers/analysis.py"))

# ── MM4: خطوط EMA من الشريط ─────────────────────────────────────────
leg = CV.parse_legend("SYMBOL: ANTUSDT\nPRICE: 95.92\nVWAP: 99.83\nBOLL_MID: 109.18\nBOLL_UB: 132.42\nBOLL_LB: 85.94\n"
                      "SUPERTREND_RED: 117.80\nEMA_A: EMA20=104.35\nEMA_B: EMA25=108.10\nEMA_C: EMA70=130.78")
chk("MM4a قراءة EMA20/25/70", leg.get("ema") == ["EMA20", "EMA25", "EMA70"] and leg.get("EMA70") == 130.78
    and leg["raw"]["EMA20"] == "104.35", leg)
chk("MM4b في المفاتيح والتعليمات", "EMA_A" in CV.LEGEND_KEYS and "EMA20=101.23" in CV.legend_prompt())
lv = "\n".join(CV.build_levels_item(leg, 95.92))
chk("MM4c قائمة المستويات تشمل EMA مرتبة", "EMA70: $130.78 (فوق السعر بنحو 36.3%)" in lv and lv.index("EMA70") < lv.index("Supertrend".replace("Supertrend", "SuperTrend")), lv)
t, n = CV.strip_unseen_mas("السعر تحت EMA20 وEMA70 وفوق EMA200، وتحت المتوسطات المتحركة", leg)
chk("MM4d الظاهرة تبقى وغير الظاهرة تُزال", "EMA20" in t and "EMA70" in t and "EMA200" not in t and "المتوسطات المتحركة" in t, t)
t0, _ = CV.strip_unseen_mas("تحت EMA20 والمتوسطات المتحركة", {"PRICE": 1.0, "ema": []})
chk("MM4e بلا EMA في الشريط: السلوك السابق", "EMA20" not in t0 and "خطوط المؤشرات المعروضة" in t0, t0)
f = CV.build_facts(leg, 95.92)
chk("MM4f الحقائق تذكر موضع السعر من EMA", "EMA70 = 130.78 (السعر تحته بنحو 26.7%)" in f, f)
chk("MM4g المستويات غير المذكورة تشمل EMA", "EMA25 108.10" in A.chart_missed_levels_b91("لا شيء", leg))
chk("MM4h الشريط القديم (بلا EMA) سليم", CV.parse_legend("PRICE: 1\nVWAP: 2").get("ema") == [])

# ── MM5: الحجم بلا لوحة VOL ─────────────────────────────────────────
c = A._chart_cleanup_b54("5- التوصية: شراء بعد إغلاق فوق 117.80 مع حجم تداول مرتفع\n6- نقطة الدخول: 118 بحجم قوي", {"PRICE": 95.0})
chk("MM5a «مع حجم تداول مرتفع» و«بحجم قوي» تُحذفان بلا VOL", "حجم" not in c and "117.80" in c and "118" in c, c)
chk("MM5b سطر التوضيح حين يبقى ذكر الحجم", A.chart_no_vol_note_b95("1- الاتجاه: هابط مع الحجم", {"PRICE": 1.0}).startswith("ℹ️ لا لوحة حجم (VOL)")
    and A.chart_no_vol_note_b95("1- الاتجاه: هابط مع الحجم", {"PRICE": 1.0, "VOL": 5.0}) == ""
    and A.chart_no_vol_note_b95("13- حجم الصفقة: لا حجم", {"PRICE": 1.0}) == "")

# ── NN1: ANT ⇒ ANTHROPIC على مستوى النظام ────────────────────────────
chk("NN1a صيغ الرمز", [A.alias_token_b95(x) for x in ("ANT", "antusdt", "ANT-USDT-SWAP", "ANT/USDT", "ANTBTC", "BTC")]
    == ["ANT", "ANT", "ANT", "ANT", None, None])


class _DL(DL.DataLayer):
    def __init__(self, own=False, alt=True):
        self.own, self.alt, self.calls = own, alt, []
    async def _price_okx(self, s, q="USDT"):
        self.calls.append(("spot", s)); return {"price": 1.0} if (self.own and s == "ANT") else None
    async def _price_okx_perp(self, s):
        self.calls.append(("perp", s)); return {"price": 96.9} if (self.alt and s == "ANTHROPIC") else None


DL._cache.clear()
chk("NN1b لا إدراج لـANT ⇒ ANTHROPIC", asyncio.run(_DL().resolve_display_alias_b95("ANT")) == "ANTHROPIC")
DL._cache.clear()
chk("NN1c إن أُدرج ANT نفسه على OKX يبقى", asyncio.run(_DL(own=True).resolve_display_alias_b95("ANT")) == "ANT")
DL._cache.clear()
d3 = _DL(alt=False)
chk("NN1d تعذّر التحقق ⇒ يبقى ولا يُخزَّن", asyncio.run(d3.resolve_display_alias_b95("ANT")) == "ANT" and "dispalias_b95:ANT" not in DL._cache)
chk("NN1e رمز عادي بلا شبكة", (lambda d: (asyncio.run(d.resolve_display_alias_b95("BTC")), d.calls))(_DL()) == ("BTC", []))
DL._cache.clear()
SENT = []


class _M:
    async def reply_text(self, t, *a, **k): SENT.append(t)


ctx = types.SimpleNamespace(args=["ANT", "full"], bot_data={"raed_engine": types.SimpleNamespace(data_layer=_DL())})
note = asyncio.run(A.alias_args_b95(types.SimpleNamespace(effective_message=_M()), ctx))
chk("NN1f الأمر يستلم ANTHROPIC مع سطر توضيح", ctx.args == ["ANTHROPIC", "full"] and SENT == [note]
    and "ANT في تطبيق OKX = عقد ANTHROPIC-USDT-SWAP" in note, (ctx.args, SENT))
ctx2 = types.SimpleNamespace(args=["BTC"], bot_data={"raed_engine": types.SimpleNamespace(data_layer=_DL())})
chk("NN1g رمز عادي لا يتغير ولا رسالة", asyncio.run(A.alias_args_b95(types.SimpleNamespace(effective_message=_M()), ctx2)) == ""
    and ctx2.args == ["BTC"] and len(SENT) == 1)
m = src("main.py")
chk("NN1h مُطبَّق مركزياً قبل المهلة", "_install_symbol_alias_b95(app)\n    _install_handler_timeouts(app)" in m
    and {"signal", "analyze", "quicksignal", "regime", "liquidity", "planweek", "planmonth"} <= A.SYMBOL_CMDS_B95)

# ── NN2/NN3: وسم السوق يصرّح بالمصدر الفعلي ─────────────────────────
cg = [{"close": 0.07, "source": "coingecko"}]
chk("NN3a Futures من CoinGecko ⇒ المصدر واسم الأصل", A._mkt_tag_b41("futures", cg, "ANT")
    == " · 📉 Futures (مرجعي: لا عقد دائم للأصل ولا إدراج على OKX — البيانات من CoinGecko (aragon))", A._mkt_tag_b41("futures", cg, "ANT"))
chk("NN3b Spot من CoinGecko", A._mkt_tag_b41("spot", cg, "ANT") == " · 💱 Spot (غير مدرج على OKX — البيانات من CoinGecko (aragon))")
chk("NN3c الفوري من OKX كما كان", A._mkt_tag_b41("futures", [{"source": "okx"}]) == " · 📉 Futures (مرجعي: لا عقد دائم للأصل — البيانات من الفوري)"
    and A._mkt_tag_b41("spot", [{"source": "okx"}]) == " · 💱 Spot" and A._mkt_tag_b41("futures", [{"source": "okx_perp"}]) == " · 📉 Futures")
chk("NN2 Spot لأصل بلا سوق فوري ⇒ بيانات العقد مصرَّح بها", A._mkt_tag_b41("spot", [{"source": "okx_perp"}], "ANTHROPIC")
    == " · 📈 Futures/Perp (لا سوق فوري للأصل على OKX — البيانات من العقد الدائم)")
p = src("handlers/plan.py")
chk("NN3d الخطة الأسبوعية تذكر المصدر الفعلي", '_mt_b45("futures", candles, str(sym))' in p and "البيانات من {_src95}" in p)
card = A.signal_card_b82({"sym": "X", "price": 1.0, "mkt_tag": A._mkt_tag_b41("futures", cg, "ANT"), "futures": True,
                          "meta": {"confidence_pct": 38}, "score": {}, "rec": {"wait": True}})
chk("NN2b بطاقة /signal: سطر Short غير متاح بلا عقد دائم", "• Short: غير متاح — لا عقد دائم للأصل على OKX" in card, card)

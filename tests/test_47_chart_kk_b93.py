"""الدفعة 93 (رحال 2026-11-19 22:21): KK1–KK5 في /chart — سعر أصل آخر بالرمز نفسه، ترقيم البنود من عناوينها،
الإطار الزمني من بنده، تحذيرات الجهة الهابطة. عرض/قراءة فقط — لا يمس قرار القياس."""
import os
from _util import chk, src, ROOT
import handlers.analysis as A

T = open(os.path.join(ROOT, "tests", "fixtures", "chart_kk_ant_b93.txt"), encoding="utf-8").read()   # رد /chart الفعلي 22:17

# ── KK2/KK3/KK5 ────────────────────────────────────────────────
n = A.chart_normalize_items_b93(T)
it = A._chart_item_lines_b51(n, nums=("0", "5", "6", "7", "8", "14"))
chk("KK2a الترقيم من العناوين: الدخول 6، الوقف 7، الأهداف 8", "104.00" in it.get("6", "") and "118.00" in it.get("7", "")
    and "92.91" in it.get("8", "") and "نوع السوق" in it.get("0", "") and "التوصية" in it.get("5", ""), it)
chk("KK5 «Market Phase» ⇒ «مرحلة السوق»", it.get("14", "").startswith("مرحلة السوق"), it.get("14"))
tf, n2 = A._chart_parse_timeframe_b50(n)
chk("KK3 الإطار من بند «الإطار الزمني المحدد: 1 يوم»", tf.startswith("يومي") and "الإطار الزمني المحدد" not in n2, tf)
ok = "0- نوع السوق: Spot\n1- الاتجاه: هابط\n6- نقطة الدخول: 100\n7- وقف الخسارة: 90\n8- هدف الربح: 120"
chk("KK2b ترقيم صحيح لا يُمس", A.chart_normalize_items_b93(ok) == ok)

# ── KK1 ────────────────────────────────────────────────────────
chk("KK1a فحص التطابق", A.chart_price_mismatch_b93(0.071962, 95.33) and not A.chart_price_mismatch_b93(95.5, 95.33)
    and A.chart_price_mismatch_b93(0.07, 104, tol=2.0) and not A.chart_price_mismatch_b93(0, 95))
chk("KK1b وسيط المستويات مرجعاً", A.chart_levels_median_b93(n2) == 104.0)
d = A.chart_drop_live_px_b93(n2)
chk("KK1c حذف PRICE_NOW الحي وتعليق «يختلف»", "PRICE_NOW" not in d and "يختلف تماماً" not in d and "مرحلة تصحيح" in d)
s = src("handlers/analysis.py")
chk("KK1d السعر الحي بنوع السوق + لا يُمرَّر عند التعارض", '_mk_px_b93 = "futures" if (_leg_mkt_b92 == "PERP" or _chart_is_futures) else "spot"' in s
    and "_px_mismatch_b93, _current_px = _current_px, 0.0" in s and "يطابق أصلاً آخر" in s)
chk("KK1e الترويسة لا تجلب سعر الأصل الآخر", "if _chart_price_sym and not _px_mismatch_b93:" in s)
ns = src("core/news.py")
chk("KK1f احتياط Groq يقرأ PRICE أيضاً", '("SYMBOL", "MARKET", "PRICE")' in ns and "PRICE: السعر الحالي الكبير" in ns)

# ── KK2+KK4: R/R والتحذيرات تعود ─────────────────────────────────
out = A._chart_rr_block_b53(d, "USDT", 95.33, (), leg={})[0]
chk("KK2c R/R 0.79 وتحذيره", "الهدف 1 = 0.79:1" in out and "R/R دون 1:1" in out, out)
chk("KK4a شرط الكسر متحقق أصلاً", "ℹ️ شرط الكسر تحت 104.46 متحقق أصلاً (السعر 95.33) — الدخول 104 فوق السعر الحالي" in out, out)
chk("KK4b «دعم» فوق السعر", "⚠️ «دعم» 104.46 فوق السعر الحالي 95.33 — يعمل مقاومة لا دعماً" in out, out)
chk("KK4c لا تحذير لدعم تحت السعر", "«دعم» 92.91" not in out)
w = A.chart_short_side_warns_b93("2- المستويات:\n- مقاومة: 90.00\n5- التوصية: شراء", [95.0], 90.0, 96.0)
chk("KK4d «مقاومة» تحت السعر", w == ["⚠️ «مقاومة» 90 تحت السعر الحالي 96 — تعمل دعماً لا مقاومة"], w)
chk("b93 رقم الشرط بين القوسين ليس دخولاً ثانياً", A._chart_nums_b53("6- الدخول: 104.00 (بعد إغلاق يومي تحت 104.46)") == [104.0])
chk("b93 «92.91» لا يُعاد ترقيمه في b56", A._chart_renumber_b56("1- نوع السوق: x\n2- الاتجاه\n92.91") .endswith("\n92.91"))

# ── b93: كتلة المخاطرة/الحجم كاملة ─────────────────────────────────
blk = "9- مستوى المخاطرة:\n2% من رأس المال\n\n10- RSI: 20\n\n13- حجم الصفقة المقترح:\n2% من رأس المال\n\n14- مرحلة السوق:\nx"
fx = A._chart_fix_risk_size_b53(blk, 0.79, 13.5)
chk("b93 «لا حجم» بلا «2%» متبقٍّ تحته", "2% من رأس المال" not in fx and "13- حجم الصفقة المقترح: لا حجم" in fx and "14- مرحلة السوق:\nx" in fx, fx)
fx2 = A._chart_fix_risk_size_b53(blk, 1.5, 5.0)
chk("b93 النسبة من السطر التالي ⇒ المخاطرة الفعلية", "≈ 0.10% من رأس المال (حجم 2% × مسافة الوقف 5.0%)" in fx2, fx2)

# ── KK1: اسم التطبيق ⇒ معرّف الـAPI للعقد الدائم ───────────────────────
import asyncio
import core.data_layer as DL
dl = DL.DataLayer.__new__(DL.DataLayer)
PX = {"ANTHROPIC": 96.81, "ANTX": 0.5}
async def _pp(sym):
    return {"price": PX[sym]} if sym in PX else None
dl._price_okx_perp = _pp
dl.session = None
r = asyncio.run(dl.perp_price_display_b93("ANT", 95.33))
chk("KK1g ANT ⇒ ANTHROPIC (جدول الأسماء + تطابق السعر)", r[1] == "ANTHROPIC" and r[0]["price"] == 96.81, r)
async def _fetch_stub(session, url, **kw):
    return {"data": [{"instId": "FOOBAR-USDT-SWAP"}, {"instId": "FOO-USDT-SWAP"}]}
DL._fetch = _fetch_stub
PX.update({"FOOBAR": 10.2, "FOO": 99.0})
DL.DataLayer._swap_bases_b93 = (0.0, [])
r2 = asyncio.run(dl.perp_price_display_b93("FOO", 10.0))
chk("KK1h اكتشاف بالبادئة حين يخالف سعر الاسم نفسه سعرَ الصورة", r2[1] == "FOOBAR", r2)
r3 = asyncio.run(dl.perp_price_display_b93("FOO", 0))
chk("KK1i بلا سعر صورة: الاسم نفسه", r3[1] == "FOO")
chk("KK1j الإرشاد إلى /signal بالمعرّف الصحيح", "التحليل الشامل: /signal {_api_sym_b93}" in s and '"CLAUDE": "ANTHROPIC"' in src("core/pair_resolver.py"))

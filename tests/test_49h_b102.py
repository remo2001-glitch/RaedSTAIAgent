"""الدفعة 102 (رحال 2026-11-20 23:29 «نعم نفذ جميع الاصلاحات المعلقة»): YY1، ZZ1، XX1، SS1، UU1 — عرض/تشغيل فقط."""
import json
from _util import chk, src, ROOT
import os
import handlers.analysis as A

s = src("handlers/analysis.py")

# ── SS1: مقام واحد = السعر الحي ───────────────────────────────
G = A._gap_live_b102
chk("SS1a بُعد NVDA = 12.4% بالمقامين (كان 11.0% ÷ المستوى)", f"{G(144.26, 128.34):.1f}" == "12.4")
x = A._anchor_cross_b64({"nearest_support": 144.26, "nearest_resistance": 160.10}, 128.34, 7.5)
chk("SS1b وسم الدعم المثبت والتنبيه بالمقام نفسه", "دونه بـ12.4%" in x["tag_s"] and "بـ12.4% (> ½ATR)" in x["alert"], x)
y = A._anchor_cross_b64({"nearest_support": 90, "nearest_resistance": 100}, 110, 2.0)
chk("SS1c فوق المقاومة ÷ السعر الحي", "فوقها بـ9.1%" in y["tag_r"], y)
chk("SS1d لا مقام المستوى في رسائل «السعر الحي دونه/فوقه»", "(1 - price / ns) * 100:.1f" not in s and "(price / resistance - 1) * 100:.1f" not in s
    and "(1 - price / support) * 100:.1f" not in s and "(1 - price / _ns_scen)" not in s and "(price / _fib_res_nxt - 1)" not in s)
fl = "\n".join(A._fmt_fib_lines({"levels": {"0.0": 144.26, "0.236": 160.10, "1.0": 211.37}, "nearest_support": 144.26,
                                 "nearest_resistance": 160.10}, 128.34, 7.5))
chk("SS1e سطر Fib «بُعد» و«دونه بـ» الرقم نفسه", "بُعد 12.4%" in fl and "دونه بـ12.4%" in fl, fl)

# ── XX1 ───────────────────────────────────────────────────────
chk("XX1 «عند حد الدخول» حين تساوي الثقة الحد", "_at102 = f\"{conf:.0f}\" == f\"{thr:.0f}\"" in s and "{'عند' if _at102 else 'فوق'} حد الدخول" in s)

# ── YY1 ───────────────────────────────────────────────────────
D = A._short_sl_desc_b102
chk("YY1a وقف بالسقف يُوصف كما هو", D({"sl_pct": 10.0}, 7.47) == "الوقف بسقف 10% (أقل من 1.5×ATR = 11.2%)", D({"sl_pct": 10.0}, 7.47))
chk("YY1b وقف 1.5×ATR", D({"sl_pct": 8.9}, 5.93) == "الوقف 1.5×ATR")
chk("YY1c وقف فوق Fib التالي", D({"sl_pct": 8.0}, 4.0).startswith("الوقف 8.0% فوق مستوى Fib"))
chk("YY1d لا «≥ 1.5×ATR» ثابتة في سطر خطة Short", "الوقف ≥ 1.5×ATR، الأهداف" not in s)
chk("YY1e خطة Short بلا تحذير «خطر إيقاف مبكر» (السقف مقصود كما في /signal)", "            if _qs_short101:\n                # YY1 (b102)" in s)
P = A._rr_pair_b102
chk("YY1f «0.7 بدل 0.7» ⇒ منزلتان", P(0.667, 0.72) == "0.67 بدل 0.72" and P(1.4, 1.9) == "1.4 بدل 1.9")
chk("YY1g R/R التحذير مرجّح ولا «دون 2.0» تحت 1", "(0.6 * abs(tp1 - entry) + 0.4 * abs((tp2 or tp1) - entry))" in s
    and '(")" if _rr_aft_q < 1.0 else' in s)

# ── ZZ1 ───────────────────────────────────────────────────────
chk("ZZ1 /quicksignal لعقد السهم 365 يوماً كما /signal", "engine.data_layer.get_ohlcv_perp(symbol, 365),\n                        engine.data_layer.get_fear_greed()," in s
    and "engine.data_layer.get_ohlcv_perp(symbol, 90)," not in s)

# ── UU1 ───────────────────────────────────────────────────────
rj = json.load(open(os.path.join(ROOT, "railway.json")))
chk("UU1 إعادة التشغيل حتى 10 مرات", rj["deploy"]["restartPolicyMaxRetries"] == 10 and rj["deploy"]["restartPolicyType"] == "ON_FAILURE")

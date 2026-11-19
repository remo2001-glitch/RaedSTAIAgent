"""بطاقة /signal القصيرة + «📖 التفاصيل الكاملة» (الدفعة 82، البند Y)، وبنود U1 وW1–W3."""
from _util import chk, src
import handlers.analysis as A
import core.auto_trader_b68 as AT

# ── U1: نسبة صغيرة في /autostats ─────────────────────────────
P = AT._pct_b82
chk("U1a منزلتان دون 0.1%", P(-0.031) == "−0.03%" and P(0.04) == "+0.04%", [P(-0.031), P(0.04)])
chk("U1b علامة «−» نفسها في المبلغ والنسبة", P(-1.26) == "−1.3%" and P(1.1) == "+1.1%" and P(0) == "0.00%")
st = AT.auto_stats_b68([])
t = AT.format_auto_stats_b68(st, [{"sym": "BTC", "side": "long", "hours": 5, "net": -0.09, "net_pct": -0.03}])
chk("U1c سطر /autostats", "−$0.09 (−0.03%)" in t and "-0.0%" not in t, t)

# ── W1: سطر توضيح بدل رسالة «رمز متضارب» ─────────────────────
chk("W1a سطر LINK", A.sym_note_b82("link") == "ℹ️ LINK هنا = Chainlink من بيانات OKX (الأسهم المرمّزة تُكتب بحرف X، مثل XAAPL)")
chk("W1b لا سطر لغيرها", A.sym_note_b82("BTC") == "")
s = src("handlers/analysis.py")
chk("W1c لا رسالة «رمز متضارب»", "تنبيه — رمز متضارب" not in s)

# ── W2: R/R واحد دون 1 (للأسفل كتذييل G3) ───────────────────
chk("W2a 0.936 ⇒ 0.93", A._fmt_rr(0.936) == "0.93" and A._fmt_rr(0.85) == "0.85" and A._fmt_rr(0.999) == "0.99", [A._fmt_rr(0.936), A._fmt_rr(0.85)])
chk("W2b فوق 1 كما كان", A._fmt_rr(1.02) == "1.02" and A._fmt_rr(1.96) == "1.96" and A._fmt_rr(2.4) == "2.4")
chk("W2c سبب AUTO-FAIL من تذييل G3 لا من rec المقرّب", "1:0.93 < 1:1" in AT.autofail_reason_b79(
    {"rr": 0.9, "verdict_reason": "❌ *AUTO-FAIL* | G3: R/R = 1:0.93 < 1:1 — لا حافة إحصائية"}))

# ── W3: Funding الطفيف ──────────────────────────────────────
chk("W3 وسم «سالب طفيف»", '"🟢 سالب طفيف (دون حد التأكيد −0.01%)"' in s and "elif _fund_pct < -0.01:\n        _fund_sig = \"🟢 سالب (فرصة Long)\"" in s)

# ── Y: البطاقة ──────────────────────────────────────────────
fib = ["📐 *مستويات Fibonacci*", "  🔴 0.236 — $9.3803 ◀ الأقرب للسعر", "  🟢   0.0 — $8.567",
       "• أقرب دعم فيبو:    $8.567 (Fib — يحتاج تأكيد سعري)",
       "⚠️ الدعم المعتمد = قاع نطاق Fib (0.0) — كسره يعني قاعاً جديداً",
       "ℹ️ الدعم/المقاومة من آخر شمعة يومية مكتملة (إغلاق $8.878) — لا تتغير"]
sh = {"bear_conf": 0.71, "flags": ["Volume Spike 1.6x (شمعة هابطة) ✓"], "n_flags": 1, "entry": 9.3803, "sl": 10.32,
      "tp1": 8.567, "tp2": 7.8921, "sl_pct": 10.0, "tp1_pct": 8.7, "tp2_pct": 15.9, "rr": 1.15, "eligible": False,
      "size_factor": 0.0, "reason": "تأكيدات Short 1/4 (الحد الأدنى 2)"}
meta = {"direction": "neutral", "confidence_pct": 29, "confirmations_met": 0, "short_b68": sh, "rsi": 32.2,
        "vol_ratio": 1.6, "vol_dir_b82": "down", "atr_pct": 7.9, "fund_known_b82": True, "fund_pct_b82": -0.0058,
        "oi_chg_b82": 0.52, "cvd_available": True, "cvd_pct_b82": 8.7, "entry_agg": 8.567, "pro_sl": 7.5179}
rec = {"verdict": "AUTO-FAIL", "wait": True, "size_pct": 0.0, "size_rule": "0% — الاتجاه غير مؤكد، انتظر وضوح الجهة", "rr": 0.9}
score = {"verdict": "AUTO-FAIL", "header": "❌ *AUTO-FAIL* | G3: R/R = 1:0.93 < 1:1 — لا حافة إحصائية"}
d = {"sym": "LINK", "price": 9.251, "mkt_tag": " · 📉 Futures", "meta": meta, "score": score, "rec": rec,
     "regime_desc": "🔴 اتجاه هابط", "adx": 30.5, "di_plus": 9, "di_minus": 32, "pve50": -18.9, "fear_greed": 10,
     "daily_dir": "🔴 هابط", "h4_dir": "🔴 هابط", "bear_confirmed": True, "fib_lines": fib,
     "pre_lines": [A.sym_note_b82("LINK")], "top_lines": [], "warn_lines": [], "futures": True, "entry_threshold": 55}
c = A.signal_card_b82(d)
lines = c.split("\n")
chk("Y1 طول البطاقة ≤ 40 سطراً (مع Fibonacci)", len(lines) <= 40, len(lines))
chk("Y2 الحكم مرة واحدة في الرأس", "⛔ *الحكم: لا شراء (بنية فاشلة) ولا Short الآن*" in c and c.count("الحكم:") == 1, c)
chk("Y3 سبب الشراء الفعلي وR/R واحد", "R/R المرجّح 1:0.93 < 1:1" in c and "0.94" not in c and "اتجاه هابط قائم، لا شراء" in c
    and "الاتجاه غير مؤكد" not in c, c)
chk("Y4 Short: السبب والمحتسب وR/R بمنزلتين", "• Short: ⏳ تأكيدات Short 1/4 (الحد الأدنى 2) — المحتسب: Volume Spike 1.6x (شمعة هابطة) | R/R 1:1.15" in c, c)
chk("Y5 خطة Short عند التفعيل", "🔻 *خطة Short (عند التفعيل):*" in c and "دخول $9.3803 بعد شمعة رفض | وقف $10.32 (+10.0%)" in c
    and "TP1 $8.567 (−8.7%، 60%) ← TP2 $7.8921 (−15.9%، 40%)" in c, c)
chk("Y6 الصورة: كل رقم مرة", c.count("RSI 32") == 1 and c.count("ATR 7.9%") == 1 and c.count("1.6x") == 2
    and "−18.9% عن EMA50" in c and "F&G 10" in c and "(ADX 30.5، −DI أعلى)" in c, c)
chk("Y7 المشتقات في سطر", "🔗 Funding −0.0058% | OI +0.52% (ساعة) | CVD +8.7%" in c, c)
chk("Y8 Fibonacci يبقى والشرح الثابت ينتقل للتفاصيل", "📐 *مستويات Fibonacci*" in c and "0.236 — $9.3803" in c
    and "أقرب دعم فيبو" in c and "قاع نطاق Fib (0.0)" not in c and "من آخر شمعة يومية مكتملة" not in c, c)
chk("Y9 ما الذي يغيّر الحكم", "• شراء: انتهاء الهبوط القائم (ADX < 25 أو تقارب DI) + ثقة ≥ 65% لتأكيد جهة الشراء (حالياً 29%) + تأكيدات شراء 2/4 (حالياً 0/4) + R/R المرجّح ≥ 1:1" in c
    and "• Short: تأكيد إضافي واحد — CVD بيع ≤ −20%، أو Funding > +0.01%" in c and "Volume Spike" not in c.split("ما الذي يغيّر")[1], c)
chk("Y10 رابط التفاصيل والتنبيه آخر سطر", "/signal LINK full" in c and lines[-1] == A._DISC and lines[0].startswith("ℹ️ LINK هنا"), lines[-2:])
chk("Y11 لا خطة خروج والأهداف غير مفعّلة", "خطة الشراء" not in c and "Time Exit" not in c and "المدة المتوقعة" not in c)

# دخول شراء
m2 = dict(meta, direction="long", confidence_pct=68, confirmations_met=2, short_b68=None,
          conf_flags_b82=["Volume Spike 1.8x ✓", "CVD شراء (+24%) ✓"], entry_agg=100.0, pro_sl=94.0, tp1_v=108.0, tp2_v=112.0,
          sl_pct=6.0, tp_pct=8.0, tp2_pct=12.0, rr_raw=1.47, vol_dir_b82="up")
r2 = {"verdict": "CONDITIONAL", "wait": False, "size_pct": 20.0, "size_rule": "20% — تأكيدات 2/4"}
c2 = A.signal_card_b82(dict(d, meta=m2, rec=r2, score={"verdict": "CONDITIONAL", "verdict_ar": "مقبولة بشروط", "score": 72},
                            bear_confirmed=False, pre_lines=[], futures=False))
chk("Y12 حكم الدخول وخطة 60/40 ونقل الوقف", "🟢 *الحكم: دخول شراء — حجم 20%*" in c2 and "دخول $100.00 | وقف $94.00 (−6.0%)" in c2
    and "TP1 $108.00 (+8.0%، 60%) ← TP2 $112.00 (+12.0%، 40%)" in c2 and "بعد TP1 يُنقل الوقف إلى $108.00 | R/R المرجّح 1:1.5" in c2, c2)
chk("Y13 التأكيدات وجودة البنية والإلغاء", "✅ تأكيدات 2/4 — Volume Spike 1.8x · CVD شراء (+24%)" in c2 and "جودة البنية: 72/100 — مقبولة بشروط" in c2
    and "❌ الإلغاء: إغلاق يومي دون الوقف $94.00" in c2 and "Short" not in c2 and "🔗" not in c2, c2)
# Short مؤهل
c3 = A.signal_card_b82(dict(d, meta=dict(meta, short_b68=dict(sh, eligible=True, n_flags=2, size_factor=0.5, reason="",
                                                              flags=sh["flags"] + ["CVD بيع (−24.0%) ✓"])),
                            rec=dict(rec, verdict="WATCH ONLY"), score={"verdict": "WATCH ONLY"}))
chk("Y14 Short مؤهل", "🔻 *الحكم: Short مؤهل — حجم نصف (Futures افتراضي 1x)*" in c3 and "🔻 *خطة Short:*" in c3
    and "• Short: ✅ مؤهل — تأكيدات 2/4" in c3, c3)

# «full» والزر
chk("Y15 /signal X full", A.wants_full_b82(["LINK", "full"]) and A.wants_full_b82(["LINK", "كامل"]) and not A.wants_full_b82(["LINK"]))
bd = {}
k = A.sigfull_put_b82(bd, 1, "LINK", "FULL")
chk("Y16 ذاكرة التفاصيل ومعرّف قصير", bd["_sigfull_b82"][k]["text"] == "FULL" and len(f"sigfull:{k}".encode()) <= 64)
for i in range(200):
    A.sigfull_put_b82(bd, 1, "X", "t")
chk("Y17 سقف الذاكرة", len(bd["_sigfull_b82"]) <= A._SIGFULL_MAX_B82)
chk("Y18 الزر مسجّل والمسح الآلي بلا بطاقة", 'pattern=r"^sigfull:"' in s and "if not _hl68 and not _full82:" in s
    and 'context.user_data["_sig_full82"] = bool(_full82)' in s)
chk("Y19 خطأ البطاقة يرسل الكامل", "إرسال التقرير الكامل" in s)
N = A._short_need_b82
chk("Y20 مانع Short بصيغة المطلوب", N("RSI 19 < 30 — ذروة بيع، لا Short", 19.4) == "خروج RSI من ذروة البيع (≥ 30، حالياً 19)"
    and N("ميل الهبوط 60% < 65% — الجهة الهابطة غير مؤكدة").startswith("ميل هبوط ≥ 65%") and N("R/R المرجّح 1:0.80 < 1:1") == "R/R المرجّح ≥ 1:1")
M = A._short_missing_b82
chk("Y21 Funding لا يُطلب للسلع", not any("Funding" in x for x in M([], True)) and any("Funding" in x for x in M([])))
c4 = A.signal_card_b82(dict(d, meta=dict(meta, short_b68=dict(sh, flags=[], n_flags=0, reason="تأكيدات Short 0/4 (الحد الأدنى 2)"))))
chk("Y22 المثنى", "• Short: تأكيدان إضافيان — حجم ≥1.5x بشمعة هابطة" in c4, c4)
chk("Y23 تنبيه السوق المغلق مختصر (موعد الفتح وساعات التداول)", '"يفتح" in x or "ساعات التداول" in x' in s)

# ── Y المرحلة 2: /analyze ───────────────────────────────────
c5 = A.signal_card_b82(dict(d, title="🧠", price_suffix=" (+5.11% / 24س)", cmd="analyze", meta=dict(meta, short_b68=None),
                            reading="الاتجاه هابط والزخم ضعيف.", h4_dir="",
                            scenarios=["🟢 صاعد: إغلاق 4H فوق $9.3803 + حجم 1.5x+", "🔴 هابط: كسر $8.567 بإغلاق 4H"]))
chk("Y24 رأس /analyze والقراءة", "🧠 *LINK* — $9.251 (+5.11% / 24س) · 📉 Futures" in c5 and "🧠 *القراءة:*\nالاتجاه هابط والزخم ضعيف." in c5, c5)
chk("Y25 السيناريوهان ورابط /analyze full", "📋 *السيناريوهان:*\n🟢 صاعد: إغلاق 4H فوق $9.3803" in c5 and "/analyze LINK full" in c5
    and "· 4H" not in c5, c5)
chk("Y26 /analyze لا يحفظ قراراً", "save=False" in s and "if not save:" in s)
chk("Y27 /analyze: زر وfull وحفظ عبر سؤال السوق", 'cmd="analyze"' in s and 'context.user_data["_an_full82"] = bool(_full82a)' in s
    and "analyze card b82" in s)
chk("Z1 سطر فارغ قبل تقييم Short", 'parts.append("")   # batch82 (Z1)' in s)

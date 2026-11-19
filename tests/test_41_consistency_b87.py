"""الدفعة 87: الاتساق بين مخرجات الأوامر، CC1–CC3، قائمة /start، وحذف الكود الميت — عرض فقط."""
import os
import re
from _util import chk, src, ROOT
import handlers.analysis as A

s = src("handlers/analysis.py")

# ── CC1: لا «OI 0.00%» لأصل بلا عقد دائم ──────────────────────
base = {"sym": "XTTWO", "price": 217.26, "mkt_tag": " · 📉 Futures (مرجعي: لا عقد دائم للأصل — البيانات من الفوري)",
        "score": {}, "rec": {"wait": True, "size_rule": "0% — الاتجاه غير مؤكد، انتظر وضوح الجهة"},
        "regime_desc": "🟡 تذبذب جانبي", "futures": True, "entry_threshold": 55}
sh = {"bear_conf": 0.44, "flags": [], "n_flags": 0, "entry": 227.15, "sl": 241.74, "tp1": 211.56, "tp2": 205.13,
      "sl_pct": 6.4, "tp1_pct": 6.9, "tp2_pct": 9.7, "rr": 1.24, "eligible": False, "size_factor": 0.0,
      "reason": "ميل الهبوط 44% < 65% — الجهة الهابطة غير مؤكدة",
      "fails_b86": ["ميل الهبوط 44% < 65% — الجهة الهابطة غير مؤكدة", "تصنيف السوق ليس هابطاً", "تأكيدات Short 0/4 (الحد الأدنى 2)"]}
meta = {"direction": "neutral", "confidence_pct": 56, "confirmations_met": 0, "short_b68": sh, "rsi": 52, "vol_ratio": 10.8,
        "atr_pct": 4.3, "oi_chg_b82": 0.0, "oi_known_b87": False, "fund_known_b82": False, "no_perp_b87": True,
        "cvd_available": True, "cvd_pct_b82": -6.5, "entry_agg": 211.56}
c = A.signal_card_b82(dict(base, meta=meta))
chk("CC1 لا OI ولا Funding بلا عقد دائم — CVD يبقى", "OI" not in c.split("📐")[0].split("📊")[-1] and "🔗 CVD −6.5%" in c and "Funding" not in c.split("⏳")[0], c)
c2 = A.signal_card_b82(dict(base, meta=dict(meta, oi_known_b87=True, oi_chg_b82=0.4)))
chk("CC1b OI معروف يظهر", "OI +0.40% (ساعة)" in c2)

# ── CC2: تأكيدات Short الممكنة فقط ─────────────────────────────
chk("CC2 بلا Funding وOI في قائمة الناقص بلا عقد دائم", "Funding > +0.01%" not in c and "ارتفاع OI" not in c
    and "حجم ≥1.5x بشمعة هابطة، أو CVD بيع ≤ −20%، أو RSI > 70 أو BB قرب الحد العلوي" in c, c)
chk("CC2b مع عقد دائم تبقى", "Funding > +0.01%" in "، ".join(A._short_missing_b82([])) and "ارتفاع OI > +5% مع الهبوط" in A._short_missing_b82([]))

# ── الثقة بين حد الدخول وحد الجهة ───────────────────────────────
chk("K1 56% فوق حد الدخول والحكم انتظار — يُذكر السبب", "• الثقة 56% (حد الدخول 55%) — فوق حد الدخول لكن جهة الشراء غير مؤكدة (حد تحديد الجهة 65%)" in c, c)
chk("K2 ما يلزم للشراء: جهة مؤكدة 65%", "• شراء: ثقة ≥ 65% لتأكيد جهة الشراء (حالياً 56%) + تأكيدات شراء 2/4 (حالياً 0/4)" in c, c)

# ── «المحتسب» مع بند التأكيدات ──────────────────────────────────
sh2 = dict(sh, flags=["Volume Spike 1.9x (شمعة هابطة) ✓"], n_flags=1,
           fails_b86=["تأكيدات Short 1/4 (الحد الأدنى 2)", "R/R المرجّح 1:0.73 < 1:1"])
c3 = A.signal_card_b82(dict(base, meta=dict(meta, short_b68=sh2)))
chk("K3 المحتسب بجانب بند التأكيدات", "تأكيدات Short 1/4 (الحد الأدنى 2) — المحتسب: Volume Spike 1.9x (شمعة هابطة)؛ R/R المرجّح 1:0.73 < 1:1" in c3, c3)

# ── ADX بصيغة واحدة ───────────────────────────────────────────
c4 = A.signal_card_b82(dict(base, meta=meta, adx=37.6, di_plus=9, di_minus=30))
chk("K4 البطاقة بصيغة التقرير الكامل (38 لا 37.6؛ 19.9 قرب الحد)", "(ADX 38، −DI أعلى)" in c4
    and "(ADX 19.9، " in A.signal_card_b82(dict(base, meta=meta, adx=19.9, di_plus=9, di_minus=12)))
chk("K5 لا «ADX {x:.0f}» في نصوص العرض", not [ln for ln in s.split("\n") if re.search(r"ADX \{[^{}]+:\.0f\}", ln) and "facts.append" not in ln])
chk("K6 ADX التقرير من regime.metrics (لا «ADX = 49» بجانب «ADX: 48»)", 'adx       = float(((getattr(regime, "metrics", None) or {}).get("adx")) or 0) or _calc_adx(candles)' in s)

# ── /regime ─────────────────────────────────────────────────────
chk("R1 /regime على سوق /signal (Futures) مع وسم الأساس و«spot» اختياري",
    'regime_market_ee3(getattr(context, "user_data", None), symbol, _explicit_ee3)' in s and "_mkt_tag_b41(_mk87, candles)" in s)   # EE3: الافتراضي Futures ثم آخر اختيار
chk("R2 الإجراء والتوصية يحيلان قرار الدخول إلى /signal", "— قرار الدخول من /signal" in s and "قرار الدخول نفسه من /signal" in s)
chk("R3 RSI الحي موسوم", "(RSI الحي={mm.group(1)}<30 — RSI 1D المكتمل {_rsi87:.0f})" in s)
chk("R4 F&G وهيمنة BTC للعملات الرقمية فقط", "if _crypto87:" in s and '(" + Fear & Greed + هيمنة BTC" if _crypto87 else "")' in s)

# ── /risk يعدّ مراكز Futures ────────────────────────────────────
t = src("handlers/trading.py")
chk("RK1 /risk يعدّ مراكز Futures ومنها القياس", "report[\"open_positions\"] += len(_f87)" in t and "(منها قياس آلي {_auto87})" in t)

# ── سجل الإشارات: حالة السوق وRSI لم تعودا فارغتين ───────────────
p = src("handlers/plan.py")
chk("T1 regime في سجل /signal و/planmonth", 'regime=str(getattr(regime, "description_ar", "") or "")' in s
    and 'regime=str(getattr(_reg_asset_pm, "description_ar", "") or "")' in p and '"_calc_rsi" in dir()' not in p)

# ── قائمة /start ────────────────────────────────────────────────
m = src("main.py")
import main as MN
listed = {c for _, items in MN.COMMAND_GROUPS_B87 for c, _ in items}
reg = set(re.findall(r'CommandHandler\(\s*"([a-z_0-9]+)"', m + src("handlers/analysis.py") + p + t + src("handlers/trade.py") + src("handlers/access.py")))
hidden = {"start", "help", "upgrade", "premium", "reject", "access", "killswitch", "drift", "review", "survey_report", "whaletest", "scanstatus", "backup", "planweek_full", "planmonth_full"}
chk("S1 كل أمر مسجّل مذكور في /start (عدا المخفية والمدراء)", not (reg - listed - hidden), sorted(reg - listed - hidden))
chk("S2 بلا أقسام الباقات والاشتراكات", "فضي+" not in m and "ذهبي+" not in m and "ماسي حصراً" not in m and "أوامر إضافية بالترقية" not in m
    and "باقتك: {tier_name}" not in m)

# ── حذف الكود الميت ──────────────────────────────────────────────
chk("DC1 core/commands.py وsecurity/ محذوفة", not os.path.exists(os.path.join(ROOT, "core", "commands.py"))
    and not os.path.exists(os.path.join(ROOT, "security")))
chk("DC2 لا استيراد لها", "core.commands" not in t and "from security" not in (m + s + p + t))

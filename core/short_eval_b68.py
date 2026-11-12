"""
core/short_eval_b68.py — تقييم Short في /signal (Futures افتراضي 1x) — الدفعة 68

قرار رحال 2026-11-06: «مرآة شروط الشراء»:
  • الجهة الهابطة مؤكدة: ميل الهبوط = 1 − ثقة الشراء ≥ 65% (حد تحديد الجهة)، والتصنيف هابط.
  • الثقة ≥ 55% (حد الدخول) — متحققة ضمناً بتحقق 65%.
  • تأكيدات Short 2/4 على الأقل (مرآة أعلام الشراء):
      حجم ≥1.5x في شمعة هابطة · CVD بيع ≤ −20% · Funding موجب > +0.01% (لا يُحتسب للسلع) ·
      OI ارتفع > +5% (مراكز جديدة مع الهبوط) · RSI > 70 أو BB قرب الحد العلوي (> 0.85).
  • R/R المرجّح (60% الهدف 1 + 40% الهدف 2) ≥ 1:1 — على القيمة غير المقرّبة.
  • الدخول عند المقاومة المثبتة (آخر شمعة يومية مكتملة) بعد شمعة رفض، والوقف ≥ 1.5×ATR فوق الدخول.
  • لا Short في ذروة بيع (RSI < 30)، ولا Short إذا كان السعر فوق المقاومة (السيناريو ملغى).
دالة خالصة بلا شبكة — تُختبر مباشرة.
"""
from typing import Any, Dict, List, Optional

SHORT_DIR_MIN = 0.65      # حد تحديد الجهة (مرآة 65% للشراء)
SHORT_ENTRY_MIN = 0.55    # حد الدخول (مرآة 55%)
SHORT_RSI_FLOOR = 30.0    # لا Short في ذروة بيع
SHORT_SL_ATR = 1.5        # الوقف ≥ 1.5×ATR فوق الدخول
SHORT_SL_CAP = 0.10       # سقف مسافة الوقف 10%
SHORT_MIN_FLAGS = 2


def _f(v, d=0.0) -> float:
    try:
        return float(v if v is not None else d)
    except (TypeError, ValueError):
        return d


def short_flags_b68(*, rsi: float, vol_ratio: float, vol_dir: str, cvd_ok: bool, cvd_pct: float,
                    fund_pct: float, fund_excluded: bool, oi_chg: float, bb_pos: Optional[float]) -> List[str]:
    flags: List[str] = []
    try:
        from core.indicators import fmt_vol_ratio as _fvr
        _vr = _fvr(vol_ratio)
    except Exception:
        _vr = f"{_f(vol_ratio):.1f}x"
    if _f(vol_ratio) >= 1.5 and str(vol_dir or "") == "down":
        flags.append(f"Volume Spike {_vr} (شمعة هابطة) ✓")
    if cvd_ok and _f(cvd_pct) <= -20.0:
        flags.append(f"CVD بيع ({_f(cvd_pct):+.1f}%) ✓")
    if _f(fund_pct) > 0.01 and not fund_excluded:
        flags.append(f"Funding موجب ({_f(fund_pct):+.4f}% — Longs مزدحمة) ✓")
    if _f(oi_chg) > 5.0:
        flags.append(f"OI ارتفع ({_f(oi_chg):+.1f}% — مراكز جديدة مع الهبوط) ✓")
    if _f(rsi) > 70 or (bb_pos is not None and _f(bb_pos) > 0.85):
        flags.append(("RSI ذروة شراء (" + f"{_f(rsi):.0f}" + ")") if _f(rsi) > 70 else "BB قرب الحد العلوي")
        flags[-1] += " ✓"
    return flags


def evaluate_short_b68(*, price: float, conf: float, rsi: float, atr_pct: float,
                       nr: float, fib_levels: Optional[Dict[str, float]], regime_desc: str,
                       flags: List[str]) -> Dict[str, Any]:
    """يُرجع تقييم Short كاملاً: الشروط، المستويات، R/R، الحكم، وعامل الحجم (0 / 0.5 / 1)."""
    price = _f(price)
    a = max(_f(atr_pct), 0.1) / 100.0
    bear = max(0.0, min(1.0, 1.0 - _f(conf)))
    lv = sorted({_f(v) for v in (fib_levels or {}).values() if _f(v) > 0})
    checks = []
    dir_ok = bear >= SHORT_DIR_MIN
    reg_ok = "هابط" in str(regime_desc or "")
    rsi_ok = _f(rsi) >= SHORT_RSI_FLOOR
    res_ok = nr > 0 and price > 0 and price < nr
    checks.append((f"الجهة هابطة مؤكدة: ميل الهبوط {bear:.0%} ≥ 65%", dir_ok))
    checks.append(("تصنيف السوق هابط", reg_ok))
    checks.append((f"RSI {_f(rsi):.0f} ≥ 30 (لا Short في ذروة بيع)", rsi_ok))
    checks.append(("السعر تحت المقاومة المثبتة" + (f" ({nr:,.6g})" if nr > 0 else " (لا مقاومة معروفة)"), res_ok))

    entry = nr if res_ok else 0.0
    out: Dict[str, Any] = {"bear_conf": bear, "checks": checks, "flags": list(flags or []),
                           "n_flags": len(flags or []), "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0,
                           "sl_pct": 0.0, "tp1_pct": 0.0, "tp2_pct": 0.0, "rr": 0.0,
                           "eligible": False, "size_factor": 0.0, "reason": ""}
    if entry > 0:
        sl = entry * (1 + SHORT_SL_ATR * a)
        _above = [x for x in lv if x > entry * 1.001]
        if _above and _above[0] <= entry * (1 + 3 * a):
            sl = max(sl, _above[0] * 1.002)        # الوقف فوق مستوى Fib التالي إن كان قريباً
        sl = min(sl, entry * (1 + SHORT_SL_CAP))
        _below = [x for x in lv if x < price * 0.997]
        tp1 = _below[-1] if _below and (entry - _below[-1]) / entry >= 0.5 * a else entry * (1 - a)
        _below2 = [x for x in lv if x < tp1 * 0.997]
        tp2 = _below2[-1] if (_below2 and _below2[-1] >= tp1 * (1 - 2.5 * a)) else tp1 * (1 - a)
        sl_pct = (sl / entry - 1) * 100
        tp1_pct = (1 - tp1 / entry) * 100
        tp2_pct = (1 - tp2 / entry) * 100
        rr = (0.6 * tp1_pct + 0.4 * tp2_pct) / sl_pct if sl_pct > 0 else 0.0
        out.update(entry=entry, sl=sl, tp1=tp1, tp2=tp2, sl_pct=sl_pct, tp1_pct=tp1_pct, tp2_pct=tp2_pct, rr=rr)
    rr_ok = out["rr"] >= 1.0
    fl_ok = out["n_flags"] >= SHORT_MIN_FLAGS
    checks.append((f"تأكيدات Short {out['n_flags']}/4 (الحد الأدنى 2)", fl_ok))
    checks.append(("R/R المرجّح ≥ 1:1" + (f" (الحالي 1:{out['rr']:.2f})" if out["entry"] else ""), rr_ok and out["entry"] > 0))
    ok = all(c for _, c in checks)
    out["eligible"] = ok
    if ok:
        out["size_factor"] = 1.0 if (bear >= 0.75 and out["n_flags"] >= 3) else 0.5
    else:
        # batch76 (N1): سبب الرفض بصيغة الحالة الفعلية — عناوين الشروط مكتوبة بصيغة المطلوب (لقائمة ✅/❌)
        # فكانت «ينقصه: RSI 29 ≥ 30» و«ينقصه: السعر تحت المقاومة المثبتة» تُقرأ عبارات خاطئة
        _fail76 = [
            f"ميل الهبوط {bear:.0%} < 65% — الجهة الهابطة غير مؤكدة",
            "تصنيف السوق ليس هابطاً",
            f"RSI {_f(rsi):.0f} < 30 — ذروة بيع، لا Short",
            (f"السعر فوق المقاومة المثبتة ({nr:,.6g}) — السيناريو ملغى" if nr > 0 else "لا مقاومة معروفة للدخول"),
            f"تأكيدات Short {out['n_flags']}/4 (الحد الأدنى 2)",
            ("R/R المرجّح" + (f" 1:{out['rr']:.2f} < 1:1" if out["entry"] else " غير محسوب — لا نقطة دخول")),
        ]
        out["reason"] = next((_fail76[i] if i < len(_fail76) else lbl for i, (lbl, c) in enumerate(checks) if not c), "")
    return out


def format_short_block_b68(ev: Dict[str, Any], fmt_price=None) -> str:
    fp = fmt_price or (lambda v: f"${v:,.6g}")
    L = ["🔻 *تقييم Short — Futures افتراضي 1x* (مرآة شروط الشراء)"]
    for lbl, ok in ev.get("checks", []):
        L.append(f"{'✅' if ok else '❌'} {lbl}")
    if ev.get("flags"):
        L.append("  ↳ المحتسب: " + " · ".join(str(x).replace(" ✓", "") for x in ev["flags"]))
    if ev.get("entry"):
        L.append(f"• Entry (Short): {fp(ev['entry'])} — عند المقاومة بعد شمعة رفض؛ لا مطاردة")
        L.append(f"• وقف الخسارة: {fp(ev['sl'])} (+{ev['sl_pct']:.1f}% فوق الدخول)")
        L.append(f"• TP1: {fp(ev['tp1'])} (−{ev['tp1_pct']:.1f}%) ×60% | TP2: {fp(ev['tp2'])} (−{ev['tp2_pct']:.1f}%) ×40%")
        L.append(f"• R/R المرجّح: 1:{ev['rr']:.2f}")
    if ev.get("eligible"):
        L.append(f"✅ *Short مؤهل* — حجم {'كامل' if ev.get('size_factor', 0) >= 1 else 'نصف'} (افتراضي 1x فقط؛ الرافعة الحقيقية غير مفعّلة)")
    else:
        L.append(f"⏳ لا Short الآن — {ev.get('reason') or 'الشروط غير مكتملة'}")
    return "\n".join(L)

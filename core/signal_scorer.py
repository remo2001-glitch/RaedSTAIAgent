"""
signal_scorer.py — نظام تقييم جودة إشارات رائد التداول
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
يُقيّم كل إشارة قبل الإرسال ويُعطيها درجة 0-100 + حكم نهائي.

الأحكام:
  PASS         ≥ 75  — إشارة قوية
  CONDITIONAL  55-74 — إشارة مقبولة بشروط
  WATCH ONLY   35-54 — مراقبة فقط
  FAIL         < 35  — إشارة ضعيفة
  AUTO-FAIL    0     — رفض فوري (Hard Gate)
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
import logging

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# بيانات الإشارة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dataclass
class SignalInput:
    # سلامة الإشارة (Hard Gates)
    has_build_error:              bool  = False
    has_entry:                    bool  = True
    has_sl:                       bool  = True
    has_tp:                       bool  = True
    has_invalidation:             bool  = True
    has_time_exit:                bool  = True
    has_sizing:                   bool  = True

    # R/R & EV
    rr_ratio:           Optional[float] = None   # TP1/SL (None = غير محسوب)
    confidence_pct:     Optional[float] = None   # ثقة النموذج 0-100

    # ATR
    sl_atr_multiple:    Optional[float] = None   # SL / ATR
    tp1_atr_multiple:   Optional[float] = None   # TP1 / ATR
    tp2_atr_multiple:   Optional[float] = None   # TP2 / ATR

    # السيولة والتنفيذ
    volume_multiple:    Optional[float] = None   # حجم / المتوسط
    is_synthetic:                 bool  = False
    spread_ok:                    bool  = True
    market_open:                  bool  = True   # False = gap risk

    # الاتساق الداخلي
    trend_phase_contradiction:    bool  = False  # تناقض فريمات
    confidence_numbers_incoherent:bool  = False  # أرقام ثقة متضاربة
    scenario_matches_indicators:  bool  = True
    sentiment_aligned:            bool  = True   # F&G لا يعاكس القرار

    # نوع الأصل
    asset_class: Literal[
        "spot_liquid",       # كريبتو سائل مباشر
        "synthetic_ok",      # مُرمَّز بتتبع مقبول
        "synthetic_weak",    # مُرمَّز سيولة منخفضة
        "new_listing",       # حديث الإدراج < 6 أشهر
    ] = "spot_liquid"
    underlying_chart_confirmed:   bool  = False  # تأكيد من الأصل الحقيقي

    # Confluence
    confirmations_met:             int  = 0      # من أصل 4

    # معلومات إضافية
    symbol:                        str  = ""
    rsi:            Optional[float]     = None
    is_overbought:                bool  = False  # RSI >= 70
    is_oversold:                  bool  = False  # RSI <= 30


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# دوال مساعدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def break_even_win_rate(rr: float) -> float:
    """win rate المطلوب للتعادل = 100 / (1 + rr)"""
    if rr <= 0:
        return 100.0
    return 100.0 / (1.0 + rr)


def _verdict_label(score: int) -> str:
    if score >= 75: return "PASS"
    if score >= 55: return "CONDITIONAL"
    if score >= 35: return "WATCH ONLY"
    return "FAIL"


def _verdict_ar(verdict: str) -> str:
    return {
        "AUTO-FAIL":   "❌ AUTO-FAIL — رفض فوري",
        "PASS":        "✅ PASS — إشارة قوية",
        "CONDITIONAL": "🟡 CONDITIONAL — مقبولة بشروط",
        "WATCH ONLY":  "👁️ WATCH ONLY — مراقبة فقط",
        "FAIL":        "🔴 FAIL — إشارة ضعيفة",
    }.get(verdict, verdict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# المقيِّم الرئيسي
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def evaluate_signal(s: SignalInput) -> dict:
    """
    يُقيّم الإشارة ويُعيد:
    {
      "score": int,
      "verdict": str,
      "verdict_ar": str,
      "reasons": list[str],    # نقاط الخصم مع شرح
      "header": str,            # سطر header مختصر
      "footer": str,            # تفاصيل كاملة في النهاية
    }
    """
    reasons = []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Hard Gates (AUTO-FAIL)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if s.has_build_error:
        return _auto_fail("G1: خطأ برمجي — الإشارة معطّلة، لا أساس للدخول")

    if not (s.has_entry and s.has_sl and s.has_invalidation):
        return _auto_fail("G2: خطة صفقة ناقصة (entry/SL/invalidation)")

    if s.rr_ratio is not None and s.rr_ratio < 1.0:
        return _auto_fail(f"G3: R/R = {s.rr_ratio:.1f} < 1:1 — لا حافة إحصائية")

    if s.is_synthetic and (s.volume_multiple or 1.0) < 0.3 and not s.market_open:
        return _auto_fail("G4: أصل اصطناعي + سيولة < 0.3x + سوق مغلق — gap risk مرتفع جداً")

    if s.asset_class == "new_listing":
        return _auto_fail("G5: أصل حديث الإدراج (<6 أشهر) — مؤشرات فنية غير موثوقة")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # أ. سلامة الإشارة — 15 نقطة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_a = 0
    score_a += 5  # لا build error (نجح Hard Gate)
    if s.has_entry and s.has_sl and s.has_invalidation:
        score_a += 5
    else:
        reasons.append("أ: entry/SL/invalidation ناقصة (-5)")
    if s.has_time_exit and s.has_sizing:
        score_a += 5
    else:
        reasons.append("أ: time exit أو sizing ناقص (-5)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ب. R/R & EV — 20 نقطة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_b = 0
    if s.rr_ratio is not None and s.confidence_pct is not None:
        be = break_even_win_rate(s.rr_ratio)
        ev_margin = s.confidence_pct - be
        if ev_margin >= 10:
            score_b += 10
        elif ev_margin >= 0:
            score_b += 5
            reasons.append(f"ب: هامش EV ضيق ({ev_margin:.1f}%) — يُفضَّل +10% (-5)")
        else:
            reasons.append(f"ب: EV سلبي (ثقة {s.confidence_pct:.0f}% < break-even {be:.0f}%) (-10)")

        # rr_rounding_fix (#311): نفس مشكلة conf_rounding_fix (#296) — رقمان
        # مختلفان فعلياً (مثال: 1.49 و1.51) قد يُعرضان كلاهما ".1f" كـ"1.5"،
        # فيبدوان متناقضين نصياً حين يقعان في فرعين مختلفين من الشرط (واحد
        # "ضعيف <1.5" والآخر "مقبول لكن دون 2:1") — لوحظ بين XSPY وXSPCX.
        # الإصلاح: عرض منزلتين عشريتين عند التقارب من حدود 1.5 أو 2.0.
        def _rr_fmt(v: float) -> str:
            for _b in (1.5, 2.0):
                if abs(round(v, 1) - _b) < 1e-9:
                    return f"{v:.2f}"
            return f"{v:.1f}"

        if s.rr_ratio >= 2.0:
            score_b += 10
        elif s.rr_ratio >= 1.5:
            score_b += 7
            reasons.append(f"ب: R/R = {_rr_fmt(s.rr_ratio)} مقبول لكن دون 2:1 (-3)")
        else:
            score_b += 3
            reasons.append(f"ب: R/R = {_rr_fmt(s.rr_ratio)} ضعيف (< 1.5) (-7)")
    elif s.rr_ratio is None:
        score_b += 5  # انتظر تأكيد — نقطة وسط
        reasons.append("ب: R/R غير محسوب بعد — 5/20 مؤقتاً")
    else:
        reasons.append("ب: بيانات R/R أو الثقة ناقصة (-20)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ج. ATR والوقف/الأهداف — 15 نقطة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_c = 0
    if s.sl_atr_multiple is not None:
        if 1.2 <= s.sl_atr_multiple <= 2.5:
            score_c += 8
        elif s.sl_atr_multiple < 1.0:
            score_c += 1
            reasons.append(f"ج: SL = {s.sl_atr_multiple:.1f}x ATR — ضيق جداً (-7)")
        else:
            score_c += 4
            reasons.append(f"ج: SL = {s.sl_atr_multiple:.1f}x ATR — خارج النطاق المثالي (-4)")
    else:
        score_c += 4  # غير محسوب
        reasons.append("ج: SL/ATR غير محسوب (-4)")

    if s.tp1_atr_multiple is not None:
        if s.tp1_atr_multiple >= 1.0:
            score_c += 4
        else:
            score_c += 1
            reasons.append(f"ج: TP1 = {s.tp1_atr_multiple:.1f}x ATR — أقل من تقلب يوم (-3)")
        if s.tp2_atr_multiple is not None and s.tp2_atr_multiple >= 2.0:
            score_c += 3
        else:
            score_c += 1
            reasons.append("ج: TP2 < 2x ATR (-2)")
    else:
        score_c += 3  # غير محسوب
        reasons.append("ج: TP/ATR غير محسوب (-4)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # د. السيولة والتنفيذ — 15 نقطة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_d = 0
    vol = s.volume_multiple or 0.0
    if vol >= 1.0:
        score_d += 5
    elif vol >= 0.5:
        score_d += 3
        reasons.append(f"د: حجم {vol:.1f}x — أقل من المتوسط (-2)")
    elif vol > 0:
        score_d += 0
        reasons.append(f"د: حجم {vol:.1f}x — ضعيف جداً (-5)")
    else:
        score_d += 3  # غير محسوب

    if s.spread_ok:
        score_d += 5
    else:
        reasons.append("د: سبريد/عمق سيء (-5)")

    if s.market_open:
        score_d += 5
    else:
        score_d += 2
        reasons.append("د: السوق مغلق — gap risk عند الافتتاح (-3)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # هـ. الاتساق الداخلي — 15 نقطة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_e = 15
    if s.trend_phase_contradiction:
        score_e -= 6
        reasons.append("هـ: تناقض بين الاتجاه/الطور (-6)")
    if s.confidence_numbers_incoherent:
        score_e -= 4
        reasons.append("هـ: أرقام ثقة متضاربة (-4)")
    if not s.scenario_matches_indicators:
        score_e -= 3
        reasons.append("هـ: السيناريو لا يتوافق مع المؤشرات (-3)")
    if not s.sentiment_aligned:
        score_e -= 2
        reasons.append("هـ: F&G يعاكس القرار دون تفسير (-2)")
    score_e = max(0, score_e)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # و. الموثوقية حسب نوع الأصل — 10 نقاط
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    score_f = 0
    asset_scores = {
        "spot_liquid":    5,
        "synthetic_ok":   3,
        "synthetic_weak": 1,
        "new_listing":    0,
    }
    score_f += asset_scores.get(s.asset_class, 3)
    if s.asset_class in ("synthetic_weak", "new_listing"):
        reasons.append(f"و: {s.asset_class} — موثوقية منخفضة (-{5 - score_f})")

    if s.underlying_chart_confirmed:
        score_f += 5
    else:
        score_f += 2
        if s.is_synthetic:
            reasons.append("و: لا تأكيد من رسم الأصل الحقيقي (-3)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ز. التأكيدات / Confluence — 10 نقاط
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    conf_map = {4: 10, 3: 10, 2: 6, 1: 2, 0: 0}
    score_g = conf_map.get(min(s.confirmations_met, 4), 0)
    if s.confirmations_met < 2:
        reasons.append(f"ز: تأكيدات {s.confirmations_met}/4 — غير كافية (-{10 - score_g})")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # الإجمالي
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    total = score_a + score_b + score_c + score_d + score_e + score_f + score_g
    total = max(0, min(100, total))
    verdict = _verdict_label(total)

    # fin_gate_confirmations_fix (#304): تأكيدات <2/4 تعني عملياً 0% حجم
    # مركز في كل مكان آخر بالنظام (قاعدة رحال الثابتة: "0/4 تأكيدات → 0%
    # حجم بغض النظر عن درجة الثقة") — فلا يجوز أن يخرج الحكم "PASS" أو
    # "CONDITIONAL" (نصائح "يمكن الدخول"/"مقبولة") من درجة رقمية بمعزل عن
    # هذه القاعدة، بينما محرك التحليل نفسه يمنع الدخول تماماً. سبق ضبط هذا
    # سابقاً فقط كخصم نقاط (سطر "ز" أعلاه) دون تقييد الحكم النهائي فعلياً.
    if s.confirmations_met < 2 and verdict in ("PASS", "CONDITIONAL"):
        reasons.append(
            f"ح: تأكيدات {s.confirmations_met}/4 دون الحد الأدنى (2) — "
            "الحكم مُقيَّد لـWATCH ONLY رغم الدرجة الرقمية (قاعدة 0% حجم)"
        )
        verdict = "WATCH ONLY"

    # تعديل خاص: RSI في ذروة مع WAIT → لا يُخفَّض الحكم
    overbought_note = ""
    if s.is_overbought or s.is_oversold:
        overbought_note = f" | {'ذروة شراء' if s.is_overbought else 'ذروة بيع'} — انتظر تبريد RSI"

    return _build_result(total, verdict, reasons, s, overbought_note)


def _auto_fail(reason: str) -> dict:
    return {
        "score":      0,
        "verdict":    "AUTO-FAIL",
        "verdict_ar": _verdict_ar("AUTO-FAIL"),
        "reasons":    [reason],
        "header":     f"❌ *AUTO-FAIL* | {reason}",
        "footer":     (
            "━━━━━━━━━━━━━━━━━━\n"
            f"📊 *تقييم الإشارة: AUTO-FAIL (0/100)*\n"
            f"• السبب: {reason}\n"
            "• القرار: لا تتداول بناءً على هذه الإشارة"
        ),
    }


def _build_result(score: int, verdict: str, reasons: list, s: SignalInput, extra: str = "") -> dict:
    verdict_ar = _verdict_ar(verdict)
    bar_len = score // 10
    bar = "█" * bar_len + "░" * (10 - bar_len)

    header = (
        f"📊 *جودة الإشارة: {bar} {score}/100 — {verdict_ar}*{extra}"
    )

    footer_lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"📊 *تقييم جودة الإشارة — {s.symbol}*",
        f"الدرجة: {bar} {score}/100",
        f"الحكم: {verdict_ar}",
    ]

    if reasons:
        footer_lines.append("")
        footer_lines.append("📋 *ملاحظات:*")
        for r in reasons:
            footer_lines.append(f"• {r}")

    verdict_advice = {
        "PASS":        "✅ الإشارة قوية — يمكن الدخول مع احترام SL/TP",
        "CONDITIONAL": "🟡 مقبولة — تحقق من الملاحظات قبل الدخول",
        "WATCH ONLY":  "👁️ مراقبة فقط — لا دخول حتى تتحسن الشروط",
        "FAIL":        "🔴 إشارة ضعيفة — تجنب الدخول",
    }
    footer_lines.append("")
    footer_lines.append(verdict_advice.get(verdict, ""))

    return {
        "score":      score,
        "verdict":    verdict,
        "verdict_ar": verdict_ar,
        "reasons":    reasons,
        "header":     header,
        "footer":     "\n".join(footer_lines),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper: بناء SignalInput من بيانات analysis.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_signal_input(
    symbol: str,
    price: float,
    pro_sl: float,
    pro_tp: float,
    pro_entry: float,
    sl_pct: float,
    tp_pct: float,
    atr_pct: float,
    vol_ratio: float,
    confidence: float,
    confirmations: int,
    is_synthetic: bool,
    market_open: bool,
    rsi: float,
    has_build_error: bool = False,
    trend_contradiction: bool = False,
    confidence_incoherent: bool = False,
    sentiment_aligned: bool = True,
    asset_class: str = "spot_liquid",
    underlying_confirmed: bool = False,
    has_tp: bool = True,
) -> SignalInput:
    """بناء SignalInput من المتغيرات الموجودة في analysis.py"""

    # R/R
    rr = (tp_pct / sl_pct) if sl_pct > 0 else None

    # ATR multiples
    sl_atr = (sl_pct / atr_pct) if atr_pct > 0 else None
    tp1_atr = (tp_pct / atr_pct) if atr_pct > 0 else None

    return SignalInput(
        symbol                       = symbol,
        has_build_error              = has_build_error,
        has_entry                    = pro_entry > 0,
        has_sl                       = pro_sl > 0,
        has_tp                       = has_tp,
        has_invalidation             = True,  # دائماً موجود في النظام
        has_time_exit                = True,
        has_sizing                   = True,
        rr_ratio                     = rr,
        confidence_pct               = confidence * 100 if confidence <= 1 else confidence,
        sl_atr_multiple              = sl_atr,
        tp1_atr_multiple             = tp1_atr,
        volume_multiple              = vol_ratio,
        is_synthetic                 = is_synthetic,
        spread_ok                    = True,
        market_open                  = market_open,
        trend_phase_contradiction    = trend_contradiction,
        confidence_numbers_incoherent= confidence_incoherent,
        scenario_matches_indicators  = True,
        sentiment_aligned            = sentiment_aligned,
        asset_class                  = asset_class,
        underlying_chart_confirmed   = underlying_confirmed,
        confirmations_met            = confirmations,
        rsi                          = rsi,
        is_overbought                = rsi >= 70 if rsi else False,
        is_oversold                  = rsi <= 30 if rsi else False,
    )

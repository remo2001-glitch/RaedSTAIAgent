"""
core/confidence.py — مصدر واحد لحساب "رفع الثقة بالتأكيدات" (المرحلة 2)

السبب: كان الرفع (+3% لكل تأكيد) مكتوباً فقط داخل handlers/analysis.py
(_build_professional_block)، بينما الخطط (/planmonth و/planweek و/portfolio)
تعرض signal.confidence الخام — فتظهر ETH بثقة 34% في الخطة و40% في /signal
بعد دقيقة (جولات 35/36). الآن تقرأ كل الواجهات من هذه الدالة نفسها.

التأكيدات الأربعة المعلنة: حجم + CVD + هيكل/مشتقات + RSI، وتُحسب كأعلام:
  Funding سالب (< -0.01%)      OI انخفض (< -5%)
  Whale Ratio >= 1.3           Volume >= 1.5x
  RSI < 30 (ذروة بيع)          CVD شراء >= +20%
  BB < 0.15 (تحت الحد السفلي)
"""
from typing import Any, Dict, List, Optional, Tuple

CONF_BOOST_PER_FLAG = 3        # نقاط مئوية لكل تأكيد
CONF_BOOST_CAP      = 0.85     # سقف الرفع (لا يُخفِّض ثقة أعلى منه أصلاً)
MIN_CONFIRMATIONS   = 2        # الحد الأدنى للتأكيدات


def collect_confirmation_flags(
    *, rsi: float, vol_ratio: float, bb_pos: Optional[float],
    whale_ratio: float, cvd_signal: Any, cvd_pct: float,
    fund_pct: float, oi_chg: float,
    base_flags: Optional[List[str]] = None,
) -> List[str]:
    """يُرجع قائمة نصوص التأكيدات المتحققة (نفس نصوص /signal الأصلية)."""
    flags: List[str] = list(base_flags or [])
    try:
        if fund_pct < -0.01:
            flags.append("Funding Rate سالب (فرصة Longs) ✓")
        if oi_chg < -5:
            flags.append("OI انخفض (تصفية Shorts) ✓")
        if whale_ratio and whale_ratio >= 1.3:
            flags.append(f"Whale Ratio {whale_ratio:.2f} — أغلبية Long ✓")
        if vol_ratio >= 1.5:
            flags.append(f"Volume Spike {vol_ratio:.1f}x ✓")
        if rsi < 30:
            flags.append(f"RSI ذروة بيع ({rsi:.0f}) ✓")
        if cvd_signal and cvd_pct >= 20.0:
            flags.append(f"CVD شراء ({cvd_pct:+.1f}%) ✓")
        if bb_pos is not None and bb_pos < 0.15:
            flags.append("BB تحت الحد السفلي ✓")
    except Exception:
        pass
    return flags


def filter_flags_by_direction(flags: List[str], direction: str) -> Tuple[List[str], List[str]]:
    """batch36 (قرار رحال): مؤشرات التشبع والحجم لا تُحتسب تأكيداً إلا إذا وافقت اتجاه الإشارة.
    • أعلام شراء (Funding سالب، OI، Whale، RSI ذروة بيع، CVD شراء، BB السفلي): تُحتسب فقط عند اتجاه long.
    • Volume Spike: يُحتسب عند اتجاه long أو short (الحجم يؤكد الحركة في الجهتين).
    • اتجاه محايد/غير معروف: لا يُحتسب شيء (تُعرض المرصودة منفصلة).
    يُرجع (المحتسبة، المرصودة غير المحتسبة)."""
    d = str(direction or "").lower()
    counted: List[str] = []
    ignored: List[str] = []
    for f in (flags or []):
        is_vol = str(f).startswith("Volume Spike")
        ok = (d == "long") or (is_vol and d == "short")
        (counted if ok else ignored).append(f)
    return counted, ignored


def adjust_bb_by_rsi(bb_pos: Optional[float], rsi: float) -> float:
    """نفس تعديل BB حسب RSI المستخدم في /signal (إصلاحا #787/#1022)."""
    bb = 0.5 if bb_pos is None else float(bb_pos)
    if rsi < 25 and bb > 0.3:
        bb = min(bb, 0.15)
    elif rsi < 30 and bb > 0.5:
        bb = min(bb, 0.30)
    elif rsi > 75 and bb < 0.7:
        bb = max(bb, 0.85)
    elif rsi > 70 and bb < 0.5:
        bb = max(bb, 0.65)
    return bb


def compute_boost(n_flags: int, direction: str, rsi: float, rsi_div: str = "none") -> int:
    """نقاط الرفع المئوية بعد تخفيفات التطرف والتباعد المعاكس."""
    boost = int(n_flags) * CONF_BOOST_PER_FLAG
    d = str(direction or "").lower()
    if rsi > 80 and d == "long":
        boost = max(0, boost - 3)
    if rsi < 20 and d == "short":
        boost = max(0, boost - 3)
    # divergence_key_fix: كان الكود يقرأ المفتاح "rsi_divergence" غير الموجود
    # (المُوجِّه يكتب "rsi_div") فلا يُطبَّق إلغاء الرفع أبداً رغم عرض تحذير
    # Bearish Divergence في نفس الرسالة.
    if rsi_div == "bearish" and d == "long":
        boost = 0
    if rsi_div == "bullish" and d == "short":
        boost = 0
    return boost


def apply_confirmation_boost(
    conf: float, n_flags: int, direction: str, rsi: float, rsi_div: str = "none",
) -> Tuple[float, int]:
    """(الثقة بعد الرفع، نقاط الرفع). لا تُخفِّض أبداً ثقة تتجاوز السقف أصلاً."""
    boost = compute_boost(n_flags, direction, rsi, rsi_div)
    if boost <= 0:
        return conf, 0
    new_conf = min(conf + boost / 100.0, max(conf, CONF_BOOST_CAP))
    return new_conf, boost


def boosted_confidence_from_signal(
    signal: Any, enriched: Optional[Dict] = None, oi_chg: float = 0.0,
) -> Tuple[float, int, int]:
    """
    للخطط: يحسب (الثقة بعد الرفع، نقاط الرفع، عدد التأكيدات) من SignalResult
    + بيانات enrichment (whale_data / funding_rate_pct / cvd_*). لا يُعدِّل signal.
    """
    enriched = enriched or {}
    tech = getattr(signal, "technicals", None) or {}
    conf = float(getattr(signal, "confidence", 0) or 0)
    direction = getattr(signal, "direction", "neutral")
    try:
        rsi = float(round(float(tech.get("rsi", 50) or 50)))
    except (TypeError, ValueError):
        rsi = 50.0
    wd = enriched.get("whale_data") or {}
    try:
        whale = float(wd.get("ratio", enriched.get("whale_ratio", 0)) or 0)
    except (TypeError, ValueError):
        whale = 0.0
    def _f(v, d=0.0):
        try:
            return float(v if v is not None else d)
        except (TypeError, ValueError):
            return d
    flags = collect_confirmation_flags(
        rsi=rsi,
        vol_ratio=_f(tech.get("vol_ratio"), 1.0),
        bb_pos=adjust_bb_by_rsi(tech.get("bb_pos"), rsi),
        whale_ratio=whale,
        cvd_signal=enriched.get("cvd_signal"),
        cvd_pct=_f(enriched.get("cvd_pct")),
        fund_pct=_f(enriched.get("funding_rate_pct")),
        oi_chg=_f(oi_chg),
    )
    flags, _ign = filter_flags_by_direction(flags, direction)   # batch36: اتجاه الإشارة يحدد ما يُحتسب
    new_conf, boost = apply_confirmation_boost(
        conf, len(flags), direction, rsi, tech.get("rsi_div", "none"))
    return new_conf, boost, len(flags)


async def apply_boost_to_signal(data_layer: Any, sym: str, signal: Any, enriched: Optional[Dict] = None,
                                oi_timeout: float = 6.0) -> Any:
    """
    يطبّق على signal.confidence نفس رفع التأكيدات المستخدم في /signal و/analyze والخطط
    (بند 3 — توحيد ثقة التداول الآلي): مصدر واحد لكل المسارات. فشل أي جلب (OI) لا يوقف
    العملية: تبقى الثقة الخام. يكتب plan_confirmations/plan_conf_boost في technicals.
    """
    import asyncio
    try:
        oi_chg = 0.0
        try:
            _oi = await asyncio.wait_for(data_layer.get_open_interest(sym), timeout=oi_timeout)
            oi_chg = float((_oi or {}).get("oi_change_pct", 0) or 0)
        except Exception:
            oi_chg = 0.0
        new_conf, boost, n_flags = boosted_confidence_from_signal(signal, enriched, oi_chg)
        if isinstance(getattr(signal, "technicals", None), dict):
            signal.technicals["plan_confirmations"] = n_flags
            signal.technicals["plan_conf_boost"] = boost
        if boost > 0:
            signal.confidence = round(new_conf, 3)
    except Exception:
        pass
    return signal

"""
مؤشرات فنية مشتركة — مصدر واحد لـRSI وATR ونسبة الحجم.

wilder_unify_fix: كان RSI وATR يُحسبان في نحو 10 مواضع متفرقة بصيغة
"متوسط بسيط لآخر 14 تغيّراً فقط"، بينما TradingView وOKX تستخدمان تنعيم
Wilder (RMA) على كامل السلسلة. الفرق الفعلي: عند خروج شمعة كبيرة من نافذة
الـ14 يوماً يقفز RSI فجأة (cliff effect)، ومتوسط الفرق وسيط 5.4 نقطة.
الآن كل المواضع تستورد من هنا. (ADX كان يستخدم Wilder أصلاً — لم يتغيّر.)

volume_completed_fix: كانت نسبة الحجم = حجم آخر شمعة ÷ SMA20 بما فيها الشمعة
اليومية التي ما زالت تتكوّن (تبدأ 19:00 بتوقيت الرياض في بيانات البوت) فتظهر
0.03x–0.1x مساءً. الآن تُقارَن آخر شمعة **مكتملة** بمتوسط 20 شمعة مكتملة.
العتبات (0.3x / 0.5x / 1.5x / 3.0x) لم تتغيّر.

هذا الملف بلا استيراد من المشروع عمداً.
"""
import time
from typing import List, Optional, Sequence


def _to_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def wilder_rsi_series(closes: Sequence[float], period: int = 14) -> List[float]:
    """RSI بتنعيم Wilder لكل نقطة. القيم قبل اكتمال النافذة = 50.0."""
    closes = [_to_float(c) for c in closes]
    n = len(closes)
    out = [50.0] * n
    if period < 1 or n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period

    def _val(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = _val(ag, al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = _val(ag, al)
    return out


def wilder_rsi(closes: Sequence[float], period: int = 14) -> float:
    """آخر قيمة RSI بتنعيم Wilder. 50.0 إذا البيانات غير كافية."""
    s = wilder_rsi_series(closes, period)
    return s[-1] if s else 50.0


def true_ranges(highs: Sequence[float], lows: Sequence[float],
                closes: Sequence[float]) -> List[float]:
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = _to_float(highs[i]), _to_float(lows[i]), _to_float(closes[i - 1])
        if h > 0 and l > 0 and pc > 0:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def wilder_atr(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float], period: int = 14) -> float:
    """ATR المطلق بتنعيم Wilder. 0.0 إذا البيانات غير كافية."""
    trs = true_ranges(highs, lows, closes)
    if len(trs) < period:
        return (sum(trs) / len(trs)) if trs else 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def wilder_atr_from_candles(candles: list, period: int = 14) -> float:
    """ATR المطلق من قائمة شموع (dict بمفاتيح high/low/close)."""
    try:
        highs = [c.get("high", c.get("close", 0)) for c in candles]
        lows = [c.get("low", c.get("close", 0)) for c in candles]
        closes = [c.get("close", c.get("price", 0)) for c in candles]
    except AttributeError:
        return 0.0
    return wilder_atr(highs, lows, closes, period)


# ── نسبة الحجم على آخر شمعة مكتملة ────────────────────────────────
def _candle_ts_seconds(c) -> Optional[float]:
    try:
        ts = c.get("timestamp", c.get("ts"))
        if ts is None:
            return None
        ts = float(ts)
        if ts > 1e11:       # milliseconds
            ts /= 1000.0
        return ts if ts > 1e8 else None
    except Exception:
        return None


def last_candle_is_forming(candles: list, now: Optional[float] = None) -> bool:
    """
    هل آخر شمعة ما زالت تتكوّن؟
    - إن وُجد حقل confirm (OKX: "0" = غير مكتملة) يُعتمد عليه.
    - وإلا بالطابع الزمني: now < ts_last + interval (interval من وسيط فروق الشموع).
    - بلا أي معلومة: نفترض أنها تتكوّن (واجهات الشموع الحية تُرجع الشمعة الحالية دائماً).
    """
    if not candles:
        return False
    last = candles[-1]
    if isinstance(last, dict):
        conf = last.get("confirm")
        if conf is not None:
            return str(conf) in ("0", "False", "false")
        ts_last = _candle_ts_seconds(last)
        if ts_last is not None and len(candles) >= 3:
            tss = [_candle_ts_seconds(c) for c in candles[-8:]]
            tss = [t for t in tss if t is not None]
            diffs = sorted(b - a for a, b in zip(tss, tss[1:]) if b - a > 0)
            if diffs:
                interval = diffs[len(diffs) // 2]
                now = time.time() if now is None else now
                return now < ts_last + interval
    return True


def volume_ratio_completed(candles: list, window: int = 20,
                           now: Optional[float] = None) -> float:
    """
    نسبة حجم آخر شمعة مكتملة ÷ SMA(window) للشموع المكتملة (تشمل نفسها — نفس
    تعريف TradingView Volume MA). 1.0 إذا البيانات غير كافية أو المتوسط صفر.
    """
    vols = []
    for c in candles or []:
        try:
            vols.append(float(c.get("volume", 0) or 0))
        except (AttributeError, TypeError, ValueError):
            vols.append(0.0)
    if last_candle_is_forming(candles, now) and len(vols) >= 3:
        vols = vols[:-1]
    if len(vols) < 3:
        return 1.0
    win = vols[-window:]
    avg = sum(win) / len(win)
    if avg <= 0:
        return 1.0
    return vols[-1] / avg


def volume_pace_forming(candles: list, window: int = 20,
                        now: Optional[float] = None) -> Optional[float]:
    """batch32: وتيرة حجم الشمعة الجارية (حجمها حتى الآن ÷ نسبة الوقت المنقضي) ÷ متوسط المكتملة.
    نسبة الحجم المعتمدة في النظام تخص آخر شمعة *مكتملة* (أمس)، فتُخفي يوماً انفجارياً قيد التكوّن
    (IOST: +21% اليوم وحجم أمس 0.79x). تُستخدم للعرض فقط ولا تغيّر أي قرار. None إن لم تكن معلومة موثوقة."""
    try:
        if not candles or len(candles) < 5 or not last_candle_is_forming(candles, now):
            return None
        ts_last = _candle_ts_seconds(candles[-1])
        if ts_last is None:
            return None
        tss = [_candle_ts_seconds(c) for c in candles[-8:]]
        tss = [t for t in tss if t is not None]
        diffs = sorted(b - a for a, b in zip(tss, tss[1:]) if b - a > 0)
        if not diffs:
            return None
        interval = diffs[len(diffs) // 2]
        now = time.time() if now is None else now
        frac = (now - ts_last) / interval
        if not (0.25 <= frac <= 1.0):
            return None
        vols = [float(c.get("volume", 0) or 0) for c in candles]
        done = vols[:-1][-window:]
        avg = sum(done) / len(done) if done else 0.0
        if avg <= 0 or vols[-1] <= 0:
            return None
        return vols[-1] / frac / avg
    except Exception:
        return None


def fmt_vol_ratio(x) -> str:
    """vol_display_fix: نسبة الحجم للعرض. أقل من 1 → منزلتان (0.06x)، وإلا منزلة.
    كان .1f يُظهر 0.06 و0.13 كلتيهما "0.1x" بينما سطر القرار يعرض القيمة الدقيقة."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "?x"
    # vol_threshold_display_fix: 1.46 كان يُعرض "1.5x" مع أن حد Spike (1.5) لم يتحقق؛
    # قرب الحدّين 1.5/2.0 من تحتهما نعرض منزلتين كي لا يتناقض العرض مع القرار.
    if (1.45 <= v < 1.5) or (1.95 <= v < 2.0):
        return f"{v:.2f}x"
    return f"{v:.2f}x" if v < 1.0 else f"{v:.1f}x"


def candles_diag(candles: list) -> str:
    """سطر تشخيصي: مصدر الشموع وعددها وتاريخ أول/آخر شمعة وهل الأخيرة جارية."""
    if not candles:
        return "شموع: لا يوجد"
    def _d(c):
        t = _candle_ts_seconds(c) if isinstance(c, dict) else None
        return time.strftime("%Y-%m-%d", time.gmtime(t)) if t else "?"
    def _dt(c):
        t = _candle_ts_seconds(c) if isinstance(c, dict) else None
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(t)) if t else "?"
    src = candles[-1].get("source", "?") if isinstance(candles[-1], dict) else "?"
    # الشرطة السفلية تكسر Markdown v1 في تيليجرام (okx_perp) → نستبدلها
    src = str(src).replace("_", "-")
    forming = "جارية" if last_candle_is_forming(candles) else "مكتملة"
    _est = ""
    if isinstance(candles[-1], dict) and candles[-1].get("synthetic_hl"):
        # batch34: شموع مُقدَّرة من أسعار الإغلاق — High/Low والحجم تقريبيان
        _est = " — ⚠️ مُقدَّرة من الإغلاقات: High/Low والحجم تقريبيان (ATR وFibonacci ونسبة الحجم أقل موثوقية)"
    if isinstance(candles[-1], dict) and candles[-1].get("snapshot_age_min") is not None:
        # batch37: بيانات OKX من لقطة محفوظة لتعذّر الجلب الحي
        _est += f" — ⚠️ لقطة OKX قديمة منذ {int(candles[-1]['snapshot_age_min'])} د (الجلب الحي تعذّر)"
    return f"شموع: {src} ×{len(candles)} | {_d(candles[0])} → {_dt(candles[-1])} ({forming}){_est}"


def ema_series(values: list, period: int) -> list:
    """true_ema_unify: EMA حقيقي (تنعيم أسي، البذرة = SMA لأول period قيمة).
    كان المشروع يحسب SMA ويسمّيه EMA في analysis/plan/data_layer بينما
    regime_detector يستخدم EMA حقيقياً → أرقام EMA50 و"السعر vs EMA50" متضاربة.
    يُرجع قائمة بطول len(values)-period+1 (فارغة إن لم تكفِ البيانات)."""
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError):
        return []
    if period <= 0 or len(vals) < period:
        return []
    k = 2.0 / (period + 1)
    e = sum(vals[:period]) / period
    out = [e]
    for v in vals[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def ema_last(values: list, period: int, default=None):
    """آخر قيمة EMA حقيقي، أو default إن لم تكفِ البيانات."""
    s = ema_series(values, period)
    return s[-1] if s else default


def fmt_atr_pct(v) -> str:
    """batch37: عرض ATR% بمنزلة عشرية واحدة بتقريب موحّد (+1e-9) — كان 3.95 يظهر 4.0 في موضع و3.9 في آخر
    لأن أحدهما يعرض قيمة مقرَّبة مسبقاً (round(,2)) والآخر الخام. يستخدمه كل عارض لـATR%."""
    try:
        return f"{round(float(v) + 1e-9, 1):.1f}"
    except Exception:
        return "0.0"

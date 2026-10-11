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

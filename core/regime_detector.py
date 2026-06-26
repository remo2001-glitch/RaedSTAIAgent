"""
📊 رائد — Market Regime Detector (ضمن طبقة 3)
يشخّص الحالة الحالية للسوق ويوجّه Strategy Router.
"""

import logging
import math
import time
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── إصلاح #85: cache نتيجة detect() لكل عملة لفترة قصيرة ──────────
# يضمن أن /signal و/analyze لنفس العملة في نفس الدقيقتين يحصلان على
# نفس Regime/Market Phase تماماً، بدل تذبذب بسبب فروق طفيفة في
# الشمعة الأخيرة (لم تُغلق بعد) بين استدعاءين منفصلين خلال ثوانٍ.
_REGIME_CACHE: Dict[str, Dict] = {}
_REGIME_CACHE_TTL = 90  # ثانية


class Regime(Enum):
    BULL_TREND       = "bull_trend"
    BEAR_TREND       = "bear_trend"
    SIDEWAYS         = "sideways"
    HIGH_VOLATILITY  = "high_volatility"
    ACCUMULATION     = "accumulation"
    DISTRIBUTION     = "distribution"
    UNKNOWN          = "unknown"


REGIME_AR = {
    Regime.BULL_TREND:      "🟢 اتجاه صاعد قوي",
    Regime.BEAR_TREND:      "🔴 اتجاه هابط",
    Regime.SIDEWAYS:        "🟡 تذبذب جانبي",
    Regime.HIGH_VOLATILITY: "⚡ تقلب عالٍ",
    Regime.ACCUMULATION:    "🔵 تراكم مؤسسي",
    Regime.DISTRIBUTION:    "🟠 توزيع وتصريف",
    Regime.UNKNOWN:         "⚪ غير محدد",
}

REGIME_STRATEGY = {
    Regime.BULL_TREND:      ["trend_following", "breakout"],
    Regime.BEAR_TREND:      ["mean_reversion", "reduce_exposure"],
    Regime.SIDEWAYS:        ["mean_reversion", "arbitrage"],
    Regime.HIGH_VOLATILITY: ["volatility_expansion", "reduce_size"],
    Regime.ACCUMULATION:    ["on_chain_accumulation", "trend_following"],
    Regime.DISTRIBUTION:    ["reduce_exposure", "mean_reversion"],
    Regime.UNKNOWN:         ["reduce_size"],
}
# ملاحظة: أُزيلت "short_only" من BEAR_TREND لأنها تتعارض مع RSI < 30
# الاستراتيجية الصحيحة: "reduce_exposure" + انتظار ارتداد


@dataclass
class RegimeResult:
    regime:         Regime
    confidence:     float           # 0–1
    description_ar: str
    strategies:     List[str]
    metrics:        Dict
    action:         str             # "trade_normal" | "reduce_size" | "avoid"
    market_phase:   str = "unknown" # "Accumulation" | "Distribution" | "Markup" | "Markdown"


class RegimeDetector:
    """
    يستخدم: ATR, ADX, EMA, BTC Dominance, Fear & Greed
    لتحديد حالة السوق الفعلية.
    """

    def detect(self, candles: List[Dict],
               btc_dominance: float = 50.0,
               fear_greed: int = 50,
               symbol: str = "") -> RegimeResult:
        """
        candles: قائمة {'close', 'high', 'low', 'volume'} بترتيب تصاعدي

        إصلاح #85: إذا تم تمرير symbol، يُستخدم cache قصير (90 ثانية)
        لضمان نتيجة Regime/Market Phase متطابقة بين /signal و/analyze
        لنفس العملة ضمن نفس النافذة الزمنية القصيرة.
        """
        if symbol:
            _key = f"{symbol.upper()}"
            _entry = _REGIME_CACHE.get(_key)
            if _entry and time.time() - _entry["ts"] < _REGIME_CACHE_TTL:
                return _entry["result"]

        if len(candles) < 30:
            return RegimeResult(
                Regime.UNKNOWN, 0.3,
                REGIME_AR[Regime.UNKNOWN],
                REGIME_STRATEGY[Regime.UNKNOWN],
                {}, "reduce_size"
            )

        closes  = [c["close"]  for c in candles]
        highs   = [c["high"]   for c in candles]
        lows    = [c["low"]    for c in candles]
        volumes = [c["volume"] for c in candles]

        # ── المؤشرات ──────────────────────────────────────────
        atr    = self._atr(highs, lows, closes, 14)
        adx    = self._adx(highs, lows, closes, 14)
        ema20  = self._ema(closes, 20)
        ema50  = self._ema(closes, 50)
        ema200 = self._ema(closes, 200) if len(closes) >= 200 else ema50
        # إصلاح M3: cap EMA لمنع بيانات تاريخية مشوهة (SPCX perp)
        # price = آخر سعر إغلاق (closes[-1])
        _current_price = closes[-1] if closes else 0
        if _current_price > 0:
            ema50  = min(ema50,  _current_price * 3.0)
            ema20  = min(ema20,  _current_price * 3.0)
            ema200 = min(ema200, _current_price * 3.0)
        rsi    = self._rsi(closes, 14)
        vol_ma = self._sma(volumes, 20)
        atr_pct = (atr / closes[-1] * 100) if closes[-1] > 0 else 0

        price = closes[-1]

        metrics = {
            "atr_pct":       round(atr_pct, 2),
            "adx":           round(adx, 1),
            "rsi":           round(rsi, 1),
            "ema20":         round(ema20, 4),
            "ema50":         round(ema50, 4),
            "price_vs_ema50": round((price - ema50) / ema50 * 100, 2),
            "btc_dominance": btc_dominance,
            "fear_greed":    fear_greed,
            # إصلاح L4 (نفس J1): متوسط 3 شموع لمنع 0.0x
            "vol_ratio":     round(
                (sum(v for v in volumes[-3:] if v > 0) / max(len([v for v in volumes[-3:] if v > 0]), 1))
                / vol_ma, 2) if vol_ma > 0 and volumes else 1.0,
        }

        # ── منطق التشخيص ──────────────────────────────────────
        regime, confidence = self._classify(
            adx, atr_pct, rsi, price, ema20, ema50, ema200,
            volumes[-1], vol_ma, btc_dominance, fear_greed
        )

        action = "trade_normal"
        rsi_val = metrics.get("rsi", 50)
        _action_basis = ""  # إصلاح #149: سبب القرار للشفافية/التشخيص
        if regime == Regime.HIGH_VOLATILITY:
            action = "reduce_size"
        elif regime == Regime.BEAR_TREND and adx >= 30:  # إصلاح L6
            if fear_greed < 20:
                action = "avoid"
                _action_basis = f" (ADX={adx:.0f}≥30، Fear={fear_greed}<20)"
            else:
                action = "reduce_size"
                _action_basis = f" (ADX={adx:.0f}≥30)"
        # إذا RSI في ذروة بيع (< 30) → تحذير انعكاس بغض النظر عن الاتجاه
        if rsi_val < 30:
            action = "wait_reversal"
            _action_basis = ""
        metrics["action_basis"] = _action_basis

        # تحديد Market Phase (Wyckoff-inspired)
        closes_20  = [c["close"] for c in candles[-20:]]
        price_chg  = (closes_20[-1] - closes_20[0]) / max(closes_20[0], 1) * 100
        if regime == Regime.BULL_TREND:
            market_phase = "Markup"
        elif regime == Regime.BEAR_TREND:
            market_phase = "Markdown"
        elif regime == Regime.ACCUMULATION:
            market_phase = "Accumulation"
        elif regime == Regime.DISTRIBUTION:
            market_phase = "Distribution"
        elif regime == Regime.SIDEWAYS:
            # الفرق بين تراكم وتوزيع: هل كان قبلها هبوط أم صعود؟
            long_chg = (closes[-1] - closes[-min(60, len(closes))]) / max(closes[-min(60, len(closes))], 1) * 100
            market_phase = "Accumulation" if long_chg < -5 else "Distribution" if long_chg > 5 else "Consolidation"
        elif regime == Regime.HIGH_VOLATILITY:
            # إصلاح #85: نطاق محايد ±1% يمنع تذبذب Markup/Markdown
            # بين /signal و/analyze لنفس اللحظة بسبب فروق طفيفة في
            # الشمعة الأخيرة (لم تُغلق بعد) بين استدعاءين منفصلين
            if price_chg > 1:
                market_phase = "Markup"
            elif price_chg < -1:
                market_phase = "Markdown"
            else:
                market_phase = "Consolidation"
        else:
            market_phase = "Unknown"

        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            description_ar=REGIME_AR[regime],
            strategies=REGIME_STRATEGY[regime],
            metrics=metrics,
            action=action,
            market_phase=market_phase,
        )
        if symbol:
            _REGIME_CACHE[symbol.upper()] = {"result": result, "ts": time.time()}
        return result

    def _classify(self, adx, atr_pct, rsi, price,
                  ema20, ema50, ema200, vol, vol_ma,
                  btc_dom, fg) -> Tuple[Regime, float]:

        score_map = {r: 0.0 for r in Regime}

        # Bull — يشترط ema50 > ema200 (اتجاه صاعد حقيقي)
        if price > ema50 > ema200:
            score_map[Regime.BULL_TREND] += 2.5   # اتجاه صاعد حقيقي
        if price > ema20 and ema50 > ema200:
            score_map[Regime.BULL_TREND] += 1.0
        if adx > 25 and rsi > 50 and ema50 > ema200:
            score_map[Regime.BULL_TREND] += 1.5
        if fg > 60 and ema50 > ema200:
            score_map[Regime.BULL_TREND] += 0.5

        # Bear
        if price < ema50 < ema200:
            score_map[Regime.BEAR_TREND] += 2.5   # وزن أعلى للاتجاه الكبير
        if price < ema20:
            score_map[Regime.BEAR_TREND] += 1.0
        if adx > 25 and rsi < 45:
            score_map[Regime.BEAR_TREND] += 1.5
        if fg < 30:
            score_map[Regime.BEAR_TREND] += 0.5
        # ارتداد ضمن هبوط: السعر ارتفع مؤخراً لكن ema50 < ema200
        if ema50 < ema200 and price > ema20:
            score_map[Regime.BEAR_TREND] += 1.0   # bear rally

        # Sideways
        if adx < 20:
            score_map[Regime.SIDEWAYS] += 2.0
        if abs(price - ema50) / ema50 < 0.03:
            score_map[Regime.SIDEWAYS] += 1.5
        if 40 <= rsi <= 60:
            score_map[Regime.SIDEWAYS] += 1.0

        # High Volatility
        if atr_pct > 5:
            score_map[Regime.HIGH_VOLATILITY] += 2.0
        if atr_pct > 8:
            score_map[Regime.HIGH_VOLATILITY] += 2.0
        if vol > vol_ma * 2:
            score_map[Regime.HIGH_VOLATILITY] += 1.0

        # Accumulation (صعود هادئ + حجم منخفض + خوف)
        if price > ema50 and adx < 25 and vol < vol_ma * 0.8 and fg < 40:
            score_map[Regime.ACCUMULATION] += 2.5
        if btc_dom > 55 and price > ema50:
            score_map[Regime.ACCUMULATION] += 1.0

        # Distribution (هبوط على حجم كبير)
        if price < ema50 and vol > vol_ma * 1.5 and rsi > 60:
            score_map[Regime.DISTRIBUTION] += 2.5
        if fg > 70 and price < ema20:
            score_map[Regime.DISTRIBUTION] += 1.0

        best = max(score_map, key=score_map.get)
        total = sum(score_map.values()) or 1
        # إصلاح #463: سقف 0.82 أكثر واقعية من 0.95
        # الأسواق دائماً تحتوي عدم يقين — 95% مضلل
        raw_conf = score_map[best] / total * 2
        confidence = min(raw_conf, 0.82)

        if confidence < 0.35:
            return Regime.UNKNOWN, 0.3
        return best, round(confidence, 2)

    # ─── مؤشرات تقنية ──────────────────────────────────────────
    @staticmethod
    def _ema(data: List[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        k   = 2 / (period + 1)
        val = sum(data[:period]) / period
        for v in data[period:]:
            val = v * k + val * (1 - k)
        return val

    @staticmethod
    def _sma(data: List[float], period: int) -> float:
        if not data:
            return 0
        window = data[-period:]
        return sum(window) / len(window)

    @staticmethod
    def _atr(highs, lows, closes, period: int) -> float:
        trs = []
        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i]  - closes[i-1])
            trs.append(max(hl, hc, lc))
        if not trs:
            return 0
        return sum(trs[-period:]) / min(period, len(trs))

    @staticmethod
    def _adx(highs, lows, closes, period: int = 14) -> float:
        """
        ADX إصلاح #142 — حل جذري لمشكلة بيانات H/L المحسوبة.
        
        جذر المشكلة: بيانات CoinGecko تُحسب H/L كـ price±vol_d/2
        فتُعطي +DM≈0 في سوق هابط → ADX=87-99 artifact.
        
        الحل: Wilder's smoothing + كشف أحادية الاتجاه + تطبيع.
        """
        n = len(closes)
        if n < period + 1:
            return 0.0

        plus_dm_list, minus_dm_list, tr_list = [], [], []
        for i in range(1, n):
            h_diff = highs[i]  - highs[i-1]
            l_diff = lows[i-1] - lows[i]
            plus_dm_list.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
            minus_dm_list.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
            hl = highs[i] - lows[i]
            hc = abs(highs[i]  - closes[i-1])
            lc = abs(lows[i]   - closes[i-1])
            tr_list.append(max(hl, hc, lc))

        # كشف artifact البيانات: إذا +DM أو -DM = 0 دائماً
        # (يحدث مع H/L المحسوبة من CoinGecko)
        pdm_nonzero = sum(1 for x in plus_dm_list  if x > 0)
        mdm_nonzero = sum(1 for x in minus_dm_list if x > 0)
        total       = len(plus_dm_list) or 1

        if pdm_nonzero / total < 0.05 or mdm_nonzero / total < 0.05:
            # بيانات أحادية — نستخدم trend strength من EMA بدلاً من ADX
            # ATR-based approach: مدى التذبذب كنسبة من السعر
            p  = closes[-1] if closes[-1] > 0 else 1
            avg_tr  = sum(tr_list[-period:]) / min(period, len(tr_list)) if tr_list else 0
            atr_pct = avg_tr / p * 100

            # الاتجاه من EMAs
            ema_f = sum(closes[-period:])   / period if len(closes) >= period else closes[-1]
            ema_s = sum(closes[-period*2:]) / (period*2) if len(closes) >= period*2 else ema_f
            trend_strength = abs(ema_f - ema_s) / max(ema_s, 1) * 100

            # ADX تقديري: مزيج من ATR وقوة الاتجاه
            adx_est = min(15 + trend_strength * 8 + atr_pct * 1.5, 65.0)
            return round(adx_est, 1)

        # Wilder's Smoothing الصحيح
        def wilder_smooth(data):
            if len(data) < period:
                return []
            val = sum(data[:period])
            result = [val]
            for v in data[period:]:
                val = val - val / period + v
                result.append(val)
            return result

        atr_s = wilder_smooth(tr_list)
        pdm_s = wilder_smooth(plus_dm_list)
        mdm_s = wilder_smooth(minus_dm_list)

        if not atr_s:
            return 0.0

        dx_list = []
        for i in range(len(atr_s)):
            atr_v = atr_s[i]
            if atr_v <= 0:
                continue
            di_p  = pdm_s[i] / atr_v * 100
            di_m  = mdm_s[i] / atr_v * 100
            denom = di_p + di_m
            if denom > 0:
                dx_list.append(abs(di_p - di_m) / denom * 100)

        if not dx_list:
            return 0.0

        adx_val = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx_val = (adx_val * (period - 1) + dx) / period

        # سقف واقعي: ADX > 75 نادر جداً في الأسواق الحقيقية
        return round(min(adx_val, 75.0), 1)

    @staticmethod
    def _rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = sum(gains[-period:]) / period or 1e-9
        avg_loss = sum(losses[-period:]) / period or 1e-9
        rs  = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    def format_ar(self, result: RegimeResult) -> str:
        m = result.metrics
        vol_ratio = m.get('vol_ratio', 1)
        strategies_txt = " · ".join(s.replace("_", " ") for s in result.strategies)
        return (
            f"📊 *حالة السوق الحالية*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{result.description_ar}\n"
            f"الثقة: {result.confidence:.0%} — (مدى يقين النموذج من تشخيص الاتجاه الحالي)\n"
            + (f"📊 Market Phase: {_mp_ar(getattr(result, 'market_phase', ''))}\n"
               if getattr(result, 'market_phase', '') else "")
            + "\n"
            f"📈 *المؤشرات*\n"
            f"• ATR: {m.get('atr_pct',0):.1f}% | ADX: {m.get('adx',0):.0f}\n"
            f"• RSI: {m.get('rsi',0):.0f} | حجم: {vol_ratio:.1f}x\n"
            # إصلاح O3: تنبيه عند EMA50 بعيد جداً
            f"• السعر vs EMA50: {m.get('price_vs_ema50',0):+.1f}%{' ⚠️ (EMA تاريخي)' if abs(m.get('price_vs_ema50',0)) > 50 else ''}\n"
            f"• Fear & Greed: {m.get('fear_greed',50)} | هيمنة BTC: {m.get('btc_dominance',50):.0f}%\n\n"
            f"🎯 *الاستراتيجية الموصى بها*\n"
            f"• {strategies_txt}\n"
            f"• الإجراء: {_action_ar(result.action)}{m.get('action_basis','')}"
            + _rsi_warning(m.get("rsi", 50), result.regime)
            + _adx_warning(m.get("adx", 0))
        )


def _mp_ar(phase: str) -> str:
    """ترجمة Market Phase للعربية."""
    return {
        "Markup":        "🟢 صعود (Markup)",
        "Markdown":      "🔴 هبوط (Markdown)",
        "Accumulation":  "🔵 تراكم (Accumulation)",
        "Distribution":  "🟠 توزيع (Distribution)",
        "Consolidation": "🟡 تعزيز (Consolidation)",
    }.get(phase, phase)


def _action_ar(action: str) -> str:
    return {
        "trade_normal":   "✅ تداول بحجم طبيعي",
        "reduce_size":    "⚠️ تقليل الحجم 50%",
        "avoid":          "🚫 تجنب الدخول الآن",
        "wait_reversal":  "⏳ انتظر — RSI في ذروة بيع (احتمال ارتداد)",
        "reduce_exposure": "📉 قلل التعرض للسوق",
        # إصلاح #103: للارتداد المؤكَّد (counter_trend_bounce + 🟢شراء)
        "bounce_entry_confirmed": "⚡ ارتداد مؤكَّد — Scalp بحجم محدود ووقف صارم",
    }.get(action, action)


def _adx_warning(adx: float) -> str:
    """تحذير عند ADX مرتفع جداً — اتجاه استثنائي القوة."""
    if adx >= 60:
        return (
            f"\n\n🚨 *تحذير ADX شديد:* ADX = {adx:.0f}"
            "\nاتجاه استثنائي القوة — تجنب الدخول حتى يضعف ADX تحت 50"
        )
    elif adx >= 50:
        return (
            f"\n\n⚠️ *ADX مرتفع:* {adx:.0f} — اتجاه قوي جداً"
            "\nقلل الحجم وانتظر تراجع ADX"
        )
    return ""


def _rsi_warning(rsi: float, regime: Regime) -> str:
    """تحذير إضافي عند تعارض RSI مع الاتجاه."""
    if rsi < 25:
        return (
            "\n\n⚠️ *تنبيه هام:* RSI عند ذروة بيع شديدة ({:.0f})\n"
            "احتمال انعكاس قريب — تجنب الصفقات القصيرة الآن".format(rsi)
        )
    elif rsi > 75:
        return (
            "\n\n⚠️ *تنبيه هام:* RSI عند ذروة شراء ({:.0f})\n"
            "احتمال تصحيح — تجنب الدخول بمراكز كبيرة".format(rsi)
        )
    elif rsi < 30 and regime == Regime.BEAR_TREND:
        return (
            "\n\n💡 RSI ({:.0f}) يُشير لانعكاس محتمل رغم الاتجاه الهابط".format(rsi)
        )
    return ""


# Singleton
regime_detector = RegimeDetector()

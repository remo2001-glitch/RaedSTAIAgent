"""
📊 رائد — Market Regime Detector (ضمن طبقة ٣)
يشخّص الحالة الحالية للسوق ويوجّه Strategy Router.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    Regime.BEAR_TREND:      ["mean_reversion", "short_only"],
    Regime.SIDEWAYS:        ["mean_reversion", "arbitrage"],
    Regime.HIGH_VOLATILITY: ["volatility_expansion", "reduce_size"],
    Regime.ACCUMULATION:    ["on_chain_accumulation", "trend_following"],
    Regime.DISTRIBUTION:    ["reduce_exposure", "mean_reversion"],
    Regime.UNKNOWN:         ["reduce_size"],
}


@dataclass
class RegimeResult:
    regime:         Regime
    confidence:     float           # 0–1
    description_ar: str
    strategies:     List[str]
    metrics:        Dict
    action:         str             # "trade_normal" | "reduce_size" | "avoid"


class RegimeDetector:
    """
    يستخدم: ATR, ADX, EMA, BTC Dominance, Fear & Greed
    لتحديد حالة السوق الفعلية.
    """

    def detect(self, candles: List[Dict],
               btc_dominance: float = 50.0,
               fear_greed: int = 50) -> RegimeResult:
        """
        candles: قائمة {'close', 'high', 'low', 'volume'} بترتيب تصاعدي
        """
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
            "vol_ratio":     round(volumes[-1] / vol_ma, 2) if vol_ma > 0 else 1.0,
        }

        # ── منطق التشخيص ──────────────────────────────────────
        regime, confidence = self._classify(
            adx, atr_pct, rsi, price, ema20, ema50, ema200,
            volumes[-1], vol_ma, btc_dominance, fear_greed
        )

        action = "trade_normal"
        if regime == Regime.HIGH_VOLATILITY:
            action = "reduce_size"
        elif regime == Regime.BEAR_TREND and adx > 30:
            action = "avoid" if fear_greed < 20 else "reduce_size"

        return RegimeResult(
            regime=regime,
            confidence=confidence,
            description_ar=REGIME_AR[regime],
            strategies=REGIME_STRATEGY[regime],
            metrics=metrics,
            action=action,
        )

    def _classify(self, adx, atr_pct, rsi, price,
                  ema20, ema50, ema200, vol, vol_ma,
                  btc_dom, fg) -> Tuple[Regime, float]:

        score_map = {r: 0.0 for r in Regime}

        # Bull
        if price > ema50 > ema200:
            score_map[Regime.BULL_TREND] += 2.0
        if price > ema20:
            score_map[Regime.BULL_TREND] += 1.0
        if adx > 25 and rsi > 50:
            score_map[Regime.BULL_TREND] += 1.5
        if fg > 60:
            score_map[Regime.BULL_TREND] += 0.5

        # Bear
        if price < ema50 < ema200:
            score_map[Regime.BEAR_TREND] += 2.0
        if price < ema20:
            score_map[Regime.BEAR_TREND] += 1.0
        if adx > 25 and rsi < 45:
            score_map[Regime.BEAR_TREND] += 1.5
        if fg < 30:
            score_map[Regime.BEAR_TREND] += 0.5

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
        confidence = min(score_map[best] / total * 2, 0.95)

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
        if len(closes) < period + 1:
            return 0
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(closes)):
            h_diff = highs[i]  - highs[i-1]
            l_diff = lows[i-1] - lows[i]
            plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
            minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
            hl  = highs[i]  - lows[i]
            hc  = abs(highs[i]  - closes[i-1])
            lc  = abs(lows[i]   - closes[i-1])
            tr_list.append(max(hl, hc, lc))

        def smooth(lst): return sum(lst[-period:]) / period if lst else 0
        str_ = smooth(tr_list) or 1
        di_plus  = smooth(plus_dm)  / str_ * 100
        di_minus = smooth(minus_dm) / str_ * 100
        dx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9) * 100
        return dx

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
        return (
            f"📊 *حالة السوق الحالية*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{result.description_ar}\n"
            f"الثقة: {result.confidence:.0%}\n\n"
            f"📈 *المؤشرات*\n"
            f"• ATR: {m.get('atr_pct',0):.1f}٪ | ADX: {m.get('adx',0):.0f}\n"
            f"• RSI: {m.get('rsi',0):.0f} | حجم نسبي: {m.get('vol_ratio',1):.1f}×\n"
            f"• السعر vs EMA50: {m.get('price_vs_ema50',0):+.1f}٪\n"
            f"• Fear & Greed: {m.get('fear_greed',50)} | هيمنة BTC: {m.get('btc_dominance',50):.0f}٪\n\n"
            f"• الإجراء: {_action_ar(result.action)}"
        )


def _action_ar(action: str) -> str:
    return {
        "trade_normal": "✅ تداول بحجم طبيعي",
        "reduce_size":  "⚠️ تقليل الحجم ٥٠٪",
        "avoid":        "🚫 تجنب الدخول الآن",
    }.get(action, action)


# Singleton
regime_detector = RegimeDetector()

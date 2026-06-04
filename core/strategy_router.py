"""
📐 رائد — Strategy Router (الطبقة 4)
📡 Signal Layer (الطبقة 3)
💼 Portfolio Allocation Engine (الطبقة 10)
"""

import math
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from core.regime_detector import Regime, RegimeResult

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# SIGNAL LAYER (الطبقة 3)
# ══════════════════════════════════════════════════════════════

@dataclass
class SignalResult:
    symbol:          str
    direction:       str        # "long" | "short" | "neutral"
    confidence:      float      # 0–1
    signal_sources:  Dict[str, float]  # source → contribution
    technicals:      Dict
    onchain_score:   float
    news_score:      float
    backtest_score:  float
    macro_score:     float
    regime:          str = "unknown"
    trade_type:      str = "spot"  # "spot" | "futures_long" | "futures_short"


class SignalLayer:
    """
    يجمع إشارات من 5 مصادر مستقلة:
    تقني + On-Chain + أخبار + Backtest + ماكرو
    ويُنتج ثقة مُرجَّحة.
    """

    WEIGHTS = {
        "technical":  0.30,
        "onchain":    0.25,
        "news":       0.20,
        "macro":      0.15,
        "backtest":   0.10,
    }

    def generate(self, symbol: str, candles: List[Dict],
                 onchain_data: Dict, news_sentiment: float,
                 backtest_win_rate: float, macro_data: Dict,
                 regime: RegimeResult) -> SignalResult:
        """يُنتج إشارة شاملة — محمية من البيانات الناقصة."""
        # حماية: candles فارغة → إشارة محايدة
        candles = candles or []
        if not onchain_data or not isinstance(onchain_data, dict):
            onchain_data = {}

        # ── 1. تحليل تقني ──
        try:
            tech = self._technical_signal(candles)
        except Exception as e:
            logger.warning(f"technical_signal ({symbol}): {e}")
            tech = {"score": 0.5, "bias": "neutral", "rsi": 50,
                    "ema_align": False, "macd_hist": 0, "bb_pos": 0.5,
                    "vol_ratio": 1.0}

        # ── 2. On-Chain ──
        try:
            oc_score = self._onchain_signal(onchain_data)
        except Exception:
            oc_score = 0.5

        # ── 3. الأخبار (sentiment -1 to +1 → 0 to 1) ──
        news_score = (news_sentiment + 1) / 2

        # ── 4. Backtest ──
        bt_score = min(backtest_win_rate, 1.0)

        # ── 5. ماكرو ──
        macro_score = self._macro_signal(macro_data, regime)

        # ── ترجيح ──
        raw_conf = (
            tech["score"]  * self.WEIGHTS["technical"] +
            oc_score       * self.WEIGHTS["onchain"]   +
            news_score     * self.WEIGHTS["news"]       +
            macro_score    * self.WEIGHTS["macro"]      +
            bt_score       * self.WEIGHTS["backtest"]
        )

        # إصلاح #311: تعديل regime_adj — RSI extreme يُعيد جزءاً من الثقة
        regime_adj = _regime_confidence_adj(regime.regime)
        rsi_now    = tech.get("rsi", 50)
        fg_now     = macro_data.get("fear_greed", 50)
        # عند ذروة بيع شديدة (RSI<20, Fear<15): نُعدّل adj لأعلى
        if rsi_now < 20 and fg_now < 20:
            regime_adj = min(regime_adj + 0.25, 1.05)  # انعكاس محتمل قوي
        elif rsi_now < 30 and fg_now < 25:
            regime_adj = min(regime_adj + 0.10, 1.0)
        confidence = min(raw_conf * regime_adj, 0.97)

        # ── تحديد الاتجاه مع دعم Futures ────────────────────────
        direction      = "neutral"
        trade_type     = "spot"   # spot | futures_long | futures_short
        rsi_val        = tech.get("rsi", 50)
        is_bear_regime = regime.regime.value in ("bear_trend", "distribution")
        is_bull_regime = regime.regime.value in ("bull_trend", "accumulation")

        if confidence > 0.65 and tech["bias"] == "bullish":
            direction  = "long"
            trade_type = "spot"
            # Futures Long: سوق صاعد + RSI مقبول + EMA مؤكد
            if is_bull_regime and rsi_val < 65 and tech.get("ema_align"):
                trade_type = "futures_long"

        elif confidence > 0.65 and tech["bias"] == "bearish":
            direction  = "short"
            trade_type = "spot"  # Spot: لا تقصير في Spot عادة
            # Futures Short: سوق هابط + RSI فوق 40 (ليس ذروة بيع)
            if is_bear_regime and rsi_val > 40:
                trade_type = "futures_short"

        # ذروة شراء RSI>70 في أي سوق → Short Futures فرصة
        elif rsi_val > 72 and confidence > 0.60 and is_bear_regime:
            direction  = "short"
            trade_type = "futures_short"

        # ذروة بيع RSI<25 + هابط → Long Futures انعكاس محتمل
        elif rsi_val < 25 and confidence > 0.70 and is_bear_regime:
            direction  = "long"
            trade_type = "futures_long"

        # إصلاح #365: RSI extreme + Fear extreme = long حتى لو bias neutral
        # منطق مالي: RSI<15 تاريخياً = نقطة انعكاس في 90%+ من الحالات
        elif rsi_val < 15 and fg_now < 20 and confidence >= 0.60:
            direction  = "long"
            trade_type = "spot"   # spot أكثر أماناً عند هذه المستويات

        elif rsi_val < 20 and fg_now < 25 and confidence >= 0.65:
            direction  = "long"
            trade_type = "spot"

        return SignalResult(
            symbol=symbol,
            direction=direction,
            trade_type=trade_type,
            confidence=round(confidence, 3),
            signal_sources={
                "technical": round(tech["score"], 3),
                "onchain":   round(oc_score, 3),
                "news":      round(news_score, 3),
                "backtest":  round(bt_score, 3),
                "macro":     round(macro_score, 3),
            },
            technicals=tech,
            onchain_score=oc_score,
            news_score=news_score,
            backtest_score=bt_score,
            macro_score=macro_score,
            regime=regime.regime.value,
        )

    def _technical_signal(self, candles: List[Dict]) -> Dict:
        if len(candles) < 10:
            return {"score": 0.5, "bias": "neutral", "rsi": 50, "ema_align": False,
                    "macd_hist": 0, "bb_pos": 0.5, "vol_ratio": 1.0}
        if len(candles) < 20:
            # بيانات جزئية — نستخدم RSI وحجم فقط
            closes = [c["close"] for c in candles]
            vols   = [c.get("volume", 0) for c in candles]
            rsi    = _rsi(closes, min(14, len(closes)-1))
            vol_ma = _sma(vols, min(10, len(vols))) or 1
            vol_r  = vols[-1] / vol_ma if vol_ma > 0 else 1.0
            score  = 0.5
            bias   = "neutral"
            if rsi < 30: score = 0.65; bias = "bullish"
            elif rsi > 70: score = 0.35; bias = "bearish"
            if vol_r > 2.0:  # حجم ضخم = إشارة اتجاه
                if bias == "bullish": score = min(score + 0.1, 0.9)
                elif bias == "bearish": score = max(score - 0.1, 0.1)
            return {"score": round(score, 3), "bias": bias, "rsi": round(rsi, 1),
                    "ema_align": False, "macd_hist": 0, "bb_pos": 0.5,
                    "vol_ratio": round(vol_r, 2)}

        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        vols   = [c["volume"] for c in candles]

        rsi   = _rsi(closes, 14)
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200) if len(closes) >= 200 else ema50
        macd_hist = _macd_histogram(closes)
        bb_pos    = _bb_position(closes, 20)
        vol_ratio = vols[-1] / (_sma(vols, 20) or 1)

        price = closes[-1]
        score = 0.5
        bias  = "neutral"
        bullish_pts = 0
        bearish_pts = 0

        # RSI — إصلاح #311: extreme RSI يستحق وزناً أكبر
        if rsi < 15:    bullish_pts += 5   # ذروة بيع شديدة جداً — انعكاس مؤكد تاريخياً
        elif rsi < 20:  bullish_pts += 4   # ذروة بيع شديدة
        elif rsi < 30:  bullish_pts += 2
        elif rsi < 45:  bullish_pts += 1
        elif rsi > 80:  bearish_pts += 5   # ذروة شراء شديدة
        elif rsi > 70:  bearish_pts += 2
        elif rsi > 55:  bearish_pts += 1

        # EMA alignment — إصلاح #365: عند RSI extreme، EMA تُخفَّف
        # منطق مالي: RSI<15 = ذروة بيع تاريخية تتجاوز إشارة EMA
        ema_align = price > ema20 > ema50
        if ema_align:
            bullish_pts += 2
        elif price < ema20 < ema50:
            # في ذروة بيع شديدة: EMA bearish أقل أهمية
            if rsi < 15:
                bearish_pts += 0   # لا وزن لـ EMA عند RSI<15 (الارتداد أقوى)
            elif rsi < 25:
                bearish_pts += 1   # نصف الوزن العادي
            else:
                bearish_pts += 2

        # MACD
        if macd_hist > 0: bullish_pts += 1
        else:             bearish_pts += 1

        # Bollinger Band
        if bb_pos < 0.2:  bullish_pts += 1   # قرب الحد السفلي
        elif bb_pos > 0.8: bearish_pts += 1

        # Volume confirmation — حجم ضخم = إشارة قوية
        if vol_ratio > 3.0:
            # حجم استثنائي — يُعزز الاتجاه الحالي بقوة
            if ema_align:           bullish_pts += 2
            elif price < ema20:     bearish_pts += 2
            else:                   bullish_pts += 1   # حجم كبير بلا اتجاه = محايد إيجابي
        elif vol_ratio > 1.5:
            if ema_align:           bullish_pts += 1
            elif not ema_align:     bearish_pts += 1

        total = bullish_pts + bearish_pts
        if total > 0:
            bull_ratio = bullish_pts / total
            if bull_ratio > 0.6:
                score = 0.5 + (bull_ratio - 0.5) * 1.2
                bias  = "bullish"
            elif bull_ratio < 0.4:
                score = 0.5 - (0.5 - bull_ratio) * 1.2
                bias  = "bearish"

        return {
            "score":     round(min(max(score, 0), 1), 3),
            "bias":      bias,
            "rsi":       round(rsi, 1),
            "ema_align": ema_align,
            "macd_hist": round(macd_hist, 6),
            "bb_pos":    round(bb_pos, 3),
            "vol_ratio": round(vol_ratio, 2),
        }

    def _onchain_signal(self, data: Dict) -> float:
        if not data:
            return 0.5
        tvl    = data.get("tvl", 0)
        score  = 0.5
        if tvl > 50_000_000_000:   score += 0.2
        elif tvl > 10_000_000_000: score += 0.1
        elif tvl < 1_000_000_000:  score -= 0.15
        return round(min(max(score, 0), 1), 3)

    def _macro_signal(self, macro: Dict, regime: RegimeResult) -> float:
        """
        إصلاح #311: منطق contrarian صحيح.
        خوف تاريخي (< 15) = فرصة شراء قوية جداً (Warren Buffett: buy when fearful).
        """
        fear_greed = macro.get("fear_greed", 50)
        btc_dom    = macro.get("btc_dominance", 50)
        score = 0.5

        # Fear & Greed — contrarian signal
        if fear_greed < 10:   score += 0.30   # ذعر تاريخي = قاع محتمل قوي جداً
        elif fear_greed < 15: score += 0.20   # خوف شديد جداً = فرصة انعكاس
        elif fear_greed < 25: score += 0.12   # خوف شديد = فرصة شراء
        elif fear_greed < 35: score += 0.05
        elif fear_greed > 90: score -= 0.20   # طمع تاريخي = قمة محتملة
        elif fear_greed > 75: score -= 0.10
        elif fear_greed > 60: score += 0.03

        # BTC Dominance
        if btc_dom > 60:   score -= 0.05
        elif btc_dom < 45: score += 0.05

        return round(min(max(score, 0), 1), 3)

    def format_ar(self, s: SignalResult) -> str:
        bar     = _confidence_bar(s.confidence)
        rsi_val = s.technicals.get("rsi", 50)
        vol_r   = s.technicals.get("vol_ratio", 1.0)

        # تحذيرات إضافية
        warnings = []
        if rsi_val > 70 and s.direction == "long":
            warnings.append("⚠️ RSI في ذروة الشراء — تحقق من التوقيت")
        if rsi_val < 30 and s.direction == "short":
            warnings.append("⚠️ RSI في ذروة البيع — خطر انعكاس")
        if vol_r > 5.0:
            warnings.append(f"🔥 حجم استثنائي {vol_r:.1f}x — إشارة قوية")
        elif vol_r > 3.0:
            warnings.append(f"📈 حجم مرتفع {vol_r:.1f}x — تأكيد الحركة")

        # تسمية نوع الصفقة
        trade_label = ""
        tt = getattr(s, "trade_type", "spot")
        if tt == "futures_long":
            trade_label = " 📈 Futures Long"
        elif tt == "futures_short":
            trade_label = " 📉 Futures Short"

        text = (
            f"📡 *إشارة رائد — {s.symbol}*{trade_label}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"الاتجاه: {'🟢 شراء' if s.direction=='long' else '🔴 بيع' if s.direction=='short' else '⚪ محايد'}"
            + (f" ({tt.replace('_', ' ').title()})" if tt != 'spot' else "") + "\n"
            f"الثقة:  {bar} {s.confidence:.0%}\n\n"
            f"📊 *مصادر الإشارة*\n"
            f"• تقني:    {s.signal_sources['technical']:.0%}\n"
            f"• On-Chain: {s.signal_sources['onchain']:.0%}\n"
            f"• أخبار:   {s.signal_sources['news']:.0%}\n"
            f"• Backtest: {s.signal_sources['backtest']:.0%}\n"
            f"• ماكرو:   {s.signal_sources['macro']:.0%}\n\n"
            f"RSI: {rsi_val:.0f} | "
            f"EMA: {'✅' if s.technicals.get('ema_align') else '❌'} | "
            f"حجم: {vol_r:.1f}x"
        )
        if warnings:
            text += "\n\n" + "\n".join(warnings)
        return text


# ══════════════════════════════════════════════════════════════
# STRATEGY ROUTER (الطبقة 4)
# ══════════════════════════════════════════════════════════════

class Strategy(Enum):
    TREND_FOLLOWING      = "trend_following"
    MEAN_REVERSION       = "mean_reversion"
    BREAKOUT             = "breakout"
    VOLATILITY_EXPANSION = "volatility_expansion"
    ARBITRAGE            = "arbitrage"
    ONCHAIN_ACCUMULATION = "on_chain_accumulation"
    REDUCE_EXPOSURE      = "reduce_exposure"
    AVOID                = "avoid"


STRATEGY_AR = {
    Strategy.TREND_FOLLOWING:      "اتباع الاتجاه",
    Strategy.MEAN_REVERSION:       "ارتداد للمتوسط",
    Strategy.BREAKOUT:             "كسر المستويات",
    Strategy.VOLATILITY_EXPANSION: "توسع التقلب",
    Strategy.ARBITRAGE:            "المراجحة",
    Strategy.ONCHAIN_ACCUMULATION: "تراكم On-Chain",
    Strategy.REDUCE_EXPOSURE:      "تقليل التعرض",
    Strategy.AVOID:                "تجنب الدخول",
}

REGIME_TO_STRATEGIES = {
    Regime.BULL_TREND:       [Strategy.TREND_FOLLOWING, Strategy.BREAKOUT],
    Regime.BEAR_TREND:       [Strategy.MEAN_REVERSION, Strategy.REDUCE_EXPOSURE],
    # ملاحظة: Futures Short تُضاف من SignalLayer.generate() مباشرة
    Regime.SIDEWAYS:         [Strategy.MEAN_REVERSION, Strategy.ARBITRAGE],
    Regime.HIGH_VOLATILITY:  [Strategy.VOLATILITY_EXPANSION, Strategy.REDUCE_EXPOSURE],
    Regime.ACCUMULATION:     [Strategy.ONCHAIN_ACCUMULATION, Strategy.TREND_FOLLOWING],
    Regime.DISTRIBUTION:     [Strategy.REDUCE_EXPOSURE, Strategy.MEAN_REVERSION],
    Regime.UNKNOWN:          [Strategy.REDUCE_EXPOSURE],
}


class StrategyRouter:
    def select(self, regime: RegimeResult, signal: SignalResult) -> Tuple[Strategy, Dict]:
        candidates = REGIME_TO_STRATEGIES.get(regime.regime, [Strategy.REDUCE_EXPOSURE])
        chosen     = candidates[0]

        params = self._strategy_params(chosen, signal, regime)
        return chosen, params

    def _strategy_params(self, strategy: Strategy,
                          signal: SignalResult, regime: RegimeResult) -> Dict:
        base = {
            "entry_type":   "market",
            "partial_exit": False,
            "scale_in":     False,
        }
        if strategy == Strategy.TREND_FOLLOWING:
            base.update({"trailing_stop": True, "scale_in": True, "entry_type": "limit"})
        elif strategy == Strategy.MEAN_REVERSION:
            base.update({"entry_type": "limit", "partial_exit": True,
                          "target_levels": [0.5, 1.0]})
        elif strategy == Strategy.BREAKOUT:
            base.update({"entry_type": "stop_market", "momentum_filter": True})
        elif strategy == Strategy.VOLATILITY_EXPANSION:
            base.update({"size_multiplier": 0.5, "tight_stop": True})
        elif strategy == Strategy.ONCHAIN_ACCUMULATION:
            base.update({"scale_in": True, "dca_intervals": 3,
                          "entry_type": "limit"})
        return base

    def format_ar(self, strategy: Strategy, params: Dict) -> str:
        return (
            f"📐 *الاستراتيجية المختارة*\n"
            f"• {STRATEGY_AR[strategy]}\n"
            f"• نوع الدخول: {params.get('entry_type', 'market')}\n"
            f"• Trailing Stop: {'✅' if params.get('trailing_stop') else '❌'}\n"
            f"• دخول تدريجي: {'✅' if params.get('scale_in') else '❌'}"
        )


# ══════════════════════════════════════════════════════════════
# PORTFOLIO ALLOCATION ENGINE (الطبقة 10)
# ══════════════════════════════════════════════════════════════

@dataclass
class AllocationResult:
    symbol:          str
    allocation_pct:  float    # نسبة من المحفظة
    allocation_usd:  float
    weight_reason:   str
    priority:        int      # 1 = أعلى أولوية


class PortfolioEngine:
    """
    يوزع رأس المال بين الأصول وفق:
    التقلب · السيولة · الارتباط · العائد المتوقع · الـ Regime
    """

    MAX_SINGLE     = 0.25   # 25% أقصى لعملة واحدة
    MAX_POSITIONS  = 5
    MIN_POSITION   = 0.05   # 5% حد أدنى لأي صفقة

    def allocate(self, candidates: List[SignalResult],
                 portfolio_value: float,
                 regime: RegimeResult,
                 event_multiplier: float = 1.0) -> List[AllocationResult]:
        """
        candidates: قائمة إشارات مرتبة بالثقة
        يوزع رأس المال بين الأفضل وفق القيود.
        """
        if not candidates:
            return []

        # فلتر العتبة الدنيا
        qualified = [s for s in candidates if s.confidence >= 0.65
                     and s.direction != "neutral"]
        qualified.sort(key=lambda x: x.confidence, reverse=True)
        qualified = qualified[:self.MAX_POSITIONS]

        if not qualified:
            return []

        # تعديل الحجم الكلي بحسب الـ Regime
        regime_exposure = {
            Regime.BULL_TREND:      0.80,
            Regime.ACCUMULATION:    0.70,
            Regime.SIDEWAYS:        0.50,
            Regime.HIGH_VOLATILITY: 0.30,
            Regime.BEAR_TREND:      0.25,
            Regime.DISTRIBUTION:    0.20,
            Regime.UNKNOWN:         0.20,
        }.get(regime.regime, 0.50)

        available = portfolio_value * regime_exposure

        # توزيع وفق الثقة (confidence-weighted)
        total_conf = sum(s.confidence for s in qualified)
        results    = []

        for i, sig in enumerate(qualified):
            raw_pct = (sig.confidence / total_conf) * regime_exposure
            # تطبيق الحدود
            pct = min(raw_pct, self.MAX_SINGLE)
            pct = max(pct, self.MIN_POSITION)
            usd = round(portfolio_value * pct, 2)

            reason = self._weight_reason(sig, regime)
            results.append(AllocationResult(
                symbol=sig.symbol,
                allocation_pct=round(pct * 100, 1),
                allocation_usd=usd,
                weight_reason=reason,
                priority=i + 1,
            ))

        return results

    def _weight_reason(self, sig: SignalResult, regime: RegimeResult) -> str:
        parts = [f"ثقة {sig.confidence:.0%}"]
        if sig.signal_sources.get("technical", 0) > 0.7:
            parts.append("إشارة تقنية قوية")
        if sig.signal_sources.get("onchain", 0) > 0.7:
            parts.append("دعم On-Chain")
        if sig.onchain_score > 0.7:
            parts.append("TVL مرتفع")
        return " · ".join(parts)

    def format_ar(self, allocations: List[AllocationResult],
                   portfolio: float, regime: RegimeResult) -> str:
        if not allocations:
            return "⚠️ لا توجد صفقات مؤهلة في الوقت الحالي"

        total_deployed = sum(a.allocation_usd for a in allocations)
        lines = [
            f"💼 *توزيع المحفظة — رائد*",
            f"━━━━━━━━━━━━━━━━━━",
            f"إجمالي المحفظة: ${portfolio:,.0f}",
            f"المُستثمر: ${total_deployed:,.0f} ({total_deployed/portfolio:.0%})",
            f"الاحتياطي: ${portfolio-total_deployed:,.0f}",
            f"الحالة: {regime.description_ar}\n",
        ]
        for a in allocations:
            lines.append(
                f"#{a.priority} {a.symbol}: ${a.allocation_usd:,.0f} "
                f"({a.allocation_pct}%) — {a.weight_reason}")
        return "\n".join(lines)


# ── Helpers مشتركة ──────────────────────────────────────────

def _ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    k = 2 / (period + 1)
    v = sum(data[:period]) / period
    for x in data[period:]: v = x * k + v * (1 - k)
    return v

def _sma(data, period):
    if not data: return 0
    return sum(data[-period:]) / min(period, len(data))

def _rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = _sma(gains, period) or 1e-9
    al = _sma(losses, period) or 1e-9
    return 100 - 100 / (1 + ag / al)

def _macd_histogram(closes):
    if len(closes) < 26: return 0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd  = ema12 - ema26
    signal = _ema([macd] * 9, 9)  # تبسيط
    return macd - signal

def _bb_position(closes, period=20):
    if len(closes) < period: return 0.5
    sma  = _sma(closes, period)
    std  = math.sqrt(sum((c - sma) ** 2 for c in closes[-period:]) / period)
    if std == 0: return 0.5
    upper = sma + 2 * std
    lower = sma - 2 * std
    price = closes[-1]
    return (price - lower) / (upper - lower) if upper > lower else 0.5

def _confidence_bar(conf: float) -> str:
    filled = round(conf * 10)
    return "█" * filled + "░" * (10 - filled)

def _regime_confidence_adj(regime: Regime) -> float:
    return {
        Regime.BULL_TREND:      1.05,
        Regime.ACCUMULATION:    1.0,
        Regime.SIDEWAYS:        0.9,
        Regime.HIGH_VOLATILITY: 0.85,
        Regime.BEAR_TREND:      0.8,
        Regime.DISTRIBUTION:    0.75,
        Regime.UNKNOWN:         0.7,
    }.get(regime, 0.9)


def check_macro_trend(candles: list) -> str:
    """
    يتحقق من الاتجاه الكبير (200 شمعة) للتمييز بين:
    - ارتداد ضمن هبوط (bear rally)
    - اتجاه صاعد حقيقي
    """
    if len(candles) < 50:
        return "unknown"
    closes = [c["close"] for c in candles]
    ema50  = _ema(closes, 50)
    ema200 = _ema(closes, 200) if len(closes) >= 200 else _ema(closes, len(closes)//2)
    price  = closes[-1]

    # في هبوط كبير: حتى لو السعر ارتفع مؤخراً — الاتجاه الكبير هابط
    if price < ema200 and ema50 < ema200:
        return "macro_bear"          # ارتداد ضمن هبوط
    elif price > ema200 and ema50 > ema200:
        return "macro_bull"          # اتجاه صاعد حقيقي
    else:
        return "macro_transition"    # انتقال / غير محدد


# Singletons
signal_layer    = SignalLayer()
strategy_router = StrategyRouter()
portfolio_engine = PortfolioEngine()

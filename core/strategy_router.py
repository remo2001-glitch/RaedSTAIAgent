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
# التمييز الثلاثي للسيناريو — الجولة الجديدة
# ══════════════════════════════════════════════════════════════
class TradeScenario:
    """
    يُميّز بين 3 سيناريوهات مختلفة جذرياً:

    TREND_CONTINUATION: الاتجاه يستمر — لا تتداول عكسه
    COUNTER_TREND_BOUNCE: ارتداد مؤقت متوقع — scalp محدود
    TREND_REVERSAL: انعكاس حقيقي — دخول كامل
    """
    TREND_CONTINUATION   = "trend_continuation"   # استمرار الاتجاه
    COUNTER_TREND_BOUNCE = "counter_trend_bounce"  # ارتداد مؤقت عكس الاتجاه
    TREND_REVERSAL       = "trend_reversal"        # انعكاس حقيقي


SCENARIO_AR = {
    TradeScenario.TREND_CONTINUATION:   "📉 استمرار الاتجاه",
    TradeScenario.COUNTER_TREND_BOUNCE: "⚡ ارتداد مؤقت (Counter-trend)",
    TradeScenario.TREND_REVERSAL:       "🔄 انعكاس اتجاه",
}

SCENARIO_MAX_SIZE = {
    TradeScenario.TREND_CONTINUATION:   0.0,    # لا دخول عكس الاتجاه
    TradeScenario.COUNTER_TREND_BOUNCE: 0.12,   # 12% max — scalp فقط
    TradeScenario.TREND_REVERSAL:       0.35,   # 35% — انعكاس مؤكد
}

SCENARIO_TARGET_MULT = {
    TradeScenario.TREND_CONTINUATION:   0.0,
    TradeScenario.COUNTER_TREND_BOUNCE: 1.5,    # هدف صغير: مقاومة قريبة فقط
    TradeScenario.TREND_REVERSAL:       4.0,    # هدف كبير: انعكاس حقيقي
}


def classify_trade_scenario(
    rsi: float,
    fear_greed: int,
    is_bear_regime: bool,
    macd_hist: float,
    bb_pos: float,
    ema_align: bool,
) -> tuple:
    """
    يُصنّف السيناريو التجاري بناءً على المؤشرات.
    يُعيد: (scenario, confidence_adj, warning_ar)
    """
    # ── سوق صاعد ─────────────────────────────────────────────
    if not is_bear_regime:
        if rsi > 70 and not ema_align:
            return (TradeScenario.COUNTER_TREND_BOUNCE,
                    0.9, "⚠️ ذروة شراء في سوق صاعد — ارتداد محتمل")
        return (TradeScenario.TREND_CONTINUATION, 1.0, "")

    # ── سوق هابط ─────────────────────────────────────────────
    # السيناريو 1: استمرار الهبوط (الأكثر احتمالاً)
    if rsi > 40 and not ema_align:
        return (TradeScenario.TREND_CONTINUATION,
                0.80, "📉 الاتجاه هابط — احتفظ بالسيولة")

    # السيناريو 2: ارتداد مؤقت (Counter-trend bounce)
    # شروط: RSI extreme + Fear extreme + Bollinger lower band
    if rsi < 15 and fear_greed < 20 and bb_pos < 0.1:
        # أقوى الإشارات + Bollinger confirmation
        return (TradeScenario.COUNTER_TREND_BOUNCE,
                1.05, "⚡ ذروة بيع تاريخية — ارتداد scalp محتمل (Counter-trend)")
    elif rsi < 20 and fear_greed < 25:
        return (TradeScenario.COUNTER_TREND_BOUNCE,
                0.95, "⚡ ارتداد مؤقت محتمل — scalp فقط، وقف صارم")
    elif rsi < 30 and fear_greed < 30 and bb_pos < 0.2:
        return (TradeScenario.COUNTER_TREND_BOUNCE,
                0.85, "⚡ منطقة oversold — ارتداد محتمل لكن غير مؤكد")

    # السيناريو 3: انعكاس حقيقي
    # يحتاج: MACD يتحول + EMA تبدأ تتقاطع + RSI يتعافى
    if macd_hist > 0 and rsi > 40 and rsi < 60 and not ema_align and fear_greed < 40:
        return (TradeScenario.TREND_REVERSAL,
                1.1, "🔄 احتمال انعكاس — MACD يتحول والسعر يتعافى")

    # افتراضي: استمرار الاتجاه
    # adj=0.85 وليس 0.75 — المعلومات المتاحة مفيدة حتى في TREND_CONTINUATION
    return (TradeScenario.TREND_CONTINUATION,
            0.85, "📉 الاتجاه هابط — احتفظ بالسيولة")


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
    suggested_leverage: int = 1   # تطوير #209: الرافعة المقترحة (1=افتراضي آمن)


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
        # إصلاح #34: bt_score الآن زخم حقيقي خاص بالعملة (ليس 0.55 ثابتاً لكل عملة)
        # backtest_win_rate يُستخدم كـ fallback فقط إذا كانت candles غير كافية
        bt_score = self._momentum_signal(candles)
        if len(candles) < 30:
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

        # ── التمييز الثلاثي للسيناريو ──────────────────────────
        rsi_now        = tech.get("rsi", 50)
        fg_now         = macro_data.get("fear_greed", 50)
        is_bear_regime = regime.regime.value in ("bear_trend", "distribution")
        is_bull_regime = regime.regime.value in ("bull_trend", "accumulation")

        scenario, scenario_adj, scenario_warn = classify_trade_scenario(
            rsi        = rsi_now,
            fear_greed = int(fg_now),
            is_bear_regime = is_bear_regime,
            macd_hist  = tech.get("macd_hist", 0),
            bb_pos     = tech.get("bb_pos", 0.5),
            ema_align  = bool(tech.get("ema_align", False)),
        )

        # regime_adj + scenario_adj + RSI extreme bonus
        regime_adj = _regime_confidence_adj(regime.regime)

        # RSI/Fear extreme = حالة نادرة تاريخياً → رفع raw_conf
        # المنطق: كلما كانت الإشارة أكثر وضوحاً، كلما كانت الثقة أعلى
        if rsi_now < 15 and fg_now < 15:
            raw_conf = min(raw_conf + 0.25, 0.85)  # ذروة تاريخية نادرة
            regime_adj = min(regime_adj + 0.20, 1.05)
        elif rsi_now < 20 and fg_now < 20:
            raw_conf = min(raw_conf + 0.15, 0.80)
            regime_adj = min(regime_adj + 0.15, 1.0)
        elif rsi_now < 30 and fg_now < 25:
            raw_conf = min(raw_conf + 0.08, 0.75)
            regime_adj = min(regime_adj + 0.08, 1.0)
        # ذروة شراء أيضاً
        elif rsi_now > 80 and fg_now > 80:
            raw_conf = min(raw_conf + 0.20, 0.85)

        confidence = min(raw_conf * regime_adj * scenario_adj, 0.97)

        # ── direction بناءً على السيناريو ─────────────────────
        direction  = "neutral"
        trade_type = "spot"
        rsi_val    = rsi_now

        if scenario == TradeScenario.TREND_CONTINUATION:
            # استمرار الاتجاه — لا تتداول عكسه
            if is_bear_regime and rsi_val > 50 and confidence > 0.65:
                direction  = "short"
                trade_type = "futures_short"
            elif is_bull_regime and confidence > 0.65:
                direction  = "long"
                trade_type = "futures_long" if tech.get("ema_align") else "spot"

        elif scenario == TradeScenario.COUNTER_TREND_BOUNCE:
            # ارتداد مؤقت — long scalp محدود
            # إصلاح #61: نفس شرط الحجم المستخدم لاحقاً لتحديد [WAIT]
            # (vol_ratio<0.8) — يمنع تناقض "🟢 شراء XX%" مع "[WAIT]/انتظر"
            if confidence >= 0.60 and tech.get("vol_ratio", 1.0) >= 0.8:
                direction  = "long"
                trade_type = "spot"   # spot فقط في counter-trend

        elif scenario == TradeScenario.TREND_REVERSAL:
            # انعكاس حقيقي — long كامل
            if confidence >= 0.65:
                direction  = "long"
                trade_type = "futures_long" if tech.get("ema_align") else "spot"

        # ذروة شراء = short دائماً
        if rsi_val > 72 and confidence > 0.60 and is_bear_regime:
            direction  = "short"
            trade_type = "futures_short"

        return SignalResult(
            symbol=symbol,
            direction=direction,
            trade_type=trade_type,
            confidence=round(confidence, 3),
            signal_sources={
                "technical": round(tech["score"], 3),
                "onchain":   round(oc_score, 3),
                "news":      round(news_score, 3),
                "momentum":  round(bt_score, 3),
                "macro":     round(macro_score, 3),
            },
            technicals={
                **tech,
                "scenario":        scenario,
                # إصلاح #51/#52: TREND_CONTINUATION كان دائماً "📉" حتى في
                # سوق صاعد (4/4 حالات صعود أعطت أيقونة هابطة خاطئة)
                "scenario_ar":     (
                    ("📈 استمرار الاتجاه الصاعد" if is_bull_regime else
                     "📉 استمرار الاتجاه الهابط" if is_bear_regime else
                     "➡️ استمرار الاتجاه")
                    if scenario == TradeScenario.TREND_CONTINUATION
                    else SCENARIO_AR.get(scenario, "")
                ),
                "scenario_warn":   scenario_warn,
                "max_size_pct":    SCENARIO_MAX_SIZE.get(scenario, 0.15),
                "target_mult":     SCENARIO_TARGET_MULT.get(scenario, 2.0),
            },
            onchain_score=oc_score,
            news_score=news_score,
            backtest_score=bt_score,
            macro_score=macro_score,
            regime=regime.regime.value,
            suggested_leverage=_calc_suggested_leverage(
                confidence, regime, tech, int(fg_now)
            ),
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
        vol_ratio  = vols[-1] / (_sma(vols, 20) or 1)
        vol_avg20  = _sma(vols, 20) or 1

        # تعريف مبكر لمنع NameError (#510)
        rsi_div     = "none"
        vol_profile = "normal"
        conf_flags  = []

        # ── RSI Divergence — محسّن بـ pivot lows ─────────────
        rsi_div = "none"
        if len(closes) >= 30:
            # احسب RSI لكل نقطة
            def _rsi_series(c, p=14):
                if len(c) < p+1: return [50.0]*len(c)
                rs = []
                for i in range(p, len(c)):
                    g = sum(max(c[j]-c[j-1],0) for j in range(i-p+1,i+1)) / p + 1e-9
                    l = sum(max(c[j-1]-c[j],0) for j in range(i-p+1,i+1)) / p + 1e-9
                    rs.append(100-100/(1+g/l))
                return [50.0]*p + rs

            rsi_arr = _rsi_series(closes)

            # أبحث عن آخر قاعين في السعر (pivot lows)
            def find_lows(data, window=5):
                lows = []
                for i in range(window, len(data)-window):
                    if data[i] == min(data[i-window:i+window+1]):
                        lows.append(i)
                return lows[-2:] if len(lows) >= 2 else []

            price_lows = find_lows(closes)
            price_highs_idx = []
            for i in range(3, len(closes)-3):
                if closes[i] == max(closes[i-3:i+4]):
                    price_highs_idx.append(i)
            price_highs_idx = price_highs_idx[-2:] if len(price_highs_idx) >= 2 else []

            if len(price_lows) == 2:
                i1, i2 = price_lows
                # Bullish: سعر أدنى لكن RSI أعلى
                if closes[i2] < closes[i1] and rsi_arr[i2] > rsi_arr[i1]:
                    rsi_div = "bullish"
            if rsi_div == "none" and len(price_highs_idx) == 2:
                i1, i2 = price_highs_idx
                # Bearish: سعر أعلى لكن RSI أدنى
                if closes[i2] > closes[i1] and rsi_arr[i2] < rsi_arr[i1]:
                    rsi_div = "bearish"

        # ── Volume Profile ────────────────────────────────────
        # Climax selling: حجم ≥3x مع هبوط | Climax buying: حجم ≥3x مع صعود
        vol_profile = "normal"
        if vol_ratio >= 3.0:
            last_change = closes[-1] - closes[-2] if len(closes) >= 2 else 0
            vol_profile = "climax_selling" if last_change < 0 else "climax_buying"
        elif vol_ratio >= 1.5:
            vol_profile = "above_average"
        elif vol_ratio < 0.5:
            vol_profile = "no_demand"

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

        # إصلاح #34: مكوّن مستمر لتمييز عملات ذات نظام نقاط خشن متطابق
        # (مثال: IMX RSI=38 وCFX RSI=32 كانا يُعطيان score مطابق تماماً)
        rsi_cont  = (50 - rsi) / 100   # RSI أقل = ميل صعودي أقوى (contrarian خفيف)
        ema_dist  = (price - ema50) / max(ema50, 0.0001)
        ema_cont  = max(min(ema_dist, 0.2), -0.2)
        cont_score = min(max(0.5 + rsi_cont * 0.6 + ema_cont * 0.4, 0.05), 0.95)
        # دمج: 70% النظام الخشن (bias الرسمي) + 30% مستمر (تمايز دقيق بين العملات)
        score = score * 0.7 + cont_score * 0.3

        return {
            "score":       round(min(max(score, 0), 1), 3),
            "bias":        bias,
            "rsi":         round(rsi, 1),
            "ema_align":   ema_align,
            "macd_hist":   round(macd_hist, 6),
            "bb_pos":      round(bb_pos, 3),
            "vol_ratio":   round(vol_ratio, 2),
            "rsi_div":     rsi_div,
            "vol_profile": vol_profile,
            "conf_flags":  conf_flags,
        }

    def _momentum_signal(self, candles: List[Dict]) -> float:
        """
        إصلاح #34: بديل حقيقي خاص بالعملة لـ backtest_win_rate المُكوَّد=0.55
        (كان ثابتاً لكل عملة، 10% من الثقة = زيف كامل).
        يقيس زخم السعر آخر 30 شمعة منسوباً للتقلب (ATR) — تمايز فعلي بين العملات.
        """
        if len(candles) < 30:
            return 0.5
        closes = [c["close"] for c in candles[-30:]]
        ret_30 = (closes[-1] - closes[0]) / max(closes[0], 0.0001)
        # تطبيع بـ tanh: زخم ±15% خلال 30 يوم → تقريباً ±0.35 حول 0.5
        norm = math.tanh(ret_30 / 0.15) * 0.35
        return round(min(max(0.5 + norm, 0.05), 0.95), 3)

    def _onchain_signal(self, data: Dict) -> float:
        """
        إصلاح #34: كان يعتمد على TVL العالمي ($133B دائماً > $50B)
        → 0.7 ثابتة لكل عملة بدون استثناء (25% من الثقة = ثابت رياضي).
        الآن: يعتمد على whale_ratio + funding_rate الخاصين بالعملة
        (عبر get_signal_enrichment) — بيانات حقيقية تختلف بين العملات.
        """
        if not data:
            return 0.5
        score = 0.5
        has_real_data = False

        whale_ratio = data.get("whale_ratio")
        if whale_ratio is not None and whale_ratio > 0:
            has_real_data = True
            if whale_ratio < 0.8:      # أغلبية Short → تحيُّز عكسي صعودي محتمل
                score += 0.15
            elif whale_ratio > 1.2:    # أغلبية Long مزدحم → خطر تصحيح
                score -= 0.10

        funding_pct = data.get("funding_rate_pct")
        if funding_pct is not None and funding_pct != 0:
            has_real_data = True
            if funding_pct < -0.01:    # Funding سالب = فرصة Long
                score += 0.10
            elif funding_pct > 0.03:   # Funding مرتفع = ضغط على Longs
                score -= 0.10

        if has_real_data:
            return round(min(max(score, 0), 1), 3)

        # fallback للاستدعاءات القديمة التي لا تُمرِّر whale/funding بعد
        tvl = data.get("tvl", 0)
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
            f"• زخم 30 يوم: {s.signal_sources['momentum']:.0%}\n"
            f"• ماكرو:   {s.signal_sources['macro']:.0%}\n\n"
            f"RSI 1D: {rsi_val:.0f} | "
            f"EMA: {'✅' if s.technicals.get('ema_align') else '❌'} (ترتيب تصاعدي: سعر>EMA20>EMA50) | "
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
            parts.append("دعم On-Chain (Whale/Funding)")
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


def _calc_suggested_leverage(
    confidence: float,
    regime,
    tech: dict,
    fear_greed: int,
) -> int:
    """تطوير #209: حساب الرافعة المقترحة بشكل تلقائي وآمن.
    الرافعة الافتراضية دائماً 1x حمايةً للمستخدم.

    قواعد المنطق المالي (أولوية تنازلية):
    1. ثقة < 40% → 1x دائماً (لا صفقة مناسبة أصلاً)
    2. ADX > 30 (اتجاه قوي جداً = خطر دخول مرتفع) → 1x
    3. Fear & Greed < 20 (خوف شديد) → 1x
    4. سوق هابط قوي (BEAR_TREND) → 1x
    5. ثقة 40–60% + سوق محايد/صاعد → 2x
    6. ثقة 60–70% + سوق محايد/صاعد → 3x
    7. ثقة ≥ 70% + سوق صاعد + ADX طبيعي → 5x (حد أقصى آمن)
    """
    adx = float(tech.get("adx", 0) or 0)

    # قواعد الحماية المطلقة
    if confidence < 0.40:                         return 1
    if adx > 30:                                  return 1
    if fear_greed < 20:                           return 1

    _reg = getattr(regime, "regime", regime)
    if hasattr(_reg, "value"):
        _reg = _reg.value
    is_bear = _reg in ("BEAR_TREND", "DISTRIBUTION")
    is_bull = _reg in ("BULL_TREND", "ACCUMULATION")

    if is_bear:                                   return 1
    # إصلاح AA1 (#1457/#1462): 2x فقط عند conf >= 0.50 — لا رافعة بثقة منخفضة
    if confidence < 0.50:                         return 1
    if confidence < 0.60:                         return 2
    if confidence < 0.70:                         return 3
    if is_bull:                                   return 5
    return 3  # محايد مع ثقة عالية


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

"""
⚖️ رائد — Risk Engine (الطبقة 5)
مستقل تماماً — لا يمكن تجاوزه من الاستراتيجية أو التنفيذ.
يطبّق: Position Sizing · Drawdown · Correlation · Exposure · Kelly
"""

import math
import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RiskDecision(Enum):
    APPROVE  = "approve"
    REDUCE   = "reduce"
    REJECT   = "reject"


@dataclass
class RiskAssessment:
    decision:        RiskDecision
    approved_size:   float    # الحجم المعتمد فعلاً (USD)
    requested_size:  float    # الحجم المطلوب أصلاً
    risk_score:      float    # 0=آمن 1=خطر
    stop_loss_pct:   float
    take_profit_pct: float
    max_hold_hours:  int
    reasons:         List[str] = field(default_factory=list)
    warnings:        List[str] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.requested_size <= 0:
            return 0
        return (1 - self.approved_size / self.requested_size) * 100


# ─── إعدادات المخاطر الافتراضية ────────────────────────────────────────────────
DEFAULT_RISK_CONFIG = {
    # حدود المحفظة
    "portfolio_size":       10_000,   # USD إجمالي رأس المال
    "max_risk_per_trade":   0.02,     # 2% من المحفظة لكل صفقة
    "max_daily_loss":       0.03,   # 3% خسارة يومية قصوى
    "max_drawdown":         0.12,   # 12% حد أقصى للـ Drawdown
    "max_open_positions":   5,        # أقصى صفقات مفتوحة
    "max_single_exposure":  0.20,     # 20% من المحفظة لعملة واحدة
    "max_sector_exposure":  0.40,     # 40% لقطاع واحد
    "min_confidence":       0.60,   # عُدِّل: 65% → 60%     # عتبة الثقة الدنيا
    # Stop/Target افتراضي
    "default_stop_pct":     0.05,     # 5%
    "default_target_pct":   0.06,     # إصلاح #805: 6% للـ scalp/counter-trend
    "min_rr_ratio":         1.5,      # نسبة Risk/Reward دنيا
    # تعديلات الحالة
    "volatility_scale":     True,     # تقليل الحجم عند ارتفاع التقلب
    "regime_scale":         True,     # تقليل الحجم بحسب Regime
}


@dataclass
class _UserRiskState:
    """حالة المخاطر لمستخدم واحد."""
    open_pos:   Dict[str, Dict] = field(default_factory=dict)   # symbol → position
    daily_pnl:  Dict[str, float] = field(default_factory=dict)  # date → pnl
    peak_value: Optional[float] = None
    cur_value:  Optional[float] = None


class RiskEngine:
    """
    يمرر كل صفقة محتملة عبر 7 فحوصات مستقلة.
    إذا فشل أي فحص حاسم → الرفض الفوري.
    """

    def __init__(self, config: Optional[Dict] = None):
        self.cfg        = {**DEFAULT_RISK_CONFIG, **(config or {})}
        # الدفعة 43: حالة مستقلة لكل مستخدم (user_id=0 = الحالة العامة/القديمة)
        self._states: Dict[int, "_UserRiskState"] = {}
        self._default_value: float = float(self.cfg["portfolio_size"])

    # ── حالة كل مستخدم ──────────────────────────────────────
    def _st(self, user_id: int = 0) -> "_UserRiskState":
        uid = int(user_id or 0)
        st = self._states.get(uid)
        if st is None:
            st = self._states[uid] = _UserRiskState()
        return st

    # توافق خلفي مع الأسماء القديمة (الحالة العامة)
    @property
    def _open_pos(self) -> Dict[str, Dict]:
        return self._st(0).open_pos

    @property
    def _daily_pnl(self) -> Dict[str, float]:
        return self._st(0).daily_pnl

    # ═══════════════════════════════════════════════════════════
    # نقطة الدخول الرئيسية
    # ═══════════════════════════════════════════════════════════
    MAX_AUTO_LEVERAGE = 3

    def assess(self, symbol: str, direction: str, confidence: float,
               price: float, atr_pct: float, regime: str,
               portfolio_value: Optional[float] = None,
               trade_type: str = "spot",
               is_autotrade: bool = False,
               leverage: int = 1,
               user_id: int = 0,
               open_positions: Optional[Dict[str, float]] = None,
               **kwargs) -> RiskAssessment:
        """
        يُقيّم الصفقة ويُعيد القرار النهائي مع الحجم المعدّل.
        user_id: حالة المخاطر (الخسارة اليومية/Drawdown/الصفقات المفتوحة) مستقلة لكل مستخدم.
        """
        st = self._st(user_id)
        portfolio = portfolio_value or st.cur_value or self._default_value
        # القمة تبدأ من أول قيمة معروفة لهذا المستخدم (لا من 10,000$ الافتراضية)
        if st.peak_value is None or portfolio > st.peak_value:
            if portfolio_value or st.peak_value is None:
                st.peak_value = portfolio
        st.cur_value = portfolio
        reasons, warnings = [], []

        # ── فحص 0: رافعة التداول الآلي
        if is_autotrade and trade_type in ("futures_long", "futures_short"):
            if leverage > self.MAX_AUTO_LEVERAGE:
                msg = "الرافعة " + str(leverage) + "X تتجاوز حد الأوتوتريد (" + str(self.MAX_AUTO_LEVERAGE) + "X) — نفّذ يدوياً"
                return self._reject(msg, 0, confidence)

        # ── فحص 1: الثقة ──────────────────────────────────────
        if confidence < self.cfg["min_confidence"]:
            return self._reject(f"الثقة {confidence:.0%} أقل من الحد {self.cfg['min_confidence']:.0%}",
                                0, 0)

        # ── فحص 2: الخسارة اليومية ────────────────────────────
        today_loss = self._get_today_loss(user_id)
        if today_loss <= -self.cfg["max_daily_loss"] * portfolio:
            return self._reject(
                f"تم بلوغ حد الخسارة اليومية {abs(today_loss):,.0f}$", 0, 0)

        # ── فحص 3: Drawdown ───────────────────────────────────
        drawdown = self._current_drawdown(portfolio, user_id)
        if drawdown >= self.cfg["max_drawdown"]:
            return self._reject(
                f"Drawdown بلغ {drawdown:.0%} — يتجاوز الحد {self.cfg['max_drawdown']:.0%}",
                drawdown, 0)

        # ── فحص 4: عدد الصفقات المفتوحة ──────────────────────
        # open_positions (إن مُرِّر) هو المصدر الموثوق: محفظة المستخدم/صفقاته الفعلية
        if open_positions is not None:
            open_map = {k: {"size_usd": float(v or 0)} for k, v in open_positions.items()}
        else:
            open_map = st.open_pos
        open_count = len(open_map)
        if open_count >= self.cfg["max_open_positions"]:
            return self._reject(
                f"عدد الصفقات المفتوحة {open_count} بلغ الحد الأقصى", drawdown, confidence)

        # ── فحص 5: التعرض لعملة واحدة ─────────────────────────
        existing_exposure = open_map.get(symbol, {}).get("size_usd", 0)
        max_exp = self.cfg["max_single_exposure"] * portfolio
        if existing_exposure >= max_exp:
            return self._reject(
                f"التعرض لـ {symbol} {existing_exposure:,.0f}$ بلغ الحد الأقصى",
                drawdown, confidence)

        # ── حساب الحجم بـ Kelly ────────────────────────────────
        win_rate   = confidence
        stop_cfg   = max(self.cfg["default_stop_pct"], 0.001)   # لا قسمة على صفر
        target_cfg = max(self.cfg["default_target_pct"], 0.001)
        rr         = target_cfg / stop_cfg
        kelly_f    = (win_rate - (1 - win_rate) / max(rr, 0.001))
        kelly_f    = max(0, min(kelly_f, 0.25))   # Half-Kelly cap

        base_risk  = self.cfg["max_risk_per_trade"] * portfolio
        kelly_size = kelly_f * portfolio

        # الحجم الأساسي = أصغر القيمتين
        raw_size = min(base_risk / stop_cfg, kelly_size)

        # ── فحص 6: تعديل التقلب ───────────────────────────────
        vol_scale = 1.0
        if self.cfg["volatility_scale"] and atr_pct > 0:
            # كلما ارتفع ATR% كلما قلّ الحجم
            target_atr = 3.0   # ATR% المرجعي
            vol_scale  = min(target_atr / max(atr_pct, 0.1), 1.0)
            if vol_scale < 0.8:
                warnings.append(f"الحجم مُخفَّض {1-vol_scale:.0%} بسبب تقلب عالٍ (ATR {atr_pct:.1f}%)")

        # ── فحص 7: تعديل الـ Regime ───────────────────────────
        regime_scale = {
            "bull_trend": 1.0, "accumulation": 0.9,
            "sideways":   0.7, "distribution": 0.6,
            "bear_trend": 0.5, "high_volatility": 0.4,
            "unknown":    0.3,
        }.get(regime, 0.7)

        if regime_scale < 0.7:
            regime_label = regime.replace("_", " ")
            warnings.append(f"الحجم مُخفَّض {1-regime_scale:.0%} بسبب حالة السوق: {regime_label}")

        final_size = raw_size * vol_scale * regime_scale

        # ── فحص 8: سقف السيناريو (جديد) ──────────────────────
        # counter-trend bounce = max 12%، trend_reversal = max 35%
        scenario_from_signal = kwargs.get("scenario_max_pct", None) if kwargs else None
        if scenario_from_signal is not None:
            scenario_cap = scenario_from_signal * portfolio
            if final_size > scenario_cap:
                final_size = scenario_cap
                warnings.append(
                    f"الحجم محدود بسقف السيناريو {scenario_from_signal:.0%}"
                    f" ({scenario_cap:,.0f}$) — counter-trend trade"
                )

        # تحقق نسبة R/R — الحد الأدنى المطلق 1:1
        stop_pct   = self._dynamic_stop(atr_pct)
        # الـ TP يجب أن يكون على الأقل = SL (R/R ≥ 1:1)
        min_rr     = max(self.cfg["min_rr_ratio"], 1.0)   # لا نقبل أقل من 1:1 أبداً
        target_pct = stop_pct * max(min_rr, rr)
        actual_rr  = target_pct / stop_pct if stop_pct > 0 else 0

        if actual_rr < 1.0:
            # تعديل TP تلقائياً ليضمن R/R ≥ 1:1
            target_pct = stop_pct
            actual_rr  = 1.0
            warnings.append(f"R/R مُعدَّل تلقائياً → 1:1 (SL={stop_pct*100:.1f}%)")
        elif actual_rr < self.cfg["min_rr_ratio"]:
            warnings.append(f"R/R = {actual_rr:.1f} — أقل من الحد المثالي {self.cfg['min_rr_ratio']}")

        # ── درجة المخاطرة الكلية ──────────────────────────────
        risk_score = (
            (drawdown / self.cfg["max_drawdown"]) * 0.3 +
            (1 - confidence) * 0.3 +
            ((atr_pct / 10) * 0.2) +
            ((open_count / self.cfg["max_open_positions"]) * 0.2)
        )
        risk_score = min(risk_score, 1.0)

        decision = RiskDecision.APPROVE if not warnings else RiskDecision.REDUCE

        max_hours = self._max_hold_hours(regime, atr_pct)

        return RiskAssessment(
            decision=decision,
            approved_size=round(final_size, 2),
            requested_size=round(raw_size, 2),
            risk_score=round(risk_score, 3),
            stop_loss_pct=round(stop_pct * 100, 2),
            take_profit_pct=round(target_pct * 100, 2),
            max_hold_hours=max_hours,
            reasons=reasons,
            warnings=warnings,
        )

    def validate_futures_leverage(self, leverage: int, is_autotrade: bool):
        """يتحقق من الرافعة — يعيد (ok, message)."""
        if is_autotrade and leverage > self.MAX_AUTO_LEVERAGE:
            return False, (
                "⛔ *الرافعة في التداول الآلي محدودة بـ 3X*\n\n"
                "• طلبك: " + str(leverage) + "X\n"
                "• الحد المسموح: 3X\n\n"
                "للتداول برافعة أعلى يُرجى التنفيذ اليدوي عبر /execute"
            )
        if leverage > 10:
            return True, "⚠️ رافعة مرتفعة جداً — خطر تصفية عالٍ"
        return True, ""

    def format_futures_signal_ar(self, symbol: str, direction: str,
                                   entry: float, target: float,
                                   stop: float, leverage: int = 1,
                                   is_autotrade: bool = False) -> str:
        """تنسيق إشارة Futures عربي."""
        rr    = abs(target - entry) / max(abs(stop - entry), 0.0001)
        d_ar  = "📈 Long" if "long" in direction else "📉 Short"
        lev_n = "• الرافعة: " + str(leverage) + "X" + (" (أوتوتريد — حد 3X)" if is_autotrade else "")
        p_fmt = lambda p: ("${:,.2f}".format(p) if p >= 1000 else "${:,.4f}".format(p))
        pct   = lambda a, b: abs(a - b) / max(b, 0.0001) * 100
        lines = [
            d_ar + " Futures — " + symbol,
            "━━━━━━━━━━━━━━━━━━",
            "• دخول: " + p_fmt(entry),
            "• هدف:  " + p_fmt(target) + " (+" + "{:.1f}".format(pct(target, entry)) + "%)",
            "• وقف:  " + p_fmt(stop)   + " (-"  + "{:.1f}".format(pct(stop, entry))  + "%)",
            "• R/R:  1:" + "{:.1f}".format(rr),
            lev_n,
            "",
            "⚠️ متاح لمن لديه ربط Futures فعّال (/live)",
            "⚠️ هذا المحتوى استرشادي — القرار النهائي للمستخدم",
        ]
        return "\n".join(lines)

    # ─── تسجيل الصفقات ──────────────────────────────────────
    def register_trade(self, symbol: str, size_usd: float, direction: str,
                       user_id: int = 0):
        self._st(user_id).open_pos[symbol] = {
            "size_usd":  size_usd,
            "direction": direction,
            "opened_at": time.time(),
        }

    def close_trade(self, symbol: str, pnl: float, user_id: int = 0):
        """يُسجَّل عند الإغلاق الفعلي: يحرّر الصفقة ويُحدّث الخسارة اليومية والقمة."""
        st = self._st(user_id)
        st.open_pos.pop(symbol, None)
        today = _today_str()
        st.daily_pnl[today] = st.daily_pnl.get(today, 0) + pnl
        st.cur_value = (st.cur_value or self._default_value) + pnl
        if st.peak_value is None or st.cur_value > st.peak_value:
            st.peak_value = st.cur_value

    def seed_daily_pnl(self, user_id: int, pnl: float):
        """إعادة بناء ربح/خسارة اليوم بعد إعادة التشغيل (من الصفقات المحفوظة)."""
        st = self._st(user_id)
        st.daily_pnl[_today_str()] = float(pnl)

    def update_portfolio_value(self, value: float, user_id: int = 0):
        st = self._st(user_id)
        st.cur_value = value
        if st.peak_value is None or value > st.peak_value:
            st.peak_value = value

    # ─── Helpers ────────────────────────────────────────────
    def _current_drawdown(self, current: float, user_id: int = 0) -> float:
        peak = self._st(user_id).peak_value
        if not peak or peak <= 0:
            return 0
        return max(0, (peak - current) / peak)

    def _get_today_loss(self, user_id: int = 0) -> float:
        return self._st(user_id).daily_pnl.get(_today_str(), 0)

    def _dynamic_stop(self, atr_pct: float) -> float:
        base = max(self.cfg["default_stop_pct"], 0.001)
        if atr_pct > 0:
            return max(atr_pct / 100 * 1.5, base)
        return base

    def _max_hold_hours(self, regime: str, atr_pct: float) -> int:
        base = 48
        if regime in ("high_volatility", "bear_trend"):
            base = 12
        elif regime in ("bull_trend", "accumulation"):
            base = 72
        if atr_pct > 6:
            base = min(base, 24)
        return base

    def _reject(self, reason: str, drawdown: float, confidence: float) -> RiskAssessment:
        logger.warning(f"❌ Risk Reject: {reason}")
        return RiskAssessment(
            decision=RiskDecision.REJECT,
            approved_size=0,
            requested_size=0,
            risk_score=1.0,
            stop_loss_pct=0,
            take_profit_pct=0,
            max_hold_hours=0,
            reasons=[reason],
        )

    # ── تقرير الحالة ────────────────────────────────────────
    def status_report(self, portfolio: float, user_id: int = 0) -> Dict:
        st = self._st(user_id)
        peak = st.peak_value if st.peak_value else portfolio
        dd = self._current_drawdown(portfolio, user_id)
        today_loss = self._get_today_loss(user_id)
        daily_loss_val = abs(min(today_loss, 0))
        daily_limit    = self.cfg["max_daily_loss"] * portfolio
        daily_used_pct = round(daily_loss_val / max(daily_limit, 1) * 100, 1)
        return {
            "portfolio":        portfolio,
            "peak":             peak,
            "drawdown_pct":     round(dd * 100, 2),
            "today_pnl":        round(today_loss, 2),
            "open_positions":   len(st.open_pos),
            "daily_loss_used":  daily_used_pct,
            "event_exposure_ar": "✅ لا أحداث تؤثر على التداول حالياً",
            "has_trades":        len(st.open_pos) > 0 or any(st.daily_pnl.values()),
        }

    def format_assessment_ar(self, a: RiskAssessment, symbol: str) -> str:
        icons = {
            RiskDecision.APPROVE: "✅",
            RiskDecision.REDUCE:  "⚠️",
            RiskDecision.REJECT:  "❌",
        }
        lines = [
            f"⚖️ *تقييم المخاطر — {symbol}*",
            f"━━━━━━━━━━━━━━━━━━",
            f"{icons[a.decision]} القرار: {_decision_ar(a.decision)}",
        ]

        if a.decision == RiskDecision.REJECT:
            # عند الرفض: أسباب فقط بدون أرقام مُربِكة
            if a.reasons:
                lines.append("")
                lines.append("🚫 *أسباب الرفض:*")
                lines += [f"• {r}" for r in a.reasons]
            lines.append("")
            lines.append("💡 /signal لإشارة عملة أخرى | /quicksignal للتحليل السريع")
        else:
            lines += [
                f"💰 الحجم المعتمد: ${a.approved_size:,.0f}",
            ]
            if a.reduction_pct > 0:
                lines.append(f"📉 تخفيض: {a.reduction_pct:.0f}% من الطلب الأصلي")
            lines += [
                f"🛑 وقف الخسارة: {a.stop_loss_pct:.1f}%",
                f"🎯 هدف الربح:   {a.take_profit_pct:.1f}%",
                f"⏰ أقصى مدة:    {a.max_hold_hours} ساعة",
                f"🌡️ درجة المخاطرة: {a.risk_score:.0%}",
            ]
            if a.warnings:
                lines.append("")
                lines.append("⚠️ *تحذيرات:*")
                lines += [f"• {w}" for w in a.warnings]

        return "\n".join(lines)


def _decision_ar(d: RiskDecision) -> str:
    return {
        RiskDecision.APPROVE: "موافقة",
        RiskDecision.REDUCE:  "موافقة بحجم مخفَّض",
        RiskDecision.REJECT:  "رفض",
    }[d]


def _today_str() -> str:
    from datetime import date
    return str(date.today())


# Singleton
risk_engine = RiskEngine()

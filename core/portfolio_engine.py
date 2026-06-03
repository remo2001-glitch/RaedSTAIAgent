"""
💼 رائد — Capital Allocation Engine (الطبقة 10) — النسخة الكاملة
يوزع رأس المال بين الأصول بناء على:
التقلب · السيولة · الارتباط · العائد المتوقع · الـ Regime · Event Risk
"""

import math
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from core.regime_detector import Regime, RegimeResult

logger = logging.getLogger(__name__)


@dataclass
class AssetAllocation:
    symbol:          str
    rank:            int             # الترتيب بحسب الأولوية
    allocation_pct:  float           # نسبة من المحفظة
    allocation_usd:  float           # المبلغ بالدولار
    max_position:    float           # أقصى حجم مسموح
    volatility_adj:  float           # تعديل التقلب (1=طبيعي)
    liquidity_adj:   float           # تعديل السيولة
    correlation_adj: float           # تعديل الارتباط
    expected_return: float           # العائد المتوقع (%)
    sharpe_estimate: float           # تقدير Sharpe
    rationale_ar:    str             # سبب التوزيع بالعربية


@dataclass
class PortfolioState:
    total_value:      float
    deployed_usd:     float
    cash_reserve:     float
    positions:        List[AssetAllocation]
    regime_exposure:  float          # نسبة التعرض الكلية بحسب الـ Regime
    diversification:  float          # درجة التنويع 0–1
    portfolio_var:    float          # Value at Risk 95%
    max_drawdown_est: float          # تقدير أقصى Drawdown
    rebalance_needed: bool


class CapitalAllocationEngine:
    """
    يوزع رأس المال بطريقة مؤسسية:
    1. يُحدد التعرض الكلي بحسب الـ Regime و Event Risk
    2. يُرتب الأصول بحسب العائد المعدَّل للمخاطر
    3. يُطبق تعديلات التقلب والسيولة والارتباط
    4. يُطبق حدود التركيز
    """

    # ── حدود التوزيع ──────────────────────────────────────────
    MAX_SINGLE_ASSET   = 0.25    # 25% أقصى لأصل واحد
    MIN_SINGLE_ASSET   = 0.03    # 3% حد أدنى
    MAX_CORRELATED_GRP = 0.40    # 40% أقصى لمجموعة مترابطة
    CASH_RESERVE_MIN   = 0.10    # 10% احتياطي نقدي دائم
    MAX_POSITIONS      = 6

    # ── مجموعات الارتباط (عملات تتحرك معاً) ──────────────────
    CORRELATION_GROUPS = {
        "layer1":  ["ETH", "SOL", "ADA", "AVAX", "DOT", "NEAR", "APT"],
        "defi":    ["UNI", "AAVE", "COMP", "CRV", "MKR", "SNX"],
        "layer2":  ["MATIC", "ARB", "OP"],
        "meme":    ["DOGE", "SHIB", "PEPE"],
        "btc":     ["BTC", "WBTC"],
        "exchange":["BNB", "OKB"],
    }

    # ── التعرض الكلي بحسب الـ Regime ──────────────────────────
    REGIME_MAX_EXPOSURE = {
        Regime.BULL_TREND:      0.85,
        Regime.ACCUMULATION:    0.75,
        Regime.SIDEWAYS:        0.55,
        Regime.HIGH_VOLATILITY: 0.35,
        Regime.BEAR_TREND:      0.25,
        Regime.DISTRIBUTION:    0.20,
        Regime.UNKNOWN:         0.20,
    }

    def __init__(self):
        self._volatility_cache: Dict[str, float] = {}   # symbol → atr_pct
        self._last_positions:   Dict[str, float] = {}   # symbol → usd

    # ═══════════════════════════════════════════════════════════
    # نقطة الدخول الرئيسية
    # ═══════════════════════════════════════════════════════════
    def allocate(self,
                  candidates: List[Dict],      # [{symbol, confidence, direction, atr_pct, liquidity_score, expected_return}]
                  portfolio_value: float,
                  regime: RegimeResult,
                  event_multiplier: float = 1.0) -> PortfolioState:
        """
        candidates: قائمة أصول مؤهلة مرتبة بالثقة
        يُعيد توزيع كامل للمحفظة.
        """
        if not candidates or portfolio_value <= 0:
            return self._empty_state(portfolio_value)

        # ── 1. التعرض الكلي ─────────────────────────────────
        regime_exposure = self.REGIME_MAX_EXPOSURE.get(regime.regime, 0.50)
        total_exposure  = regime_exposure * event_multiplier
        total_exposure  = max(total_exposure, 0.0)

        # احتياطي نقدي إلزامي
        max_deploy = portfolio_value * total_exposure * (1 - self.CASH_RESERVE_MIN)

        # ── 2. تصفية وترتيب المرشحين ────────────────────────
        qualified = [c for c in candidates
                      if c.get("confidence", 0) >= 0.65
                      and c.get("direction", "neutral") != "neutral"]
        qualified = qualified[:self.MAX_POSITIONS]

        if not qualified:
            return self._empty_state(portfolio_value)

        # ── 3. حساب الوزن لكل أصل ───────────────────────────
        weighted = []
        for asset in qualified:
            w = self._compute_weight(asset, qualified)
            weighted.append((asset, w))

        # تطبيع الأوزان
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0:
            return self._empty_state(portfolio_value)

        # ── 4. حدود الارتباط ────────────────────────────────
        weighted = self._apply_correlation_caps(weighted)

        # ── 5. تحويل لمبالغ ─────────────────────────────────
        allocations = []
        deployed    = 0.0

        for i, (asset, weight) in enumerate(sorted(weighted, key=lambda x: -x[1])):
            norm_weight = weight / total_weight
            raw_pct     = min(norm_weight * total_exposure, self.MAX_SINGLE_ASSET)
            raw_pct     = max(raw_pct, self.MIN_SINGLE_ASSET)
            usd         = round(raw_pct * portfolio_value, 2)

            # لا تتجاوز الحد المتاح
            if deployed + usd > max_deploy:
                usd     = max(0, max_deploy - deployed)
                raw_pct = usd / portfolio_value

            if usd < 50:   # أقل من 50$ لا قيمة له
                continue

            deployed += usd

            alloc = AssetAllocation(
                symbol=asset["symbol"],
                rank=i + 1,
                allocation_pct=round(raw_pct * 100, 2),
                allocation_usd=usd,
                max_position=round(self.MAX_SINGLE_ASSET * portfolio_value, 2),
                volatility_adj=round(asset.get("vol_adj", 1.0), 3),
                liquidity_adj=round(asset.get("liquidity_score", 0.8), 3),
                correlation_adj=round(asset.get("corr_adj", 1.0), 3),
                expected_return=round(asset.get("expected_return", 0), 2),
                sharpe_estimate=round(self._estimate_sharpe(asset), 2),
                rationale_ar=self._rationale(asset, raw_pct),
            )
            allocations.append(alloc)

        # ── 6. مقاييس المحفظة ───────────────────────────────
        diversification = self._diversification_score(allocations)
        var_95          = self._portfolio_var(allocations, portfolio_value)
        max_dd_est      = var_95 * 2.5   # تقريب بسيط
        rebalance       = self._needs_rebalance(allocations, portfolio_value)

        return PortfolioState(
            total_value=portfolio_value,
            deployed_usd=round(deployed, 2),
            cash_reserve=round(portfolio_value - deployed, 2),
            positions=allocations,
            regime_exposure=round(total_exposure, 3),
            diversification=round(diversification, 3),
            portfolio_var=round(var_95, 2),
            max_drawdown_est=round(max_dd_est, 2),
            rebalance_needed=rebalance,
        )

    # ─── الوزن المُعدَّل للمخاطر ────────────────────────────────
    def _compute_weight(self, asset: Dict, all_assets: List[Dict]) -> float:
        conf     = asset.get("confidence", 0.65)
        exp_ret  = asset.get("expected_return", 5.0)
        atr_pct  = max(asset.get("atr_pct", 3.0), 0.1)
        liq      = asset.get("liquidity_score", 0.7)

        # تعديل التقلب (inverse volatility)
        vol_adj = 3.0 / atr_pct    # ATR 3% مرجعي
        vol_adj = min(max(vol_adj, 0.3), 2.0)

        # تعديل السيولة
        liq_adj = max(liq, 0.3)

        # تقدير Sharpe بسيط
        sharpe = (exp_ret / atr_pct) if atr_pct > 0 else 0.5

        # الوزن = ثقة × Sharpe × vol_adj × liq_adj
        weight = conf * max(sharpe, 0.1) * vol_adj * liq_adj

        # حفظ للـ rationale
        asset["vol_adj"]  = vol_adj
        asset["corr_adj"] = 1.0
        return max(weight, 0.001)

    def _apply_correlation_caps(self, weighted: List) -> List:
        """يُطبق حد التعرض لمجموعة الارتباط."""
        group_exposure: Dict[str, float] = {}
        result = []

        for asset, weight in sorted(weighted, key=lambda x: -x[1]):
            sym   = asset.get("symbol", "")
            group = self._get_group(sym)

            current = group_exposure.get(group, 0)
            if group and current >= self.MAX_CORRELATED_GRP:
                weight *= 0.3   # تقليل شديد للعملات الزائدة
                asset["corr_adj"] = 0.3

            group_exposure[group] = current + (weight / 10)   # تقريب
            result.append((asset, weight))

        return result

    def _get_group(self, symbol: str) -> str:
        for group, symbols in self.CORRELATION_GROUPS.items():
            if symbol.upper() in symbols:
                return group
        return symbol   # مجموعة خاصة به

    # ─── مقاييس المحفظة ─────────────────────────────────────────
    def _estimate_sharpe(self, asset: Dict) -> float:
        exp   = asset.get("expected_return", 5.0)
        risk  = asset.get("atr_pct", 3.0) * 2.5  # تحويل ATR → تقدير انحراف
        rf    = 4.5   # معدل خالي من المخاطر (Fed Rate تقريباً)
        return (exp - rf) / max(risk, 0.1)

    def _diversification_score(self, allocs: List[AssetAllocation]) -> float:
        if len(allocs) <= 1:
            return 0.2
        pcts  = [a.allocation_pct for a in allocs]
        total = sum(pcts) or 1
        hhi   = sum((p / total) ** 2 for p in pcts)   # Herfindahl
        return max(1 - hhi, 0)

    def _portfolio_var(self, allocs: List[AssetAllocation],
                        portfolio: float) -> float:
        """تقدير VaR 95% (تبسيط بدون matrix ارتباط)."""
        if not allocs:
            return 0
        # VaR ≈ 1.65 × volatility المُرجَّحة
        weighted_vol = sum(
            (a.allocation_usd / portfolio) * (1 / max(a.volatility_adj, 0.1)) * 3.0
            for a in allocs
        )
        return portfolio * weighted_vol * 1.65 / 100

    def _needs_rebalance(self, allocs: List[AssetAllocation],
                          portfolio: float) -> bool:
        for sym, prev_usd in self._last_positions.items():
            current = next(
                (a.allocation_usd for a in allocs if a.symbol == sym), 0)
            if portfolio > 0 and abs(current - prev_usd) / portfolio > 0.05:
                return True
        return False

    def _empty_state(self, value: float) -> PortfolioState:
        return PortfolioState(
            total_value=value, deployed_usd=0, cash_reserve=value,
            positions=[], regime_exposure=0, diversification=0,
            portfolio_var=0, max_drawdown_est=0, rebalance_needed=False,
        )

    def _rationale(self, asset: Dict, pct: float) -> str:
        parts = [f"ثقة {asset.get('confidence',0):.0%}"]
        atr = asset.get("atr_pct", 3)
        liq = asset.get("liquidity_score", 0.7)
        if atr < 2.5: parts.append("تقلب منخفض")
        elif atr > 6: parts.append("تقلب عالٍ → تخفيض")
        if liq > 0.8: parts.append("سيولة ممتازة")
        elif liq < 0.5: parts.append("سيولة محدودة")
        exp = asset.get("expected_return", 0)
        if exp > 10: parts.append(f"عائد متوقع {exp:.0f}%")
        return " · ".join(parts)

    # ═══════════════════════════════════════════════════════════
    # تنسيق التقرير
    # ═══════════════════════════════════════════════════════════
    def format_ar(self, state: PortfolioState, regime: RegimeResult) -> str:
        if not state.positions:
            from core.regime_detector import Regime
            reason = ""
            if regime.regime in (Regime.BEAR_TREND, Regime.DISTRIBUTION):
                reason = (
                    f"\n\n📋 *توصية رائد في السوق الهابط*\n"
                    f"• الاحتفاظ بالسيولة {state.cash_reserve:,.0f}$ نقداً\n"
                    f"• انتظار إشارة انعكاس (RSI يرتد فوق 35 أو Fear & Greed < 20)\n"  # إصلاح #215
                    f"• مراقبة {' و '.join(['BTC','ETH'])} لأول إشارة تعافٍ\n"
                    f"• الاستراتيجية الحالية: {' · '.join(regime.strategies).replace('_',' ')}"
                )
            return (
                f"⚠️ لا توجد أصول مؤهلة للتوزيع حالياً\n"
                f"السبب: ثقة السوق منخفضة في {regime.description_ar}"
                + reason
            )

        lines = [
            f"💼 *توزيع المحفظة — رائد*",
            f"━━━━━━━━━━━━━━━━━━",
            f"إجمالي المحفظة:  ${state.total_value:,.0f}",
            f"مُستثمر:         ${state.deployed_usd:,.0f} ({state.deployed_usd/state.total_value:.0%})",
            f"احتياطي نقدي:    ${state.cash_reserve:,.0f}",
            f"",
            f"📊 *مقاييس المحفظة*",
            f"• حالة السوق: {regime.description_ar}",
            f"• التعرض الكلي: {state.regime_exposure:.0%}",
            f"• التنويع: {state.diversification:.0%}",
            f"• VaR 95%: ${state.portfolio_var:,.0f}",
            f"• أقصى Drawdown متوقع: ${state.max_drawdown_est:,.0f}",
            f"{'⚠️ إعادة توازن مطلوبة' if state.rebalance_needed else '✅ المحفظة متوازنة'}",
            f"",
            f"📋 *التوزيع التفصيلي*",
        ]
        for pos in state.positions:
            sharpe_str = f"Sharpe {pos.sharpe_estimate:.1f}" if pos.sharpe_estimate != 0 else ""
            lines.append(
                f"#{pos.rank} {pos.symbol}: ${pos.allocation_usd:,.0f} "
                f"({pos.allocation_pct}%)"
                + (f" — {sharpe_str}" if sharpe_str else "")
            )
            if pos.rationale_ar:
                lines.append(f"   ↳ {pos.rationale_ar}")
        return "\n".join(lines)


# Singleton
capital_engine = CapitalAllocationEngine()

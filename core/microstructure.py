"""
🔬 رائد — Microstructure / Liquidity Layer
يقرأ: Order Book · Depth · Spread · Slippage · Liquidity Pressure
فارق حاسم في الكريبتو — لا تدخل بدون قراءة السيولة.
"""

import math
import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── حدود السيولة ──────────────────────────────────────────────────────────────
MIN_BOOK_DEPTH_USD     = 50_000    # حد أدنى لعمق الـ Order Book
MAX_ACCEPTABLE_SPREAD  = 0.005     # 0.5٪ أقصى spread مقبول
MAX_SLIPPAGE_ESTIMATE  = 0.008     # 0.8٪ أقصى slippage متوقع لكل صفقة
LARGE_ORDER_THRESHOLD  = 5_000     # USD — فوقه نحتاج تحليل عمق
IMBALANCE_THRESHOLD    = 0.65      # نسبة اختلال Order Book


@dataclass
class LiquidityProfile:
    symbol:          str
    bid_price:       float
    ask_price:       float
    spread_pct:      float          # (ask - bid) / mid * 100
    mid_price:       float
    bid_depth_usd:   float          # إجمالي قيمة بيع في ٢٪ من السعر
    ask_depth_usd:   float          # إجمالي قيمة شراء في ٢٪ من السعر
    imbalance:       float          # bid_depth / (bid+ask) — >0.5 ضغط شراء
    estimated_slippage_pct: float   # تقدير الـ slippage لحجم الصفقة
    liquidity_score: float          # 0–1 (1 = سيولة ممتازة)
    pressure:        str            # "buy_pressure" | "sell_pressure" | "neutral"
    is_tradeable:    bool
    warnings:        List[str] = field(default_factory=list)


@dataclass
class OrderFlowSignal:
    """يُعبّر عن الضغط الحالي من خلال تحليل الـ Order Book."""
    symbol:        str
    buy_walls:     List[Tuple[float, float]]   # (سعر، حجم USD)
    sell_walls:    List[Tuple[float, float]]
    support_level: float    # أقرب جدار شراء قوي
    resistance_level: float # أقرب جدار بيع قوي
    net_pressure:  float    # -1 (بيع) → +1 (شراء)


class MicrostructureLayer:
    """
    يستخدم Binance Public API لقراءة Order Book بدون API key.
    يُقدّر الـ slippage ويحكم بإمكانية التداول.
    """

    def __init__(self, session=None):
        self.session  = session
        self._cache:  Dict[str, Tuple[LiquidityProfile, float]] = {}
        self._cache_ttl = 15   # ثانية

    # ═══════════════════════════════════════════════════════════
    # 1. تحليل السيولة الرئيسي
    # ═══════════════════════════════════════════════════════════
    async def analyze(self, symbol: str,
                      order_size_usd: float = 1000) -> LiquidityProfile:
        """
        يجلب Order Book من Binance ويُحلل السيولة.
        مجاني بالكامل — Public API.
        """
        # كاش 15 ثانية
        cached = self._cache.get(symbol)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        try:
            import aiohttp
            url = (f"https://api.binance.com/api/v3/depth"
                   f"?symbol={symbol.upper()}USDT&limit=50")
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    profile = self._compute_profile(symbol, data, order_size_usd)
                    self._cache[symbol] = (profile, time.time())
                    return profile
        except Exception as e:
            logger.warning(f"Microstructure fetch fail ({symbol}): {e}")

        return self._fallback_profile(symbol)

    def _compute_profile(self, symbol: str, book: Dict,
                          order_size_usd: float) -> LiquidityProfile:
        bids = [(float(p), float(q)) for p, q in book.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in book.get("asks", [])]

        if not bids or not asks:
            return self._fallback_profile(symbol)

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid      = (best_bid + best_ask) / 2
        spread   = (best_ask - best_bid) / mid if mid > 0 else 0

        # عمق ٢٪ من السعر
        depth_range = 0.02
        bid_depth   = sum(p * q for p, q in bids if p >= best_bid * (1 - depth_range))
        ask_depth   = sum(p * q for p, q in asks if p <= best_ask * (1 + depth_range))

        total_depth = bid_depth + ask_depth
        imbalance   = bid_depth / total_depth if total_depth > 0 else 0.5

        # تقدير Slippage للحجم المطلوب
        slippage = self._estimate_slippage(asks if True else bids,
                                            order_size_usd, mid, side="buy")

        # ضغط السوق
        if imbalance > IMBALANCE_THRESHOLD:
            pressure = "buy_pressure"
        elif imbalance < (1 - IMBALANCE_THRESHOLD):
            pressure = "sell_pressure"
        else:
            pressure = "neutral"

        # درجة السيولة
        score = 1.0
        warnings = []

        if spread > MAX_ACCEPTABLE_SPREAD:
            score -= 0.3
            warnings.append(f"Spread عالٍ {spread*100:.2f}٪")

        if bid_depth < MIN_BOOK_DEPTH_USD:
            score -= 0.3
            warnings.append(f"عمق شراء ضعيف ${bid_depth:,.0f}")

        if slippage > MAX_SLIPPAGE_ESTIMATE:
            score -= 0.2
            warnings.append(f"Slippage متوقع {slippage*100:.2f}٪")

        if imbalance < 0.35:
            score -= 0.15
            warnings.append("ضغط بيع كبير في Order Book")

        score = max(score, 0.0)
        is_tradeable = (score >= 0.4
                        and spread <= MAX_ACCEPTABLE_SPREAD * 2
                        and slippage <= MAX_SLIPPAGE_ESTIMATE * 2)

        return LiquidityProfile(
            symbol=symbol,
            bid_price=round(best_bid, 6),
            ask_price=round(best_ask, 6),
            spread_pct=round(spread * 100, 4),
            mid_price=round(mid, 6),
            bid_depth_usd=round(bid_depth, 2),
            ask_depth_usd=round(ask_depth, 2),
            imbalance=round(imbalance, 3),
            estimated_slippage_pct=round(slippage * 100, 4),
            liquidity_score=round(score, 3),
            pressure=pressure,
            is_tradeable=is_tradeable,
            warnings=warnings,
        )

    def _estimate_slippage(self, levels: List[Tuple],
                            order_usd: float, mid: float,
                            side: str = "buy") -> float:
        """
        يُحاكي تنفيذ أمر بـ order_usd$ ويُحسب متوسط الانزلاق.
        """
        remaining = order_usd
        filled_value = 0.0
        filled_qty   = 0.0

        for price, qty in levels:
            level_value = price * qty
            take        = min(remaining, level_value)
            take_qty    = take / price
            filled_value += take
            filled_qty   += take_qty
            remaining    -= take
            if remaining <= 0:
                break

        if filled_qty == 0 or mid == 0:
            return 0.01   # 1٪ افتراضي عند عدم كفاية السيولة

        avg_price  = filled_value / filled_qty
        slippage   = abs(avg_price - mid) / mid
        return slippage

    def _fallback_profile(self, symbol: str) -> LiquidityProfile:
        return LiquidityProfile(
            symbol=symbol, bid_price=0, ask_price=0,
            spread_pct=0.3, mid_price=0,
            bid_depth_usd=100_000, ask_depth_usd=100_000,
            imbalance=0.5, estimated_slippage_pct=0.1,
            liquidity_score=0.5, pressure="neutral",
            is_tradeable=True,
            warnings=["بيانات Order Book غير متاحة — تقديرات افتراضية"],
        )

    # ═══════════════════════════════════════════════════════════
    # 2. تحليل الجدران (Walls)
    # ═══════════════════════════════════════════════════════════
    async def detect_walls(self, symbol: str,
                            wall_threshold_usd: float = 100_000) -> OrderFlowSignal:
        """يكشف جدران الشراء/البيع القوية في Order Book."""
        try:
            import aiohttp
            url = (f"https://api.binance.com/api/v3/depth"
                   f"?symbol={symbol.upper()}USDT&limit=100")
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    return self._compute_walls(symbol, data, wall_threshold_usd)
        except Exception as e:
            logger.warning(f"Wall detection fail ({symbol}): {e}")

        return OrderFlowSignal(symbol, [], [], 0, 0, 0)

    def _compute_walls(self, symbol: str, book: Dict,
                        threshold: float) -> OrderFlowSignal:
        bids = [(float(p), float(p) * float(q))
                for p, q in book.get("bids", [])]
        asks = [(float(p), float(p) * float(q))
                for p, q in book.get("asks", [])]

        buy_walls  = [(p, v) for p, v in bids if v >= threshold]
        sell_walls = [(p, v) for p, v in asks if v >= threshold]

        support    = buy_walls[0][0]  if buy_walls  else (bids[-1][0]  if bids  else 0)
        resistance = sell_walls[0][0] if sell_walls else (asks[-1][0]  if asks  else 0)

        # ضغط صافٍ
        total_buy  = sum(v for _, v in buy_walls)  if buy_walls  else 1
        total_sell = sum(v for _, v in sell_walls) if sell_walls else 1
        net = (total_buy - total_sell) / (total_buy + total_sell)

        return OrderFlowSignal(
            symbol=symbol,
            buy_walls=buy_walls[:5],
            sell_walls=sell_walls[:5],
            support_level=round(support, 6),
            resistance_level=round(resistance, 6),
            net_pressure=round(net, 3),
        )

    # ═══════════════════════════════════════════════════════════
    # 3. تعديل حجم الصفقة بناء على السيولة
    # ═══════════════════════════════════════════════════════════
    def adjust_size_for_liquidity(self, requested_usd: float,
                                   profile: LiquidityProfile) -> Tuple[float, str]:
        """
        يُعدّل حجم الصفقة بناء على عمق السيولة.
        يمنع التأثير الكبير على السوق.
        """
        reason = ""
        adj_size = requested_usd

        # لا تتجاوز ٥٪ من عمق الطرف المقابل
        max_safe = min(profile.bid_depth_usd, profile.ask_depth_usd) * 0.05
        if requested_usd > max_safe and max_safe > 0:
            adj_size = max_safe
            reason   = f"تقليل لعدم التأثير على السوق (عمق {max_safe:,.0f}$)"

        # تعديل بـ liquidity score
        adj_size = adj_size * max(profile.liquidity_score, 0.3)

        return round(adj_size, 2), reason

    # ═══════════════════════════════════════════════════════════
    # 4. تنسيق التقرير
    # ═══════════════════════════════════════════════════════════
    def format_ar(self, p: LiquidityProfile) -> str:
        pressure_ar = {
            "buy_pressure":  "🟢 ضغط شراء",
            "sell_pressure": "🔴 ضغط بيع",
            "neutral":       "⚪ متوازن",
        }.get(p.pressure, "⚪")

        tradeable = "✅ قابل للتداول" if p.is_tradeable else "❌ سيولة غير كافية"
        score_bar = "█" * round(p.liquidity_score * 10) + "░" * (10 - round(p.liquidity_score * 10))

        lines = [
            f"🔬 *تحليل السيولة — {p.symbol}*",
            f"━━━━━━━━━━━━━━━━━━",
            f"السيولة: {score_bar} {p.liquidity_score:.0%}",
            f"الحكم: {tradeable}",
            f"",
            f"📊 *Order Book*",
            f"• Spread: {p.spread_pct:.3f}٪",
            f"• عمق الشراء: ${p.bid_depth_usd:,.0f}",
            f"• عمق البيع:  ${p.ask_depth_usd:,.0f}",
            f"• الاختلال:   {p.imbalance:.0%} ({pressure_ar})",
            f"• Slippage متوقع: {p.estimated_slippage_pct:.3f}٪",
        ]
        if p.warnings:
            lines += ["", "⚠️ *تحذيرات:*"] + [f"• {w}" for w in p.warnings]
        return "\n".join(lines)


# Singleton (يحتاج session — يُمرر عند التهيئة)
microstructure_layer = MicrostructureLayer()

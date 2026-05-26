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
        يُحلل السيولة:
        ١. Binance Order Book (حقيقي) — إذا فشل →
        ٢. CoinGecko (حجم تداول حقيقي) — إذا فشل →
        ٣. تقديرات افتراضية آمنة
        """
        cached = self._cache.get(symbol)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        # ── ١. Binance Order Book ─────────────────────────────
        try:
            import aiohttp
            url = (f"https://api.binance.com/api/v3/depth"
                   f"?symbol={symbol.upper()}USDT&limit=50")
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                if r.status == 200:
                    data    = await r.json()
                    profile = self._compute_profile(symbol, data, order_size_usd)
                    self._cache[symbol] = (profile, time.time())
                    return profile
        except Exception as e:
            logger.warning(f"Binance Order Book ({symbol}): {e}")

        # ── ٢. CoinGecko — حجم تداول حقيقي ──────────────────
        try:
            from core.data_layer import _cg_id, _fetch, _H_CG
            if self.session:
                cg   = _cg_id(symbol)
                data = await _fetch(
                    self.session,
                    f"https://api.coingecko.com/api/v3/coins/{cg}",
                    headers=_H_CG,
                    params={"localization": "false", "tickers": "false",
                            "community_data": "false", "developer_data": "false"},
                    retries=2,
                )
                if isinstance(data, dict) and "market_data" in data:
                    md         = data["market_data"]
                    price      = float(md.get("current_price", {}).get("usd") or 0)
                    volume_24h = float(md.get("total_volume", {}).get("usd") or 0)
                    market_cap = float(md.get("market_cap", {}).get("usd") or 0)
                    if price > 0 and volume_24h > 0:
                        profile = self._fallback_profile(
                            symbol, price, volume_24h, market_cap)
                        logger.info(
                            f"Liquidity CoinGecko ({symbol}): "
                            f"vol=${volume_24h/1e6:.0f}M | score={profile.liquidity_score:.2f}")
                        self._cache[symbol] = (profile, time.time())
                        return profile
        except Exception as e2:
            logger.warning(f"CoinGecko liquidity ({symbol}): {e2}")

        # ── ٣. Fallback آمن ───────────────────────────────────
        profile = self._fallback_profile(symbol)
        self._cache[symbol] = (profile, time.time())
        return profile


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

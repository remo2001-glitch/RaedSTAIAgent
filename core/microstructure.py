"""
🔬 رائد — Microstructure Layer (الطبقة ٧)
يُحلل السيولة وجودة التنفيذ
١. Binance Order Book (حقيقي)
٢. CoinGecko (حجم تداول حقيقي) — fallback
٣. تقديرات آمنة — fallback نهائي
"""

import time
import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class LiquidityProfile:
    symbol:                  str
    bid_price:               float
    ask_price:               float
    spread_pct:              float
    mid_price:               float
    bid_depth_usd:           float
    ask_depth_usd:           float
    imbalance:               float        # 0=كل بيع, 1=كل شراء
    estimated_slippage_pct:  float
    liquidity_score:         float        # 0-1
    pressure:                str          # "شراء" | "بيع" | "neutral"
    is_tradeable:            bool
    warnings:                List[str]    = field(default_factory=list)
    source:                  str          = "unknown"


@dataclass
class OrderFlowSignal:
    symbol:          str
    buy_walls:       List[Dict]
    sell_walls:      List[Dict]
    net_pressure:    float
    support_level:   float
    resistance_level: float


class MicrostructureLayer:

    _cache_ttl = 15   # ثانية

    def __init__(self):
        self._cache: Dict[str, Tuple[LiquidityProfile, float]] = {}
        self.session: Optional[aiohttp.ClientSession] = None

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
        BN_ENDPOINTS = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
        ]
        for ep in BN_ENDPOINTS:
            try:
                url = f"{ep}/api/v3/depth?symbol={symbol.upper()}USDT&limit=50"
                if self.session:
                    async with self.session.get(
                        url, timeout=aiohttp.ClientTimeout(total=6)
                    ) as r:
                        if r.status == 200:
                            data    = await r.json()
                            profile = self._compute_profile(symbol, data, order_size_usd)
                            self._cache[symbol] = (profile, time.time())
                            return profile
            except Exception as e:
                logger.warning(f"Binance Order Book {ep} ({symbol}): {e}")
                continue

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
                        profile = self._build_profile_from_market_data(
                            symbol, price, volume_24h, market_cap)
                        logger.info(
                            f"Liquidity CoinGecko ({symbol}): "
                            f"vol=${volume_24h/1e6:.0f}M | "
                            f"score={profile.liquidity_score:.2f}")
                        self._cache[symbol] = (profile, time.time())
                        return profile
        except Exception as e2:
            logger.warning(f"CoinGecko liquidity ({symbol}): {e2}")

        # ── ٣. Fallback آمن ───────────────────────────────────
        profile = self._build_safe_fallback(symbol)
        self._cache[symbol] = (profile, time.time())
        return profile

    def _build_profile_from_market_data(self, symbol: str, price: float,
                                          volume_24h: float,
                                          market_cap: float) -> LiquidityProfile:
        """
        يبني LiquidityProfile من بيانات CoinGecko الحقيقية.
        الحسابات مبنية على معادلات سوقية واقعية.
        """
        # عمق الشراء/البيع ≈ 2% من حجم التداول اليومي
        depth_usd     = volume_24h * 0.02
        bid_depth_usd = depth_usd * 0.52   # ضغط شراء أكبر قليلاً
        ask_depth_usd = depth_usd * 0.48

        # Spread حسب Market Cap (معادلة سوقية واقعية)
        if market_cap > 100e9:   spread = 0.05   # BTC/ETH — ضيق جداً
        elif market_cap > 10e9:  spread = 0.10   # Large cap
        elif market_cap > 1e9:   spread = 0.20   # Mid cap
        else:                    spread = 0.50   # Small cap

        slippage  = spread / 2

        # نسبة السيولة حسب حجم التداول اليومي
        if volume_24h > 10e9:    liq_score = 0.95
        elif volume_24h > 1e9:   liq_score = 0.85
        elif volume_24h > 100e6: liq_score = 0.70
        elif volume_24h > 10e6:  liq_score = 0.50
        else:                    liq_score = 0.30

        imbalance = bid_depth_usd / (bid_depth_usd + ask_depth_usd)
        pressure  = ("شراء" if imbalance > 0.52
                     else "بيع" if imbalance < 0.48
                     else "neutral")

        return LiquidityProfile(
            symbol=symbol,
            bid_price=round(price * (1 - spread / 200), 6),
            ask_price=round(price * (1 + spread / 200), 6),
            spread_pct=round(spread, 3),
            mid_price=price,
            bid_depth_usd=round(bid_depth_usd, 0),
            ask_depth_usd=round(ask_depth_usd, 0),
            imbalance=round(imbalance, 3),
            estimated_slippage_pct=round(slippage, 3),
            liquidity_score=round(liq_score, 3),
            pressure=pressure,
            is_tradeable=liq_score >= 0.4,
            warnings=["البيانات من CoinGecko (حجم تداول حقيقي)"],
            source="coingecko",
        )

    def _build_safe_fallback(self, symbol: str) -> LiquidityProfile:
        """Fallback آمن عند فشل جميع المصادر."""
        return LiquidityProfile(
            symbol=symbol,
            bid_price=0, ask_price=0,
            spread_pct=0.3, mid_price=0,
            bid_depth_usd=50_000, ask_depth_usd=50_000,
            imbalance=0.5,
            estimated_slippage_pct=0.15,
            liquidity_score=0.4,
            pressure="neutral",
            is_tradeable=True,
            warnings=["بيانات غير متاحة — تقديرات افتراضية"],
            source="fallback",
        )

    # ═══════════════════════════════════════════════════════════
    # 2. تحليل الجدران (Walls)
    # ═══════════════════════════════════════════════════════════
    async def detect_walls(self, symbol: str,
                            depth_limit: int = 100) -> OrderFlowSignal:
        """يكشف جدران الشراء والبيع في Order Book."""
        BN_ENDPOINTS = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
        ]
        for ep in BN_ENDPOINTS:
            try:
                if self.session:
                    url = f"{ep}/api/v3/depth?symbol={symbol.upper()}USDT&limit={depth_limit}"
                    async with self.session.get(
                        url, timeout=aiohttp.ClientTimeout(total=6)
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            return self._compute_walls(symbol, data)
            except Exception as e:
                logger.warning(f"detect_walls {ep} ({symbol}): {e}")
                continue

        # fallback: OKX Public API (لا يحتاج API key)
        try:
            if self.session:
                okx_sym = f"{symbol.upper()}-USDT"
                url = f"https://www.okx.com/api/v5/market/books?instId={okx_sym}&sz={depth_limit}"
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=6)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        books = (data.get("data") or [{}])[0]
                        if books.get("bids") or books.get("asks"):
                            return self._compute_walls_from_levels(
                                symbol,
                                books.get("bids", []),
                                books.get("asks", []),
                            )
        except Exception as e:
            logger.warning(f"OKX Order Book ({symbol}): {e}")

        # fallback: Bybit Public API
        try:
            if self.session:
                url = f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={symbol.upper()}USDT&limit={depth_limit}"
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=6)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        result_data = (data.get("result") or {})
                        bids = result_data.get("b", [])
                        asks = result_data.get("a", [])
                        if bids or asks:
                            return self._compute_walls_from_levels(symbol, bids, asks)
        except Exception as e:
            logger.warning(f"Bybit Order Book ({symbol}): {e}")

        # OKX Public API fallback
        try:
            if self.session:
                okx_sym = f"{symbol.upper()}-USDT"
                url = f"https://www.okx.com/api/v5/market/books?instId={okx_sym}&sz={depth_limit}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        books = (data.get("data") or [{}])[0]
                        if books.get("bids") or books.get("asks"):
                            return self._compute_walls_from_levels(
                                symbol, books.get("bids", []), books.get("asks", []))
        except Exception as e:
            logger.debug(f"OKX walls ({symbol}): {e}")

        # Bybit Public API fallback
        try:
            if self.session:
                url = f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={symbol.upper()}USDT&limit={depth_limit}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        data = await r.json()
                        res  = data.get("result", {})
                        bids, asks = res.get("b", []), res.get("a", [])
                        if bids or asks:
                            return self._compute_walls_from_levels(symbol, bids, asks)
        except Exception as e:
            logger.debug(f"Bybit walls ({symbol}): {e}")

        return OrderFlowSignal(symbol, [], [], 0, 0, 0)

    def _compute_walls(self, symbol: str, book: Dict) -> OrderFlowSignal:
        """يحسب الجدران من Order Book."""
        threshold = 500_000   # $500K = جدار معتبر

        buy_walls  = []
        sell_walls = []

        for price_str, qty_str in book.get("bids", []):
            try:
                p   = float(price_str)
                q   = float(qty_str)
                val = p * q
                if val >= threshold:
                    buy_walls.append({"price": p, "value_usd": val})
            except (ValueError, TypeError):
                continue

        for price_str, qty_str in book.get("asks", []):
            try:
                p   = float(price_str)
                q   = float(qty_str)
                val = p * q
                if val >= threshold:
                    sell_walls.append({"price": p, "value_usd": val})
            except (ValueError, TypeError):
                continue

        buy_val  = sum(w["value_usd"] for w in buy_walls)
        sell_val = sum(w["value_usd"] for w in sell_walls)
        total    = buy_val + sell_val
        pressure = (buy_val - sell_val) / total if total > 0 else 0

        support    = min((w["price"] for w in buy_walls),  default=0)
        resistance = min((w["price"] for w in sell_walls), default=0)

        return OrderFlowSignal(
            symbol=symbol,
            buy_walls=sorted(buy_walls,  key=lambda x: x["value_usd"], reverse=True)[:3],
            sell_walls=sorted(sell_walls, key=lambda x: x["value_usd"], reverse=True)[:3],
            net_pressure=round(pressure, 3),
            support_level=support,
            resistance_level=resistance,
        )

    # ═══════════════════════════════════════════════════════════
    # 3. تحليل Order Book من بيانات خام
    # ═══════════════════════════════════════════════════════════
    def _compute_profile(self, symbol: str, book: Dict,
                          order_size_usd: float) -> LiquidityProfile:
        """يحسب السيولة من Order Book الحقيقي."""
        bids = [(float(p), float(q)) for p, q in book.get("bids", [])[:20]]
        asks = [(float(p), float(q)) for p, q in book.get("asks", [])[:20]]

        if not bids or not asks:
            return self._build_safe_fallback(symbol)

        best_bid  = bids[0][0]
        best_ask  = asks[0][0]
        mid_price = (best_bid + best_ask) / 2
        spread    = ((best_ask - best_bid) / mid_price * 100) if mid_price > 0 else 0.3

        bid_depth = sum(p * q for p, q in bids)
        ask_depth = sum(p * q for p, q in asks)
        total_d   = bid_depth + ask_depth
        imbalance = bid_depth / total_d if total_d > 0 else 0.5
        pressure  = ("شراء" if imbalance > 0.55
                     else "بيع" if imbalance < 0.45
                     else "neutral")

        # Slippage لحجم الأمر
        remaining = order_size_usd
        exec_val  = 0.0
        exec_qty  = 0.0
        for p, q in asks:
            available = p * q
            used      = min(remaining, available)
            exec_val += used
            exec_qty += used / p
            remaining -= used
            if remaining <= 0:
                break
        avg_price = exec_val / exec_qty if exec_qty > 0 else best_ask
        slippage  = abs(avg_price - mid_price) / mid_price * 100 if mid_price > 0 else 0.1

        # نسبة السيولة
        if spread < 0.05:   liq_score = 0.95
        elif spread < 0.1:  liq_score = 0.85
        elif spread < 0.3:  liq_score = 0.70
        elif spread < 0.5:  liq_score = 0.55
        else:               liq_score = 0.35

        return LiquidityProfile(
            symbol=symbol,
            bid_price=round(best_bid, 6),
            ask_price=round(best_ask, 6),
            spread_pct=round(spread, 4),
            mid_price=round(mid_price, 6),
            bid_depth_usd=round(bid_depth, 0),
            ask_depth_usd=round(ask_depth, 0),
            imbalance=round(imbalance, 3),
            estimated_slippage_pct=round(slippage, 4),
            liquidity_score=round(liq_score, 3),
            pressure=pressure,
            is_tradeable=liq_score >= 0.4,
            warnings=[],
            source="binance",
        )

    # ═══════════════════════════════════════════════════════════
    # 4. تعديل الحجم بناءً على السيولة
    # ═══════════════════════════════════════════════════════════
    def adjust_size_for_liquidity(self, size_usd: float,
                                   profile: LiquidityProfile
                                   ) -> Tuple[float, str]:
        """يُعدّل حجم الأمر بناءً على السيولة."""
        if not profile or not isinstance(profile, LiquidityProfile):
            return size_usd, ""

        score = profile.liquidity_score
        if score >= 0.8:
            return size_usd, ""
        elif score >= 0.6:
            adj = size_usd * 0.75
            return adj, f"الحجم مُخفَّض 25% (سيولة متوسطة: {score:.0%})"
        elif score >= 0.4:
            adj = size_usd * 0.5
            return adj, f"الحجم مُخفَّض 50% (سيولة منخفضة: {score:.0%})"
        else:
            adj = size_usd * 0.25
            return adj, f"الحجم مُخفَّض 75% (سيولة ضعيفة: {score:.0%})"

    # ═══════════════════════════════════════════════════════════
    # 5. تنسيق التقرير
    # ═══════════════════════════════════════════════════════════
    def format_ar(self, profile: LiquidityProfile,
                   walls: "OrderFlowSignal" = None) -> str:
        if not profile:
            return "⚠️ لا تتوفر بيانات سيولة"

        score_bar = "█" * round(profile.liquidity_score * 10)
        score_bar = score_bar.ljust(10, "░")
        source_label = {
            "binance":   "Binance Order Book",
            "coingecko": "CoinGecko (تقديري)",
            "fallback":  "تقديرات افتراضية",
        }.get(profile.source, profile.source)

        lines = [
            f"🔬 *تحليل السيولة — {profile.symbol}*",
            "━━━━━━━━━━━━━━━━━━",
            f"السيولة: {score_bar} {profile.liquidity_score:.0%}",
            f"الحكم: {'✅ قابل للتداول' if profile.is_tradeable else '⛔ غير موصى بالتداول'}",
            "",
            "📊 *Order Book*",
            f"• Spread: {profile.spread_pct:.3f}٪",
        ]

        if profile.bid_depth_usd > 0:
            lines += [
                f"• عمق الشراء: ${profile.bid_depth_usd:,.0f}",
                f"• عمق البيع:  ${profile.ask_depth_usd:,.0f}",
                f"• الاختلال:   {profile.imbalance:.0%} "
                f"({'🟢 ضغط شراء قوي' if profile.imbalance > 0.60 else '🟢 ضغط شراء خفيف' if profile.imbalance > 0.52 else '🔴 ضغط بيع قوي' if profile.imbalance < 0.40 else '🔴 ضغط بيع خفيف' if profile.imbalance < 0.48 else '⚪ متوازن'})",
            ]

        lines.append(f"• Slippage متوقع: {profile.estimated_slippage_pct:.3f}٪")

        # أكبر أوامر الشراء والبيع (من walls إذا متاحة)
        if walls and not isinstance(walls, Exception):
            buy_walls  = getattr(walls, "buy_walls",  []) or []
            sell_walls = getattr(walls, "sell_walls", []) or []
            if buy_walls:
                lines += ["", "📋 *أكبر أوامر الشراء (Bids)*"]
                for w in buy_walls[:3]:
                    lines.append(f"• ${w['price']:,.2f} — ${w['value_usd']/1e6:.2f}M")
            if sell_walls:
                lines += ["", "📋 *أكبر أوامر البيع (Asks)*"]
                for w in sell_walls[:3]:
                    lines.append(f"• ${w['price']:,.2f} — ${w['value_usd']/1e6:.2f}M")
            if not buy_walls and not sell_walls:
                lines += ["", "📋 جدران السوق: لا توجد أوامر كبيرة حالياً"]

            # دعم ومقاومة
            sup = getattr(walls, "support_level", 0)
            res = getattr(walls, "resistance_level", 0)
            net = getattr(walls, "net_pressure", 0)
            if sup > 0 or res > 0:
                net_ar = ("🟢 ضغط شراء" if net > 0.1 else
                          "🔴 ضغط بيع"  if net < -0.1 else "⚪ متوازن")
                lines += ["", "🧱 *جدران السوق*"]
                if sup > 0:
                    lines.append(f"• دعم:    ${sup:,.2f}")
                if res > 0:
                    lines.append(f"• مقاومة: ${res:,.2f}")
                lines.append(f"• الضغط:  {net_ar}")
        else:
            lines += ["", "📋 *جدران السوق:* غير متاحة (Binance مطلوب)"]

        # تحذير إذا CoinGecko
        if profile.source == "coingecko":
            lines += ["", "⚠️ البيانات تقديرية من CoinGecko — اربط منصتك عبر /live للدقة"]
        elif profile.source == "fallback":
            lines += ["", "⚠️ بيانات افتراضية — تحقق من الاتصال"]

        lines += ["", f"📡 المصدر: {source_label}"]
        return "\n".join(lines)


# Singleton
microstructure_layer = MicrostructureLayer()

"""
🔬 رائد — Microstructure Layer (الطبقة 7)
يُحلل السيولة وجودة التنفيذ
1. Binance Order Book (حقيقي)
2. CoinGecko (حجم تداول حقيقي) — fallback
3. تقديرات آمنة — fallback نهائي
"""

import time
import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import aiohttp

logger = logging.getLogger(__name__)


def _imbalance_label(imb: float) -> str:
    """تسمية موحَّدة لاختلال السيولة — تُستخدم في 'الاختلال' و'الضغط'
    لضمان عدم تعارضهما (إصلاح: 54% كان يُعرض '🟢خفيف' و'⚪متوازن' معاً)."""
    if imb > 0.60:   return "🟢 ضغط شراء قوي"
    if imb > 0.52:   return "🟢 ضغط شراء خفيف"
    if imb < 0.40:   return "🔴 ضغط بيع قوي"
    if imb < 0.48:   return "🔴 ضغط بيع خفيف"
    return "⚪ متوازن"


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
                       order_size_usd: float = 10_000,
                       is_futures: bool = False) -> "LiquidityProfile":
        """يحلل سيولة العملة — OKX أولاً ثم Bybit ثم CoinGecko.
        is_futures=True → يجلب Futures/SWAP Order Book (سيولة أدق)
        """
        # فحص الـ cache
        if symbol in self._cache:
            profile, ts = self._cache[symbol]
            if time.time() - ts < self._cache_ttl:
                return profile

        import aiohttp

        # ── 1. OKX Public API (أفضل مصدر متاح من Railway) ────
        try:
            if self.session:
                # إصلاح S1: Futures → SWAP Order Book (سيولة أدق)
                okx_sym = (f"{symbol.upper()}-USDT-SWAP"
                           if is_futures
                           else f"{symbol.upper()}-USDT")
                url = f"https://www.okx.com/api/v5/market/books?instId={okx_sym}&sz=50"
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        data  = await r.json()
                        books = (data.get("data") or [{}])[0]
                        bids  = books.get("bids", [])
                        asks  = books.get("asks", [])
                        if bids or asks:
                            walls   = self._compute_walls_from_levels(symbol, bids, asks)
                            profile = self._build_profile_from_walls(
                                symbol, walls, bids, asks, order_size_usd)
                            profile.source = "okx"
                            self._cache[symbol] = (profile, time.time())
                            logger.info(f"✅ Liquidity OKX ({symbol})")
                            return profile
        except Exception as e:
            logger.debug(f"OKX analyze ({symbol}): {e}")

        # ── 2. Bybit Public API ────────────────────────────────
        try:
            if self.session:
                url = (f"https://api.bybit.com/v5/market/orderbook"
                       f"?category=spot&symbol={symbol.upper()}USDT&limit=50")
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        res  = data.get("result", {})
                        bids = res.get("b", [])
                        asks = res.get("a", [])
                        if bids or asks:
                            walls   = self._compute_walls_from_levels(symbol, bids, asks)
                            profile = self._build_profile_from_walls(
                                symbol, walls, bids, asks, order_size_usd)
                            profile.source = "bybit"
                            self._cache[symbol] = (profile, time.time())
                            logger.info(f"✅ Liquidity Bybit ({symbol})")
                            return profile
        except Exception as e:
            logger.debug(f"Bybit analyze ({symbol}): {e}")

        # ── 3. Binance (محجوب على Railway غالباً) ─────────────
        for ep in ["https://api.binance.com", "https://api1.binance.com"]:
            try:
                url = f"{ep}/api/v3/depth?symbol={symbol.upper()}USDT&limit=50"
                if self.session:
                    async with self.session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            data    = await r.json()
                            profile = self._compute_profile(symbol, data, order_size_usd)
                            self._cache[symbol] = (profile, time.time())
                            return profile
            except Exception as e:
                logger.debug(f"Binance {ep} ({symbol}): {e}")

        # ── 4. CoinGecko — حجم تداول تقديري ──────────────────
        try:
            from core.data_layer import _cg_id, _fetch, _H_CG
            cg = _cg_id(symbol)
            url = f"https://api.coingecko.com/api/v3/coins/{cg}"
            if self.session:
                async with self.session.get(
                    url, headers=_H_CG,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status == 200:
                        coin = await r.json()
                        md   = coin.get("market_data", {})
                        vol  = float(md.get("total_volume", {}).get("usd", 0))
                        price= float(md.get("current_price", {}).get("usd", 0))
                        if vol > 0 and price > 0:
                            profile = self._estimate_profile_from_volume(
                                symbol, vol, price, order_size_usd)
                            profile.source = "coingecko"
                            self._cache[symbol] = (profile, time.time())
                            return profile
        except Exception as e:
            logger.debug(f"CoinGecko analyze ({symbol}): {e}")

        return self._fallback_profile(symbol)


    async def detect_walls(self, symbol: str,
                            depth_limit: int = 100) -> OrderFlowSignal:
        """يكشف جدران الشراء والبيع — OKX أولاً ثم Bybit ثم Binance."""

        # ── 1. OKX (الأفضل من Railway) ──────────────────────────
        try:
            if self.session:
                url = f"https://www.okx.com/api/v5/market/books?instId={symbol.upper()}-USDT&sz={depth_limit}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        books = (data.get("data") or [{}])[0]
                        bids  = books.get("bids", [])
                        asks  = books.get("asks", [])
                        if bids or asks:
                            walls = self._compute_walls_from_levels(symbol, bids, asks)
                            logger.info(f"✅ detect_walls OKX ({symbol})")
                            return walls
        except Exception as e:
            logger.debug(f"detect_walls OKX ({symbol}): {e}")

        # ── 2. Bybit ────────────────────────────────────────────
        try:
            if self.session:
                url = f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={symbol.upper()}USDT&limit={depth_limit}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        res  = data.get("result", {})
                        bids = res.get("b", [])
                        asks = res.get("a", [])
                        if bids or asks:
                            walls = self._compute_walls_from_levels(symbol, bids, asks)
                            logger.info(f"✅ detect_walls Bybit ({symbol})")
                            return walls
        except Exception as e:
            logger.debug(f"detect_walls Bybit ({symbol}): {e}")

        # ── 3. Binance (قد يكون محجوباً على Railway) ───────────
        BN_ENDPOINTS = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
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
                logger.debug(f"detect_walls Binance {ep} ({symbol}): {e}")
                continue

        # fallback: OKX Public API (لا يحتاج API key)
        try:
            if self.session:
                # إصلاح S1: Futures → SWAP Order Book (سيولة أدق)
                okx_sym = (f"{symbol.upper()}-USDT-SWAP"
                           if is_futures
                           else f"{symbol.upper()}-USDT")
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
                # إصلاح S1: Futures → SWAP Order Book (سيولة أدق)
                okx_sym = (f"{symbol.upper()}-USDT-SWAP"
                           if is_futures
                           else f"{symbol.upper()}-USDT")
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
        """يحسب الجدران من Order Book — threshold ديناميكي."""
        bids_raw = book.get("bids", [])
        asks_raw = book.get("asks", [])

        # حساب threshold ديناميكي بناءً على متوسط حجم الأوامر
        def _avg_val(levels):
            vals = []
            for lvl in levels[:20]:
                try:
                    p, q = float(lvl[0]), float(lvl[1])
                    vals.append(p * q)
                except: pass
            return sum(vals) / len(vals) if vals else 100_000

        avg_bid = _avg_val(bids_raw)
        avg_ask = _avg_val(asks_raw)
        # threshold = 3× المتوسط (جدار حقيقي يفوق المتوسط بكثير)
        threshold = max(min((avg_bid + avg_ask) / 2 * 3, 5_000_000), 50_000)

        buy_walls  = []
        sell_walls = []

        for price_str, qty_str in bids_raw:
            try:
                p   = float(price_str)
                q   = float(qty_str)
                val = p * q
                if val >= threshold:
                    buy_walls.append({"price": p, "value_usd": val})
            except (ValueError, TypeError):
                continue

        for price_str, qty_str in asks_raw:
            try:
                p   = float(price_str)
                q   = float(qty_str)
                val = p * q
                if val >= threshold:
                    sell_walls.append({"price": p, "value_usd": val})
            except (ValueError, TypeError):
                continue

        # إصلاح #627/#919: fallback فقط إذا best bid/ask >= $10K
        if not buy_walls and bids_raw:
            try:
                p, q = float(bids_raw[0][0]), float(bids_raw[0][1])
                val = p * q
                if val >= 10_000:  # لا نُعرِض جدراناً أقل من $10K
                    buy_walls = [{"price": p, "value_usd": val, "is_best": True}]
            except: pass
        if not sell_walls and asks_raw:
            try:
                p, q = float(asks_raw[0][0]), float(asks_raw[0][1])
                val = p * q
                if val >= 10_000:
                    sell_walls = [{"price": p, "value_usd": val, "is_best": True}]
            except: pass

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
        # إصلاح #270: slippage واقعي مع حد أدنى من الـ spread
        raw_slip  = abs(avg_price - mid_price) / mid_price * 100 if mid_price > 0 else 0.1
        # الحد الأدنى = نصف الـ spread (لا يمكن أن يكون أقل)
        min_slip  = spread * 0.5 if spread > 0 else 0.005
        slippage  = max(raw_slip, min_slip, 0.005)  # لا أقل من 0.005%
        slippage  = min(slippage, 2.0)              # لا أكثر من 2%
        # تقريب لـ 3 أرقام عشرية للعرض الواضح
        slippage  = round(slippage, 3)

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
            spread_pct=round(spread, 6),
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
            # U6: حكم مُحسَّن يُوضّح سبب "غير موصى"
            (f"الحكم: ✅ قابل للتداول"
             if profile.is_tradeable
             else f"الحكم: {'⚠️ بيانات غير موثوقة' if any('تقديرية' in w for w in profile.warnings) else '⛔ غير موصى بالتداول'}"),
            "",
            "📊 *Order Book*",
            f"• Spread: {profile.spread_pct:.5f}%",
        ]

        if profile.bid_depth_usd > 0:
            lines += [
                f"• عمق الشراء: ${profile.bid_depth_usd:,.0f}",
                f"• عمق البيع:  ${profile.ask_depth_usd:,.0f}",
                f"• الاختلال:   {profile.imbalance:.0%} "
                f"({_imbalance_label(profile.imbalance)})",
            ]

        lines.append(f"• Slippage متوقع: {profile.estimated_slippage_pct:.3f}%")

        # أكبر أوامر الشراء والبيع (من walls إذا متاحة)
        if walls is not None and not isinstance(walls, Exception) and not isinstance(walls, bool):
            buy_walls  = getattr(walls, "buy_walls",  []) or []
            sell_walls = getattr(walls, "sell_walls", []) or []
            # عرض أعلى/أدنى أمر (ملاحظة #26)
            lines += ["", "📋 *جدران الأوامر*"]
            if buy_walls:
                top_bid   = buy_walls[0]
                bid_count = len(buy_walls)
                lines.append(f"• أعلى أمر شراء (Bid): ${top_bid['price']:,.2f} — ${top_bid['value_usd']/1e6:.2f}M | {bid_count} أمر")
            else:
                lines.append("• أعلى أمر شراء (Bid): لا جدران كبيرة — السوق سائل")
            if sell_walls:
                top_ask   = sell_walls[0]
                ask_count = len(sell_walls)
                lines.append(f"• أدنى أمر بيع (Ask):  ${top_ask['price']:,.2f} — ${top_ask['value_usd']/1e6:.2f}M | {ask_count} أمر")
            else:
                lines.append("• أدنى أمر بيع (Ask):  لا جدران كبيرة — السوق سائل")

            # إصلاح #136/#196: جدران حقيقية فقط + ضغط من imbalance
            bw_real  = getattr(walls, "buy_walls",  []) or []
            sw_real  = getattr(walls, "sell_walls", []) or []
            real_sup = bw_real[0]["price"] if bw_real else 0
            real_res = sw_real[0]["price"] if sw_real else 0
            # إصلاح #196: الضغط من imbalance (أدق من net_pressure)
            _imb   = getattr(profile, "imbalance", 0.5)
            net_ar = _imbalance_label(_imb)
            if real_sup > 0 or real_res > 0:
                lines += ["", "🧱 *مستويات الدعم والمقاومة*"]
                if real_sup > 0:
                    # إصلاح #198: أرقام عشرية حسب قيمة السعر
                    _d = 2 if real_sup >= 100 else 4 if real_sup >= 1 else 6
                    lines.append(f"• دعم (جدار شراء):   ${real_sup:,.{_d}f}")
                if real_res > 0:
                    _d = 2 if real_res >= 100 else 4 if real_res >= 1 else 6
                    lines.append(f"• مقاومة (جدار بيع): ${real_res:,.{_d}f}")
                lines.append(f"• الضغط: {net_ar}")
        else:
            lines += ["", "📋 *جدران السوق:* غير متاحة"]

        # إصلاح #150/#161: مصدر واحد فقط
        if profile.source == "coingecko":
            lines += [
                "",
                "⚠️ *ملاحظة:* بيانات تقديرية — ليست Order Book حقيقي",
            ]
        elif profile.source in ("okx", "bybit"):
            lines += ["", f"📡 المصدر: Order Book حقيقي من {profile.source.upper()}"]
        elif profile.source == "fallback":
            lines += ["", "⚠️ المصدر: بيانات افتراضية"]
        else:
            lines += ["", f"📡 المصدر: {source_label}"]
        return "\n".join(lines)

    # حدود دنيا للجدران حسب العملة (#195)
    _WALL_MIN_USD = {
        "BTC":  150_000,   # $150K لـ BTC
        "ETH":   50_000,   # $50K لـ ETH
        "BNB":   20_000,
        "SOL":   15_000,
        "XRP":   10_000,
    }
    _WALL_MIN_DEFAULT = 5_000   # $5K للعملات الأخرى

    def _compute_walls_from_levels(self, symbol: str,
                                   bids: list, asks: list) -> "OrderFlowSignal":
        """يحسب الجدران من bids/asks مُهيَّكلة [[price, size], ...]
        إصلاح #195: حد أدنى مطلق حسب العملة"""
        try:
            buy_walls, sell_walls = [], []
            all_sizes = [float(b[0])*float(b[1]) for b in bids[:20]] + \
                        [float(a[0])*float(a[1]) for a in asks[:20]]
            avg = (sum(all_sizes)/len(all_sizes)) if all_sizes else 1
            # إصلاح #195: الأكبر بين avg×5 والحد الأدنى المطلق
            min_wall = self._WALL_MIN_USD.get(symbol.upper(), self._WALL_MIN_DEFAULT)
            threshold = max(avg * 5.0, min_wall)
            for b in bids[:20]:
                p, s = float(b[0]), float(b[1])
                if p * s > threshold:
                    buy_walls.append({"price": p, "value_usd": p*s, "size": s})
            for a in asks[:20]:
                p, s = float(a[0]), float(a[1])
                if p * s > threshold:
                    sell_walls.append({"price": p, "value_usd": p*s, "size": s})
            buy_walls.sort(key=lambda x: x["value_usd"], reverse=True)
            sell_walls.sort(key=lambda x: x["value_usd"], reverse=True)
            total = sum(w["value_usd"] for w in buy_walls) + sum(w["value_usd"] for w in sell_walls)
            net   = (sum(w["value_usd"] for w in buy_walls) - sum(w["value_usd"] for w in sell_walls)) / max(total, 1)
            return OrderFlowSignal(
                symbol=symbol,
                buy_walls=buy_walls[:5], sell_walls=sell_walls[:5],
                net_pressure=round(net, 3),
                support_level=buy_walls[0]["price"] if buy_walls else 0,
                resistance_level=sell_walls[0]["price"] if sell_walls else 0,
            )
        except Exception as e:
            logger.warning(f"_compute_walls_from_levels ({symbol}): {e}")
            return OrderFlowSignal(symbol, [], [], 0, 0, 0)

    def _build_profile_from_walls(self, symbol: str, walls,
                                   bids: list, asks: list,
                                   order_size_usd: float) -> "LiquidityProfile":
        """يبني LiquidityProfile من bids/asks حقيقي."""
        try:
            total_bid  = sum(float(b[0]) * float(b[1]) for b in bids[:20])
            total_ask  = sum(float(a[0]) * float(a[1]) for a in asks[:20])
            total      = total_bid + total_ask
            total_dep  = total_bid + total_ask
            imbalance  = total_bid / max(total_dep, 1)  # 0=كل بيع, 1=كل شراء
            best_bid   = float(bids[0][0]) if bids else 0
            best_ask   = float(asks[0][0]) if asks else 0
            mid_price  = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            spread     = abs(best_ask - best_bid) / max(best_bid, 0.0001) * 100
            # إصلاح #151: slippage من أفضل ask وليس من total_bid
            # للشراء: نقدّر التأثير على أفضل سعر ask
            best_ask_v = float(asks[0][0]) if asks else 0
            if best_ask_v > 0 and total_ask > 0:
                # نسبة الأمر من إجمالي عمق البيع في أول 5 مستويات
                top5_ask = sum(float(a[0])*float(a[1]) for a in asks[:5])
                slippage = (order_size_usd / max(top5_ask, order_size_usd)) * spread * 0.5
                # إصلاح #303: حد أدنى 0.005% (موحد مع الدالة الرئيسية)
                slippage = max(0.005, min(slippage, 2.0))
            else:
                slippage = max(spread * 0.5, 0.005) if spread > 0 else 0.05
            score      = min(1.0, total / 1e6 * 0.5 + (1 - min(spread, 1)) * 0.5)
            warnings_  = []
            if spread > 0.5:
                warnings_.append(f"spread مرتفع: {spread:.2f}%")
            return LiquidityProfile(
                symbol=symbol,
                bid_price=best_bid, ask_price=best_ask, mid_price=mid_price,
                spread_pct=round(spread, 6),
                bid_depth_usd=round(total_bid, 0),
                ask_depth_usd=round(total_ask, 0),
                imbalance=round(imbalance, 3),
                estimated_slippage_pct=round(slippage, 4),
                liquidity_score=round(score, 3),
                pressure=("شراء" if imbalance > 0.55 else "بيع" if imbalance < 0.45 else "neutral"),
                # V3 (#1216/#1221): إضافة حد عمق أدنى $25K للحكم "قابل للتداول"
                # V3 إصلاح (#1319/#1325): min(bid,ask) >= $10K
                is_tradeable=(score > 0.3 and min(total_bid, total_ask) >= 10_000),
                warnings=warnings_,
                source="exchange",
            )
        except Exception as e:
            logger.warning(f"_build_profile_from_walls ({symbol}): {e}")
            return self._fallback_profile(symbol)

    
    def _estimate_profile_from_volume(self, symbol: str,
                                       vol: float, price: float,
                                       order_size_usd: float) -> "LiquidityProfile":
        """يُقدِّر السيولة من حجم التداول."""
        bid_depth = vol * 0.05
        ask_depth = vol * 0.05
        score     = min(1.0, vol / 1e8)
        slippage  = order_size_usd / max(bid_depth, 1) * 100
        return LiquidityProfile(
            symbol=symbol,
            bid_price=price * 0.999, ask_price=price * 1.001, mid_price=price,
            spread_pct=0.05,
            bid_depth_usd=round(bid_depth, 0), ask_depth_usd=round(ask_depth, 0),
            # إصلاح U7 (#1132/#1186): imbalance=0.5 (متوازن) بدلاً من 0.0
            # لأن 0.0 يُفسَّر خطأً كـ "ضغط بيع قوي" بينما البيانات غير معروفة فعلياً
            imbalance=0.5, estimated_slippage_pct=round(slippage, 4),
            liquidity_score=round(score, 3), pressure=0.0,
            # إصلاح U6 (#1126/#1133): بيانات تقديرية → غير موصى بالتداول
            is_tradeable=False,
            warnings=["⚠️ بيانات تقديرية — ليست Order Book حقيقي"],
            source="coingecko",
        )

    
    def _fallback_profile(self, symbol: str) -> "LiquidityProfile":
        """بروفايل افتراضي."""
        # إصلاح V3c (#1324/#1333): imbalance=0.5 وis_tradeable=False للـ fallback
        return LiquidityProfile(
            symbol=symbol,
            bid_price=0, ask_price=0, mid_price=0, spread_pct=0.1,
            bid_depth_usd=0, ask_depth_usd=0, imbalance=0.5,
            estimated_slippage_pct=1.0, liquidity_score=0.5,
            pressure=0.0, is_tradeable=False,
            warnings=["⚠️ بيانات غير متاحة"], source="fallback",
        )



# Singleton
microstructure_layer = MicrostructureLayer()

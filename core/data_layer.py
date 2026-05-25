"""
📡 رائد — Data Layer (الطبقة ١)
يجمع البيانات من CoinGecko · Binance · DeFiLlama · CryptoPanic · Etherscan
لا اعتماد على مصدر واحد — fallback تلقائي.
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Any
import aiohttp

from core.data_validator import validator, ValidationStatus

logger = logging.getLogger(__name__)

# ─── Rate Limits (مجاني بالكامل) ──────────────────────────────────────────────
COINGECKO_RPM   = 10     # Free tier
BINANCE_RPM     = 1200   # Public API
DEFILLAMA_RPM   = 60
CRYPTOPANIC_RPM = 60
ETHERSCAN_RPM   = 300    # 5/second

# ─── TTL كاش (ثانية) ───────────────────────────────────────────────────────────
CACHE_TTL = {
    "price":   30,
    "ohlcv":   60,
    "news":    300,
    "onchain": 600,
    "fear":    3600,
}

_cache: Dict[str, Dict] = {}  # key → {data, ts}


def _cached(key: str, ttl_key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL[ttl_key]:
        return entry["data"]
    return None


def _store(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "ts": time.time()}


class DataLayer:
    """
    واجهة موحدة لجميع مصادر البيانات.
    كل method تُحاول المصدر الرئيسي أولاً ثم الـ fallback.
    """

    def __init__(self, session: aiohttp.ClientSession,
                 cryptopanic_key: str = "", etherscan_key: str = ""):
        self.session         = session
        self.cryptopanic_key = cryptopanic_key
        self.etherscan_key   = etherscan_key

    # ═══════════════════════════════════════════════════════════
    # 1. السعر الحي — Binance أولاً ← CoinGecko fallback
    # ═══════════════════════════════════════════════════════════
    async def get_price(self, symbol: str) -> Optional[Dict]:
        """يُعيد {symbol, price, change_24h, volume_24h, market_cap, source}"""
        key = f"price:{symbol}"
        if cached := _cached(key, "price"):
            return cached

        # ── Binance ──
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol.upper()}USDT"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    price = float(d["lastPrice"])
                    result = validator.validate_price(symbol, price)
                    if result.is_usable:
                        data = {
                            "symbol":     symbol.upper(),
                            "price":      price,
                            "change_24h": float(d["priceChangePercent"]),
                            "volume_24h": float(d["quoteVolume"]),
                            "high_24h":   float(d["highPrice"]),
                            "low_24h":    float(d["lowPrice"]),
                            "source":     "binance",
                        }
                        _store(key, data)
                        return data
        except Exception as e:
            logger.warning(f"Binance price fail ({symbol}): {e}")

        # ── CoinGecko fallback ──
        try:
            cg_id = _symbol_to_coingecko(symbol)
            url   = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    d    = await r.json()
                    coin = d.get(cg_id, {})
                    if coin:
                        price  = coin["usd"]
                        result = validator.validate_price(symbol, price)
                        if result.is_usable:
                            data = {
                                "symbol":     symbol.upper(),
                                "price":      price,
                                "change_24h": coin.get("usd_24h_change", 0),
                                "volume_24h": coin.get("usd_24h_vol", 0),
                                "market_cap": coin.get("usd_market_cap", 0),
                                "source":     "coingecko",
                            }
                            _store(key, data)
                            return data
        except Exception as e:
            logger.warning(f"CoinGecko price fail ({symbol}): {e}")

        return None

    # ═══════════════════════════════════════════════════════════
    # 2. بيانات OHLCV — Binance
    # ═══════════════════════════════════════════════════════════
    async def get_ohlcv(self, symbol: str, interval: str = "1d",
                        limit: int = 365) -> List[Dict]:
        """يُعيد قائمة شموع OHLCV مُتحقق منها"""
        key = f"ohlcv:{symbol}:{interval}:{limit}"
        if cached := _cached(key, "ohlcv"):
            return cached

        try:
            url = (f"https://api.binance.com/api/v3/klines"
                   f"?symbol={symbol.upper()}USDT&interval={interval}&limit={limit}")
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    raw    = await r.json()
                    candles = []
                    for c in raw:
                        candle = {
                            "timestamp": c[0] / 1000,
                            "open":  float(c[1]),
                            "high":  float(c[2]),
                            "low":   float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5]),
                        }
                        res = validator.validate_ohlcv(candle)
                        if res.is_usable:
                            candles.append(res.cleaned)

                    _store(key, candles)
                    return candles
        except Exception as e:
            logger.error(f"OHLCV fail ({symbol}): {e}")
        return []

    # ═══════════════════════════════════════════════════════════
    # 3. بيانات تاريخية (٣ سنوات) — CoinGecko
    # ═══════════════════════════════════════════════════════════
    async def get_historical_prices(self, symbol: str, days: int = 365) -> List[Dict]:
        """يُعيد بيانات تاريخية للـ Backtest"""
        key = f"hist:{symbol}:{days}"
        if cached := _cached(key, "ohlcv"):
            return cached

        cg_id = _symbol_to_coingecko(symbol)
        results = []

        # CoinGecko يدعم ٣٦٥ يوم في طلب واحد → نجمع ٣ سنوات بـ ٣ طلبات
        chunks = [(days, 0)]
        if days > 365:
            chunks = [(365, 0), (365, 365), (days - 730, 730)]

        for chunk_days, offset_days in chunks:
            try:
                url = (f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
                       f"?vs_currency=usd&days={chunk_days}&interval=daily")
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        d      = await r.json()
                        prices = d.get("prices", [])
                        vols   = d.get("total_volumes", [])
                        for i, (ts, price) in enumerate(prices):
                            results.append({
                                "timestamp": ts / 1000,
                                "price":     price,
                                "volume":    vols[i][1] if i < len(vols) else 0,
                            })
                await asyncio.sleep(1.5)   # احترام rate limit
            except Exception as e:
                logger.warning(f"Historical chunk fail ({symbol}): {e}")

        _store(key, results)
        return results

    # ═══════════════════════════════════════════════════════════
    # 4. الأخبار — CryptoPanic
    # ═══════════════════════════════════════════════════════════
    async def get_news(self, currencies: str = "BTC,ETH",
                       limit: int = 20) -> List[Dict]:
        key = f"news:{currencies}"
        if cached := _cached(key, "news"):
            return cached

        try:
            params = {
                "public":     "true",
                "currencies": currencies,
                "filter":     "important",
                "kind":       "news",
            }
            if self.cryptopanic_key:
                params["auth_token"] = self.cryptopanic_key

            url = "https://cryptopanic.com/api/v1/posts/"
            async with self.session.get(url, params=params,
                                         timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data   = await r.json()
                    raw    = data.get("results", [])[:limit]
                    valid, _ = validator.validate_batch(
                        raw, validator.validate_news_item)
                    _store(key, valid)
                    return valid
        except Exception as e:
            logger.warning(f"CryptoPanic fail: {e}")

        # Fallback: RSS CoinTelegraph
        return await self._get_rss_news()

    async def _get_rss_news(self) -> List[Dict]:
        """Fallback: RSS بدون مفتاح API"""
        try:
            url = "https://cointelegraph.com/rss"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    text  = await r.text()
                    items = _parse_rss(text)
                    return items[:15]
        except Exception as e:
            logger.warning(f"RSS fallback fail: {e}")
        return []

    # ═══════════════════════════════════════════════════════════
    # 5. On-Chain — DeFiLlama + Etherscan
    # ═══════════════════════════════════════════════════════════
    async def get_onchain(self, protocol: str = "all") -> Dict:
        key = f"onchain:{protocol}"
        if cached := _cached(key, "onchain"):
            return cached

        result = {"tvl": 0, "volume24h": 0, "protocols": [], "source": "defillama"}

        # ── TVL من DeFiLlama ──
        try:
            url = "https://api.llama.fi/charts"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    if data:
                        result["tvl"] = data[-1].get("totalLiquidityUSD", 0)
        except Exception as e:
            logger.warning(f"DeFiLlama TVL fail: {e}")

        # ── أفضل البروتوكولات ──
        try:
            url = "https://api.llama.fi/protocols"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    protos = await r.json()
                    top    = sorted(protos, key=lambda x: x.get("tvl", 0), reverse=True)[:10]
                    result["protocols"] = [
                        {"name": p["name"], "tvl": p["tvl"], "chain": p.get("chain", "multi")}
                        for p in top
                    ]
        except Exception as e:
            logger.warning(f"DeFiLlama protocols fail: {e}")

        # ── Ethereum نشاط المحافظ الكبيرة (Etherscan) ──
        if self.etherscan_key:
            try:
                url = (f"https://api.etherscan.io/api?module=stats"
                       f"&action=ethsupply&apikey={self.etherscan_key}")
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        d = await r.json()
                        result["eth_supply"] = int(d.get("result", 0)) / 1e18
            except Exception as e:
                logger.warning(f"Etherscan fail: {e}")

        vres = validator.validate_onchain(result)
        if vres.is_usable:
            _store(key, result)
        return result

    # ═══════════════════════════════════════════════════════════
    # 6. Fear & Greed Index
    # ═══════════════════════════════════════════════════════════
    async def get_fear_greed(self) -> Dict:
        key = "fear_greed"
        if cached := _cached(key, "fear"):
            return cached
        try:
            url = "https://api.alternative.me/fng/?limit=1"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d    = await r.json()
                    item = d["data"][0]
                    data = {
                        "value":       int(item["value"]),
                        "label":       item["value_classification"],
                        "label_ar":    _fear_ar(int(item["value"])),
                        "updated_ago": int(item["time_until_update"]),
                    }
                    _store(key, data)
                    return data
        except Exception as e:
            logger.warning(f"Fear & Greed fail: {e}")
        return {"value": 50, "label": "Neutral", "label_ar": "محايد"}

    # ═══════════════════════════════════════════════════════════
    # 7. أسعار متعددة دفعة واحدة
    # ═══════════════════════════════════════════════════════════
    async def get_top_coins(self, limit: int = 100) -> List[Dict]:
        """Top N عملة بالقيمة السوقية — للمسح الشامل"""
        key = f"top:{limit}"
        if cached := _cached(key, "news"):   # TTL 5 دقائق
            return cached
        try:
            url = (f"https://api.coingecko.com/api/v3/coins/markets"
                   f"?vs_currency=usd&order=market_cap_desc"
                   f"&per_page={limit}&page=1&sparkline=false"
                   f"&price_change_percentage=24h,7d")
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    # فلتر stablecoins
                    filtered = [
                        c for c in data
                        if c.get("symbol", "").upper() not in
                        {"USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX","LUSD"}
                    ]
                    _store(key, filtered)
                    return filtered
        except Exception as e:
            logger.error(f"Top coins fail: {e}")
        return []


# ─── Helpers ───────────────────────────────────────────────────────────────────
_CG_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana",  "ADA": "cardano",  "XRP": "ripple",
    "DOT": "polkadot","DOGE":"dogecoin", "AVAX":"avalanche-2",
    "MATIC":"matic-network","LINK":"chainlink","UNI":"uniswap",
    "LTC": "litecoin","ATOM":"cosmos",   "NEAR":"near",
}

def _symbol_to_coingecko(symbol: str) -> str:
    return _CG_MAP.get(symbol.upper(), symbol.lower())


def _fear_ar(value: int) -> str:
    if value >= 75: return "جشع شديد"
    if value >= 55: return "جشع"
    if value >= 45: return "محايد"
    if value >= 25: return "خوف"
    return "خوف شديد"


def _parse_rss(xml: str) -> List[Dict]:
    """محلّل RSS بسيط بدون مكتبات خارجية"""
    import re
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        title = re.search(r"<title><!\[CDATA\[(.*?)\]\]>|<title>(.*?)</title>", block)
        link  = re.search(r"<link>(.*?)</link>", block)
        if title:
            t = (title.group(1) or title.group(2) or "").strip()
            l = link.group(1).strip() if link else ""
            items.append({"title": t, "url": l,
                          "source": {"title": "CoinTelegraph"},
                          "created_at": time.time()})
    return items

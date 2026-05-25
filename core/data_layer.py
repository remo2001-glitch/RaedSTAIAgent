"""
📡 رائد — Data Layer (الطبقة ١) — النسخة المحسّنة
يجمع البيانات من CoinGecko · Binance · DeFiLlama · CryptoPanic · Etherscan
retry تلقائي · fallbacks متعددة · headers محسّنة لتجاوز 403
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
import aiohttp

from core.data_validator import validator, ValidationStatus

logger = logging.getLogger(__name__)

# ─── TTL كاش (ثانية) ──────────────────────────────────────────
CACHE_TTL = {
    "price":   60,
    "ohlcv":   300,
    "news":    300,
    "onchain": 600,
    "fear":    3600,
    "hist":    3600,
}

_cache: Dict[str, Dict] = {}

def _cached(key: str, ttl_key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL.get(ttl_key, 300):
        return entry["data"]
    return None

def _store(key: str, data: Any, ttl_key: str = "price") -> None:
    _cache[key] = {"data": data, "ts": time.time()}

# ─── Headers مخصصة لكل API ────────────────────────────────────
HEADERS_COINGECKO = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.coingecko.com/",
    "Origin":          "https://www.coingecko.com",
}

HEADERS_BINANCE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept":     "application/json",
}

HEADERS_DEFILLAMA = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Accept":     "application/json",
}

# ─── CoinGecko IDs ────────────────────────────────────────────
_CG_MAP = {
    "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin",
    "SOL":"solana","ADA":"cardano","XRP":"ripple",
    "DOT":"polkadot","DOGE":"dogecoin","AVAX":"avalanche-2",
    "MATIC":"matic-network","LINK":"chainlink","UNI":"uniswap",
    "LTC":"litecoin","ATOM":"cosmos","NEAR":"near",
    "ARB":"arbitrum","OP":"optimism","APT":"aptos",
    "TRX":"tron","SHIB":"shiba-inu",
}

def _cg_id(symbol: str) -> str:
    return _CG_MAP.get(symbol.upper(), symbol.lower())


async def _get_with_retry(session: aiohttp.ClientSession, url: str,
                           headers: Dict = None, params: Dict = None,
                           retries: int = 3, delay: float = 2.0) -> Optional[Any]:
    """يُجرّب الطلب مع retry تلقائي عند الفشل."""
    for attempt in range(retries):
        try:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 200:
                    return await r.json()
                elif r.status == 429:   # Rate limit
                    wait = delay * (attempt + 2)
                    logger.warning(f"Rate limit ({url[:50]}) — انتظار {wait}ث")
                    await asyncio.sleep(wait)
                elif r.status in (403, 401):
                    logger.warning(f"403/401 ({url[:60]}) attempt {attempt+1}")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"HTTP {r.status} ({url[:50]})")
                    await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout ({url[:50]}) attempt {attempt+1}")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.warning(f"Request error ({url[:50]}): {e}")
            await asyncio.sleep(delay)
    return None


class DataLayer:

    def __init__(self, session: aiohttp.ClientSession,
                 cryptopanic_key: str = "", etherscan_key: str = ""):
        self.session         = session
        self.cryptopanic_key = cryptopanic_key
        self.etherscan_key   = etherscan_key

    # ═══════════════════════════════════════════════════════════
    # 1. السعر الحي
    # ═══════════════════════════════════════════════════════════
    async def get_price(self, symbol: str) -> Optional[Dict]:
        key = f"price:{symbol}"
        if cached := _cached(key, "price"):
            return cached

        # ── Binance أولاً ──
        result = await self._price_binance(symbol)
        if result:
            _store(key, result)
            return result

        # ── CoinGecko fallback ──
        result = await self._price_coingecko(symbol)
        if result:
            _store(key, result)
            return result

        logger.error(f"get_price فشل كلياً لـ {symbol}")
        return None

    async def _price_binance(self, symbol: str) -> Optional[Dict]:
        url  = f"https://api.binance.com/api/v3/ticker/24hr"
        data = await _get_with_retry(
            self.session, url,
            headers=HEADERS_BINANCE,
            params={"symbol": f"{symbol.upper()}USDT"},
        )
        if data and "lastPrice" in data:
            price = float(data["lastPrice"])
            if validator.validate_price(symbol, price).is_usable:
                return {
                    "symbol":     symbol.upper(),
                    "price":      price,
                    "change_24h": float(data.get("priceChangePercent", 0)),
                    "volume_24h": float(data.get("quoteVolume", 0)),
                    "high_24h":   float(data.get("highPrice", 0)),
                    "low_24h":    float(data.get("lowPrice", 0)),
                    "source":     "binance",
                }
        return None

    async def _price_coingecko(self, symbol: str) -> Optional[Dict]:
        cg  = _cg_id(symbol)
        url = "https://api.coingecko.com/api/v3/simple/price"
        data = await _get_with_retry(
            self.session, url,
            headers=HEADERS_COINGECKO,
            params={
                "ids": cg,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
                "include_market_cap": "true",
            },
        )
        if data and cg in data:
            coin  = data[cg]
            price = coin.get("usd", 0)
            if price and validator.validate_price(symbol, price).is_usable:
                return {
                    "symbol":     symbol.upper(),
                    "price":      price,
                    "change_24h": coin.get("usd_24h_change", 0),
                    "volume_24h": coin.get("usd_24h_vol", 0),
                    "market_cap": coin.get("usd_market_cap", 0),
                    "source":     "coingecko",
                }
        return None

    # ═══════════════════════════════════════════════════════════
    # 2. بيانات OHLCV
    # ═══════════════════════════════════════════════════════════
    async def get_ohlcv(self, symbol: str, interval: str = "1d",
                         limit: int = 365) -> List[Dict]:
        key = f"ohlcv:{symbol}:{interval}:{limit}"
        if cached := _cached(key, "ohlcv"):
            return cached

        # ── Binance ──
        candles = await self._ohlcv_binance(symbol, interval, limit)
        if len(candles) >= 30:
            _store(key, candles, "ohlcv")
            return candles

        # ── CoinGecko fallback ──
        logger.info(f"OHLCV Binance فشل لـ {symbol} — جاري تجربة CoinGecko")
        candles = await self._ohlcv_coingecko(symbol, min(limit, 365))
        if candles:
            _store(key, candles, "ohlcv")
            return candles

        return []

    async def _ohlcv_binance(self, symbol: str, interval: str,
                               limit: int) -> List[Dict]:
        url  = "https://api.binance.com/api/v3/klines"
        data = await _get_with_retry(
            self.session, url,
            headers=HEADERS_BINANCE,
            params={"symbol": f"{symbol.upper()}USDT",
                    "interval": interval, "limit": limit},
        )
        if not data or not isinstance(data, list):
            return []

        candles = []
        for c in data:
            try:
                candle = {
                    "timestamp": c[0] / 1000,
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                }
                res = validator.validate_ohlcv(candle)
                if res.is_usable:
                    candles.append(res.cleaned)
            except Exception:
                continue
        return candles

    async def _ohlcv_coingecko(self, symbol: str, days: int) -> List[Dict]:
        cg   = _cg_id(symbol)
        url  = f"https://api.coingecko.com/api/v3/coins/{cg}/market_chart"
        data = await _get_with_retry(
            self.session, url,
            headers=HEADERS_COINGECKO,
            params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
        )
        if not data:
            return []

        prices = data.get("prices", [])
        vols   = data.get("total_volumes", [])
        candles = []

        for i in range(1, len(prices)):
            try:
                prev_p = prices[i-1][1]
                curr_p = prices[i][1]
                candle = {
                    "timestamp": prices[i][0] / 1000,
                    "open":   prev_p,
                    "high":   max(prev_p, curr_p) * 1.005,
                    "low":    min(prev_p, curr_p) * 0.995,
                    "close":  curr_p,
                    "volume": vols[i][1] if i < len(vols) else 0,
                }
                res = validator.validate_ohlcv(candle)
                if res.is_usable:
                    candles.append(res.cleaned)
            except Exception:
                continue

        logger.info(f"CoinGecko OHLCV ({symbol}): {len(candles)} شمعة")
        return candles

    # ═══════════════════════════════════════════════════════════
    # 3. بيانات تاريخية ٣ سنوات
    # ═══════════════════════════════════════════════════════════
    async def get_historical_prices(self, symbol: str,
                                     days: int = 1095) -> List[Dict]:
        key = f"hist:{symbol}:{days}"
        if cached := _cached(key, "hist"):
            return cached

        cg      = _cg_id(symbol)
        results = []
        # نجمع بـ ٣ طلبات (٣٦٥ يوم لكل طلب)
        chunks = [365, 365, min(days - 730, 365)] if days > 730 else [days]

        for chunk in chunks:
            if chunk <= 0:
                continue
            url  = f"https://api.coingecko.com/api/v3/coins/{cg}/market_chart"
            data = await _get_with_retry(
                self.session, url,
                headers=HEADERS_COINGECKO,
                params={"vs_currency": "usd",
                        "days": str(chunk), "interval": "daily"},
            )
            if data:
                prices = data.get("prices", [])
                vols   = data.get("total_volumes", [])
                for i, (ts, price) in enumerate(prices):
                    results.append({
                        "timestamp": ts / 1000,
                        "price":     price,
                        "volume":    vols[i][1] if i < len(vols) else 0,
                    })
            await asyncio.sleep(2)   # احترام rate limit

        # إزالة التكرار وترتيب
        seen = set()
        unique = []
        for r in sorted(results, key=lambda x: x["timestamp"]):
            ts = round(r["timestamp"])
            if ts not in seen:
                seen.add(ts)
                unique.append(r)

        logger.info(f"Historical ({symbol}): {len(unique)} يوم")
        if unique:
            _store(key, unique, "hist")
        return unique

    # ═══════════════════════════════════════════════════════════
    # 4. الأخبار — CryptoPanic + RSS
    # ═══════════════════════════════════════════════════════════
    async def get_news(self, currencies: str = "BTC,ETH",
                        limit: int = 20) -> List[Dict]:
        key = f"news:{currencies}"
        if cached := _cached(key, "news"):
            return cached

        items = []

        # ── CryptoPanic ──
        try:
            params = {"public": "true", "currencies": currencies,
                      "filter": "important", "kind": "news"}
            if self.cryptopanic_key:
                params["auth_token"] = self.cryptopanic_key

            data = await _get_with_retry(
                self.session,
                "https://cryptopanic.com/api/v1/posts/",
                params=params,
            )
            if data:
                for post in data.get("results", [])[:limit]:
                    items.append({
                        "title":     post.get("title", ""),
                        "url":       post.get("url", ""),
                        "source":    post.get("source", {}).get("title", ""),
                        "published": post.get("created_at", ""),
                        "votes_pos": post.get("votes", {}).get("positive", 0),
                        "votes_neg": post.get("votes", {}).get("negative", 0),
                    })
        except Exception as e:
            logger.warning(f"CryptoPanic error: {e}")

        # ── RSS fallback ──
        if len(items) < 5:
            items.extend(await self._rss_news())

        items = items[:limit]
        _store(key, items, "news")
        return items

    async def _rss_news(self) -> List[Dict]:
        import re
        sources = [
            ("CoinTelegraph", "https://cointelegraph.com/rss"),
            ("Decrypt",       "https://decrypt.co/feed"),
        ]
        results = []
        for name, url in sources:
            try:
                async with self.session.get(
                    url, headers={"User-Agent": "Mozilla/5.0",
                                   "Accept": "application/rss+xml"},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        text = await r.text()
                        for block in re.findall(r"<item>(.*?)</item>",
                                                 text, re.DOTALL)[:6]:
                            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                                           block)
                            l = re.search(r"<link>(.*?)</link>", block)
                            if t:
                                results.append({
                                    "title":     t.group(1).strip(),
                                    "url":       l.group(1).strip() if l else "",
                                    "source":    name,
                                    "published": "",
                                    "votes_pos": 0, "votes_neg": 0,
                                })
            except Exception as e:
                logger.warning(f"RSS {name}: {e}")
        return results

    # ═══════════════════════════════════════════════════════════
    # 5. On-Chain — DeFiLlama
    # ═══════════════════════════════════════════════════════════
    async def get_onchain(self, protocol: str = "all") -> Dict:
        key = f"onchain:{protocol}"
        if cached := _cached(key, "onchain"):
            return cached

        result = {"tvl": 0, "volume24h": 0, "protocols": [], "source": "defillama"}

        # TVL إجمالي
        data = await _get_with_retry(
            self.session, "https://api.llama.fi/charts",
            headers=HEADERS_DEFILLAMA,
        )
        if data and isinstance(data, list):
            result["tvl"] = data[-1].get("totalLiquidityUSD", 0)

        # أفضل البروتوكولات
        protos = await _get_with_retry(
            self.session, "https://api.llama.fi/protocols",
            headers=HEADERS_DEFILLAMA,
        )
        if protos and isinstance(protos, list):
            top = sorted(protos, key=lambda x: x.get("tvl", 0), reverse=True)[:10]
            result["protocols"] = [
                {"name": p["name"], "tvl": p.get("tvl", 0),
                 "chain": p.get("chain", "multi")}
                for p in top
            ]

        _store(key, result, "onchain")
        return result

    # ═══════════════════════════════════════════════════════════
    # 6. Fear & Greed
    # ═══════════════════════════════════════════════════════════
    async def get_fear_greed(self) -> Dict:
        if cached := _cached("fear_greed", "fear"):
            return cached
        data = await _get_with_retry(
            self.session,
            "https://api.alternative.me/fng/?limit=1",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        if data and "data" in data:
            item = data["data"][0]
            val  = int(item["value"])
            result = {
                "value":     val,
                "label":     item["value_classification"],
                "label_ar":  _fear_ar(val),
            }
            _store("fear_greed", result, "fear")
            return result
        return {"value": 50, "label": "Neutral", "label_ar": "محايد"}

    # ═══════════════════════════════════════════════════════════
    # 7. Top Coins
    # ═══════════════════════════════════════════════════════════
    async def get_top_coins(self, limit: int = 20) -> List[Dict]:
        key = f"top:{limit}"
        if cached := _cached(key, "news"):
            return cached

        data = await _get_with_retry(
            self.session,
            "https://api.coingecko.com/api/v3/coins/markets",
            headers=HEADERS_COINGECKO,
            params={
                "vs_currency": "usd",
                "order":       "market_cap_desc",
                "per_page":    str(limit),
                "page":        "1",
                "sparkline":   "false",
            },
        )
        if not data:
            return []

        stables = {"USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX","LUSD"}
        filtered = [c for c in data
                    if c.get("symbol","").upper() not in stables]
        _store(key, filtered, "news")
        return filtered


# ─── Helpers ──────────────────────────────────────────────────
def _fear_ar(value: int) -> str:
    if value >= 75: return "جشع شديد"
    if value >= 55: return "جشع"
    if value >= 45: return "محايد"
    if value >= 25: return "خوف"
    return "خوف شديد"

"""
📡 رائد — Data Layer (الطبقة ١) — النسخة المُحكمة
CoinGecko · Binance · DeFiLlama · CryptoPanic
- retry تلقائي ٣ محاولات
- fallback لكل مصدر
- لا تُعيد أبداً None — دائماً [] أو {}
- CoinGecko intervals مدعومة فقط: daily / hourly
"""

import asyncio
import json
import time
import re
import logging
from typing import Dict, List, Optional, Any
import aiohttp

from core.data_validator import validator

logger = logging.getLogger(__name__)

# ─── TTL كاش ──────────────────────────────────────────────────
CACHE_TTL = {
    "price":   30,    # 30 ثانية — سعر حديث دائماً
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

# ─── CoinGecko IDs ────────────────────────────────────────────
_CG_MAP = {
    "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin",
    "SOL":"solana","ADA":"cardano","XRP":"ripple",
    "DOT":"polkadot","DOGE":"dogecoin","AVAX":"avalanche-2",
    "MATIC":"matic-network","LINK":"chainlink","UNI":"uniswap",
    "LTC":"litecoin","ATOM":"cosmos","NEAR":"near",
    "ARB":"arbitrum","OP":"optimism","APT":"aptos",
    "TRX":"tron","SHIB":"shiba-inu","SUI":"sui",
    "TON":"the-open-network","PEPE":"pepe",
    # إضافات مهمة
    "BGB":"bitget-token","OKB":"okb","HT":"huobi-token",
    "CRO":"crypto-com-chain","FTT":"ftx-token","GT":"gate",
    "MX":"mexc-token","KCS":"kucoin-shares","WBT":"whitebit",
    "INJ":"injective-protocol","SEI":"sei-network",
    "TIA":"celestia","PYTH":"pyth-network","JTO":"jito-governance-token",
    "WIF":"dogwifcoin","BONK":"bonk","FLOKI":"floki",
    "RENDER":"render-token","FET":"fetch-ai","AGIX":"singularitynet",
    "TAO":"bittensor","WLD":"worldcoin-wld","ONDO":"ondo-finance",
    "STX":"blockstack","ICP":"internet-computer","FIL":"filecoin",
    "HBAR":"hedera-hashgraph","VET":"vechain","ALGO":"algorand",
    "EOS":"eos","XLM":"stellar","XMR":"monero","AAVE":"aave",
    "SAND":"the-sandbox","MANA":"decentraland","AXS":"axie-infinity",
    "CHZ":"chiliz","GALA":"gala","ENJ":"enjincoin",
    "RUNE":"thorchain","KAVA":"kava","ZIL":"zilliqa",
}
def _cg_id(symbol: str) -> str:
    return _CG_MAP.get(symbol.upper(), symbol.lower())

# ─── تحويل interval لـ CoinGecko ─────────────────────────────
def _cg_interval(interval: str) -> str:
    """CoinGecko يدعم: daily فقط للفترات الطويلة."""
    if interval in ("1d","1w","1M"):
        return "daily"
    return "daily"   # fallback آمن لأي interval

# ─── headers ──────────────────────────────────────────────────
_H_CG = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.coingecko.com/",
}
_H_BN = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Accept":     "application/json",
}
_H_DL = {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0", "Accept": "application/json"}
_H_GN = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


async def _fetch(session: aiohttp.ClientSession, url: str,
                  headers: Dict = None, params: Dict = None,
                  retries: int = 3, backoff: float = 3.0) -> Optional[Any]:
    """
    يُجرّب الطلب مع retry تلقائي.
    يُعيد None فقط إذا فشلت كل المحاولات — المستدعي مسؤول عن الـ fallback.
    """
    for attempt in range(retries):
        try:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                if r.status == 200:
                    ct = r.headers.get("Content-Type","")
                    if "json" in ct:
                        return await r.json()
                    text = await r.text()
                    try:
                        import json
                        return json.loads(text)
                    except Exception:
                        return text
                elif r.status == 429:
                    wait = backoff * (attempt + 2)
                    logger.warning(f"Rate limit [{url[:55]}] انتظار {wait:.0f}ث")
                    await asyncio.sleep(wait)
                elif r.status in (403, 401, 451):
                    logger.warning(f"HTTP {r.status} [{url[:55]}] محاولة {attempt+1}/{retries}")
                    await asyncio.sleep(backoff * (attempt + 1))
                else:
                    logger.warning(f"HTTP {r.status} [{url[:55]}]")
                    await asyncio.sleep(backoff)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout [{url[:55]}] محاولة {attempt+1}")
            await asyncio.sleep(backoff)
        except Exception as e:
            logger.warning(f"Error [{url[:55]}]: {e}")
            await asyncio.sleep(backoff)
    logger.error(f"فشلت كل المحاولات [{url[:55]}]")
    return None


class DataLayer:

    def __init__(self, session: aiohttp.ClientSession,
                 cryptopanic_key: str = "", etherscan_key: str = ""):
        self.session         = session
        self.cryptopanic_key = cryptopanic_key
        self.etherscan_key   = etherscan_key

    # ═══════════════════════════════════════════════════════════
    # 1. السعر الحي — يُعيد Dict أو None (مع حماية في المستدعي)
    # ═══════════════════════════════════════════════════════════
    async def get_price(self, symbol: str) -> Optional[Dict]:
        key = f"price:{symbol.upper()}"
        if cached := _cached(key, "price"):
            return cached

        # Binance
        result = await self._price_binance(symbol)
        if result:
            _store(key, result)
            return result

        # CoinGecko fallback
        await asyncio.sleep(1)
        result = await self._price_coingecko(symbol)
        if result:
            _store(key, result)
            return result

        logger.error(f"get_price فشل لـ {symbol}")
        return None

    async def _price_binance(self, symbol: str) -> Optional[Dict]:
        data = await _fetch(
            self.session,
            "https://api.binance.com/api/v3/ticker/24hr",
            headers=_H_BN,
            params={"symbol": f"{symbol.upper()}USDT"},
        )
        if not isinstance(data, dict) or "lastPrice" not in data:
            return None
        try:
            price = float(data["lastPrice"])
            if price <= 0:
                return None
            return {
                "symbol":     symbol.upper(),
                "price":      price,
                "change_24h": float(data.get("priceChangePercent", 0)),
                "volume_24h": float(data.get("quoteVolume", 0)),
                "high_24h":   float(data.get("highPrice", 0)),
                "low_24h":    float(data.get("lowPrice", 0)),
                "source":     "binance",
            }
        except (ValueError, TypeError):
            return None

    async def _price_coingecko(self, symbol: str) -> Optional[Dict]:
        cg   = _cg_id(symbol)
        data = await _fetch(
            self.session,
            "https://api.coingecko.com/api/v3/simple/price",
            headers=_H_CG,
            params={
                "ids": cg, "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol":    "true",
                "include_market_cap":  "true",
            },
        )
        if not isinstance(data, dict) or cg not in data:
            return None
        try:
            coin  = data[cg]
            price = float(coin.get("usd", 0))
            if price <= 0:
                return None
            return {
                "symbol":     symbol.upper(),
                "price":      price,
                "change_24h": float(coin.get("usd_24h_change", 0)),
                "volume_24h": float(coin.get("usd_24h_vol", 0)),
                "market_cap": float(coin.get("usd_market_cap", 0)),
                "source":     "coingecko",
            }
        except (ValueError, TypeError):
            return None

    # ═══════════════════════════════════════════════════════════
    # 2. OHLCV — يُعيد دائماً List (قد تكون فارغة لكن ليست None)
    # ═══════════════════════════════════════════════════════════
    async def get_ohlcv(self, symbol: str, interval: str = "1d",
                         limit: int = 365) -> List[Dict]:
        key = f"ohlcv:{symbol}:{interval}:{limit}"
        if cached := _cached(key, "ohlcv"):
            return cached  # دائماً List

        # Binance
        candles = await self._ohlcv_binance(symbol, interval, limit)
        if len(candles) >= 10:
            _store(key, candles, "ohlcv")
            return candles

        # CoinGecko fallback — يدعم daily فقط
        await asyncio.sleep(1)
        cg_days = min(limit, 365)
        candles = await self._ohlcv_coingecko(symbol, cg_days)
        if candles:
            _store(key, candles, "ohlcv")
            return candles

        logger.error(f"get_ohlcv فشل لـ {symbol} — يُعيد []")
        return []   # دائماً List، أبداً None

    async def _ohlcv_binance(self, symbol: str, interval: str,
                               limit: int) -> List[Dict]:
        # تحقق من صحة interval لـ Binance
        valid_intervals = {"1m","3m","5m","15m","30m","1h","2h","4h",
                           "6h","8h","12h","1d","3d","1w","1M"}
        if interval not in valid_intervals:
            interval = "1d"

        data = await _fetch(
            self.session,
            "https://api.binance.com/api/v3/klines",
            headers=_H_BN,
            params={"symbol": f"{symbol.upper()}USDT",
                    "interval": interval, "limit": str(limit)},
        )
        if not isinstance(data, list):
            return []

        candles = []
        for c in data:
            try:
                candle = {
                    "timestamp": float(c[0]) / 1000,
                    "open":      float(c[1]),
                    "high":      float(c[2]),
                    "low":       float(c[3]),
                    "close":     float(c[4]),
                    "volume":    float(c[5]),
                }
                res = validator.validate_ohlcv(candle)
                if res.is_usable:
                    candles.append(res.cleaned)
            except (ValueError, TypeError, IndexError):
                continue
        return candles

    async def _ohlcv_coingecko(self, symbol: str, days: int) -> List[Dict]:
        cg   = _cg_id(symbol)
        # CoinGecko: days<=1 → hourly, days>1 → daily تلقائياً
        data = await _fetch(
            self.session,
            f"https://api.coingecko.com/api/v3/coins/{cg}/market_chart",
            headers=_H_CG,
            params={"vs_currency": "usd",
                    "days": str(max(days, 2)),
                    "interval": "daily"},
        )
        if not isinstance(data, dict):
            return []

        prices = data.get("prices", [])
        vols   = data.get("total_volumes", [])
        if not prices:
            return []

        candles = []
        for i in range(1, len(prices)):
            try:
                prev_p = float(prices[i-1][1])
                curr_p = float(prices[i][1])
                if prev_p <= 0 or curr_p <= 0:
                    continue
                candle = {
                    "timestamp": float(prices[i][0]) / 1000,
                    "open":      prev_p,
                    "high":      max(prev_p, curr_p) * 1.005,
                    "low":       min(prev_p, curr_p) * 0.995,
                    "close":     curr_p,
                    "volume":    float(vols[i][1]) if i < len(vols) else 0.0,
                }
                res = validator.validate_ohlcv(candle)
                if res.is_usable:
                    candles.append(res.cleaned)
            except (ValueError, TypeError, IndexError):
                continue
        logger.info(f"CoinGecko OHLCV ({symbol}): {len(candles)} شمعة")
        return candles

    # ═══════════════════════════════════════════════════════════
    # 3. بيانات تاريخية ٣ سنوات — يُعيد دائماً List
    # ═══════════════════════════════════════════════════════════
    async def get_historical_prices(self, symbol: str,
                                     days: int = 1095) -> List[Dict]:
        """
        يجلب بيانات OHLCV تاريخية حقيقية للـ Backtest.
        المصدر الأساسي: Binance (open,high,low,close,volume حقيقية)
        Fallback: CoinGecko (price + volume فقط)
        """
        key = f"hist:{symbol}:{days}"
        if cached := _cached(key, "hist"):
            return cached

        # ── Binance أولاً — OHLCV حقيقي كامل ──────────────────
        results = await self._hist_binance(symbol, days)
        if len(results) >= 90:
            logger.info(f"Historical Binance ({symbol}): {len(results)} يوم OHLCV حقيقي")
            _store(key, results, "hist")
            return results

        # ── CoinGecko fallback ──────────────────────────────────
        logger.info(f"Historical: Binance فشل لـ {symbol} — CoinGecko fallback")
        results = await self._hist_coingecko(symbol, days)

        seen, unique = set(), []
        for r in sorted(results, key=lambda x: x["timestamp"]):
            ts = round(r["timestamp"])
            if ts not in seen and r.get("price", r.get("close", 0)) > 0:
                seen.add(ts)
                unique.append(r)

        logger.info(f"Historical CoinGecko ({symbol}): {len(unique)} يوم")
        if unique:
            _store(key, unique, "hist")
        return unique

    async def _hist_binance(self, symbol: str, days: int) -> List[Dict]:
        """
        Binance klines — OHLCV حقيقية كاملة.
        يستخدم urllib (built-in) لتجاوز قيود aiohttp في Railway.
        1000 شمعة/طلب × طلبات متعددة = سنوات من البيانات.
        """
        import urllib.request
        import urllib.parse
        import ssl

        results  = []
        limit    = 1000
        requests_needed = min((days // limit) + 1, 4)
        end_time = None
        ctx      = ssl.create_default_context()

        def _fetch_klines(params_dict: dict) -> list:
            """جلب synchronous في executor."""
            qs  = urllib.parse.urlencode(params_dict)
            url = f"https://api.binance.com/api/v3/klines?{qs}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
                    "Accept":     "application/json",
                },
            )
            with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))

        loop = asyncio.get_event_loop()

        for req_num in range(requests_needed):
            try:
                params: Dict = {
                    "symbol":   f"{symbol.upper()}USDT",
                    "interval": "1d",
                    "limit":    str(limit),
                }
                if end_time:
                    params["endTime"] = str(end_time)

                data = await loop.run_in_executor(
                    None, lambda p=params: _fetch_klines(p))

                if not isinstance(data, list) or len(data) == 0:
                    break

                for c in data:
                    try:
                        ts    = float(c[0]) / 1000
                        open_ = float(c[1])
                        high  = float(c[2])
                        low   = float(c[3])
                        close = float(c[4])
                        vol   = float(c[5])
                        if close > 0 and high >= low > 0:
                            results.append({
                                "timestamp": ts,
                                "open":      open_,
                                "high":      high,
                                "low":       low,
                                "close":     close,
                                "price":     close,
                                "volume":    vol,
                                "source":    "binance",
                            })
                    except (ValueError, TypeError, IndexError):
                        continue

                if data:
                    end_time = int(float(data[0][0])) - 1

                await asyncio.sleep(0.3)

            except Exception as e:
                logger.warning(f"Binance hist ({symbol}) req {req_num+1}: {e}")
                break

        if results:
            logger.info(f"Binance hist ({symbol}): {len(results)} شمعة OHLCV حقيقية ✅")
        return results
    async def _hist_coingecko(self, symbol: str, days: int) -> List[Dict]:
        """
        CoinGecko fallback — price + volume + H/L ديناميكي واقعي.
        H/L محسوب من التقلب الفعلي (std) بدلاً من نسبة ثابتة.
        """
        cg      = _cg_id(symbol)
        results = []
        chunks  = [365, 365, max(days - 730, 30)] if days > 730 else [days]

        for chunk in chunks:
            if chunk <= 0:
                continue
            data = await _fetch(
                self.session,
                f"https://api.coingecko.com/api/v3/coins/{cg}/market_chart",
                headers=_H_CG,
                params={"vs_currency": "usd",
                        "days": str(chunk),
                        "interval": "daily"},
            )
            if isinstance(data, dict):
                prices = data.get("prices", [])
                vols   = data.get("total_volumes", [])
                raw    = []
                for i, item in enumerate(prices):
                    try:
                        raw.append((float(item[0]) / 1000,
                                    float(item[1]),
                                    float(vols[i][1]) if i < len(vols) else 0.0))
                    except (ValueError, TypeError, IndexError):
                        continue

                # حساب التقلب اليومي لكل نقطة (نافذة 14 يوم)
                for i, (ts, price, vol) in enumerate(raw):
                    if price <= 0:
                        continue
                    # حساب std من آخر 14 يوم
                    window = raw[max(0, i-14):i+1]
                    if len(window) >= 3:
                        px     = [w[1] for w in window if w[1] > 0]
                        rets   = [(px[j]-px[j-1])/px[j-1]
                                  for j in range(1, len(px)) if px[j-1] > 0]
                        vol_d  = (sum(r**2 for r in rets)/len(rets))**0.5 if rets else 0.02
                        # تحديد نطاق H/L واقعي
                        vol_d  = max(0.005, min(vol_d * 1.5, 0.08))
                    else:
                        vol_d  = 0.025   # افتراضي 2.5%

                    open_  = raw[i-1][1] if i > 0 else price
                    results.append({
                        "timestamp": ts,
                        "price":     price,
                        "close":     price,
                        "open":      open_,
                        "high":      price * (1 + vol_d / 2),
                        "low":       price * (1 - vol_d / 2),
                        "volume":    vol,
                        "source":    "coingecko",
                    })
            await asyncio.sleep(2)
        return results

    # ═══════════════════════════════════════════════════════════
    # 4. الأخبار — يُعيد دائماً List
    # ═══════════════════════════════════════════════════════════
    async def get_news(self, currencies: str = "BTC,ETH",
                        limit: int = 20) -> List[Dict]:
        key = f"news:{currencies}"
        if cached := _cached(key, "news"):
            return cached

        items = []

        # ── ١. CryptoPanic ─────────────────────────────────────
        try:
            params = {"public": "true", "currencies": currencies,
                      "filter": "important", "kind": "news"}
            if self.cryptopanic_key:
                params["auth_token"] = self.cryptopanic_key

            data = await _fetch(self.session,
                                "https://cryptopanic.com/api/v1/posts/",
                                params=params)
            if isinstance(data, dict):
                for post in data.get("results", [])[:limit]:
                    title = post.get("title", "")
                    if title:
                        items.append({
                            "title":     title,
                            "url":       post.get("url", ""),
                            "source":    post.get("source", {}).get("title", ""),
                            "published": post.get("created_at", ""),
                            "votes_pos": post.get("votes", {}).get("positive", 0),
                            "votes_neg": post.get("votes", {}).get("negative", 0),
                        })
        except Exception as e:
            logger.warning(f"CryptoPanic: {e}")

        # ── ٢. RSS — دائماً يعمل بغض النظر عن CryptoPanic ─────
        # نُمرر العملات للفلترة الذكية
        symbols_list = [s.strip().upper() for s in currencies.split(",") if s.strip()]
        rss = await self._rss_news(filter_symbols=symbols_list)
        existing = {i.get("title", "")[:50].lower() for i in items}
        for r in rss:
            t = r.get("title", "")
            if t and t[:50].lower() not in existing:
                items.append(r)
                existing.add(t[:50].lower())

        # ترتيب: أخبار العملات المطلوبة أولاً
        if symbols_list:
            def _relevance(item):
                t = item.get("title", "").lower()
                return sum(
                    3 if s.lower() in t else
                    1 if s[:3].lower() in t else 0
                    for s in symbols_list
                )
            items.sort(key=_relevance, reverse=True)

        logger.info(f"News ({currencies}): {len(items)} خبر مُجمَّع")
        items = items[:limit]
        if items:
            _store(key, items, "news")
        return items
    async def _rss_news(self, filter_symbols: List[str] = None) -> List[Dict]:
        """
        يجلب أخبار RSS.
        filter_symbols: فلترة بالعملات (اختياري) — يُعيد الأخبار المتعلقة بها أولاً
        """
        sources = [
            ("CoinTelegraph", "https://cointelegraph.com/rss"),
            ("Decrypt",       "https://decrypt.co/feed"),
            ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ]
        results = []
        for name, url in sources:
            try:
                async with self.session.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0",
                             "Accept": "application/rss+xml, text/xml"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status == 200:
                        text = await r.text()
                        for block in re.findall(r"<item>(.*?)</item>",
                                                text, re.DOTALL)[:6]:
                            t = re.search(
                                r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                                block, re.DOTALL)
                            l = re.search(r"<link>(.*?)</link>", block)
                            if t:
                                title = t.group(1).strip()
                                if title:
                                    results.append({
                                        "title":     title[:200],
                                        "url":       l.group(1).strip() if l else "",
                                        "source":    name,
                                        "published": "",
                                        "votes_pos": 0,
                                        "votes_neg": 0,
                                    })
            except Exception as e:
                logger.warning(f"RSS {name}: {e}")
        return results

    # ═══════════════════════════════════════════════════════════
    # 5. On-Chain — يُعيد دائماً Dict
    # ═══════════════════════════════════════════════════════════
    async def get_onchain(self, protocol: str = "all") -> Dict:
        key = f"onchain:{protocol}"
        if cached := _cached(key, "onchain"):
            return cached

        result: Dict = {"tvl": 0, "volume24h": 0,
                         "protocols": [], "source": "defillama"}

        data = await _fetch(self.session, "https://api.llama.fi/charts",
                             headers=_H_DL)
        if isinstance(data, list) and data:
            try:
                result["tvl"] = float(data[-1].get("totalLiquidityUSD", 0))
            except (ValueError, TypeError):
                pass

        protos = await _fetch(self.session, "https://api.llama.fi/protocols",
                               headers=_H_DL)
        if isinstance(protos, list):
            # فلترة: DeFi فقط — استبعاد CEX والبورصات المركزية
            CEX_EXCLUDE = {
                "binance","okx","bitfinex","bybit","coinbase","kraken",
                "gate","kucoin","htx","huobi","crypto.com","bitstamp",
                "gemini","bitget","mexc","binance cex","okx exchange",
            }
            defi_only = [
                p for p in protos
                if p.get("name","").lower() not in CEX_EXCLUDE
                and p.get("category","").lower() not in ("cex","exchange","centralized exchange")
            ]
            top = sorted(defi_only, key=lambda x: float(x.get("tvl") or 0),
                         reverse=True)[:10]
            result["protocols"] = [
                {"name":  p.get("name", ""),
                 "tvl":   float(p.get("tvl") or 0),
                 "chain": p.get("chain", "multi"),
                 "category": p.get("category", "")}
                for p in top
            ]

        _store(key, result, "onchain")
        return result   # دائماً Dict

    # ═══════════════════════════════════════════════════════════
    # 6. Fear & Greed — يُعيد دائماً Dict مع قيم افتراضية
    # ═══════════════════════════════════════════════════════════
    async def get_fear_greed(self) -> Dict:
        _DEFAULT = {"value": 50, "label": "Neutral", "label_ar": "محايد"}
        if cached := _cached("fear_greed", "fear"):
            return cached

        data = await _fetch(
            self.session,
            "https://api.alternative.me/fng/?limit=1",
            headers=_H_GN,
        )
        if not isinstance(data, dict) or "data" not in data:
            return _DEFAULT

        try:
            item   = data["data"][0]
            val    = int(item["value"])
            result = {
                "value":    val,
                "label":    item.get("value_classification", "Neutral"),
                "label_ar": _fear_ar(val),
            }
            _store("fear_greed", result, "fear")
            return result
        except (ValueError, TypeError, IndexError, KeyError):
            return _DEFAULT

    # ═══════════════════════════════════════════════════════════
    # 7. Top Coins — يُعيد دائماً List
    # ═══════════════════════════════════════════════════════════
    async def get_top_coins(self, limit: int = 20) -> List[Dict]:
        key = f"top:{limit}"
        if cached := _cached(key, "news"):
            return cached

        data = await _fetch(
            self.session,
            "https://api.coingecko.com/api/v3/coins/markets",
            headers=_H_CG,
            params={
                "vs_currency": "usd",
                "order":       "market_cap_desc",
                "per_page":    str(limit),
                "page":        "1",
                "sparkline":   "false",
            },
        )
        if not isinstance(data, list):
            return []

        stables = {"USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX","LUSD"}
        filtered = [c for c in data
                    if isinstance(c, dict)
                    and c.get("symbol","").upper() not in stables]
        _store(key, filtered, "news")
        return filtered


# ─── Helpers ──────────────────────────────────────────────────
def _fear_ar(value: int) -> str:
    if value >= 75: return "جشع شديد"
    if value >= 55: return "جشع"
    if value >= 45: return "محايد"
    if value >= 25: return "خوف"
    return "خوف شديد"

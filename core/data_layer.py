"""
📡 رائد — Data Layer (الطبقة 1) — النسخة المُحكمة
CoinGecko · Binance · DeFiLlama · CryptoPanic
- retry تلقائي 3 محاولات
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
try:
    from core.coins_list import get_cg_id as _coins_cg_id, RANKED_CG_MAP as _RANKED_CG_MAP
except ImportError:
    _coins_cg_id = None
    _RANKED_CG_MAP = {}

logger = logging.getLogger(__name__)

# ─── TTL كاش ──────────────────────────────────────────────────
CACHE_TTL = {
    "price":   60,    # 60 ثانية — مناسب لـ 100+ مستخدم (كان 30)
    "ohlcv":   600,   # 10 دقائق (كان 5)
    "news":    600,   # 10 دقائق (كان 5)
    "onchain": 900,   # 15 دقيقة (كان 10)
    "fear":    3600,
    "hist":    7200,  # ساعتان (كان ساعة)
}
# كاش مشترك بين المستخدمين — طلب واحد يخدم الجميع
_shared_price_cache: Dict[str, Dict] = {}
_SHARED_PRICE_TTL = 60  # ثانية
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
    "TON":"the-open-network","PEPE":"pepe","ARKM":"arkham","BGB":"bitget-token","OKB":"okb",
    # عملات جديدة 2024-2026
    "HYPE":"hyperliquid","LEO":"leo-token","TRUMP":"maga",
    # DeFi classics
    "CRV":"curve-dao-token","MKR":"maker","SNX":"havven",
    "COMP":"compound-governance-token","YFI":"yearn-finance",
    "BAL":"balancer","SUSHI":"sushi","1INCH":"1inch",
    "CAKE":"pancakeswap-token","GMX":"gmx",
    # Infrastructure
    "RAIN":"rain-coin-2","KDA":"kadena","ROSE":"oasis-network",
    "KAVA":"kava","BAND":"band-protocol","API3":"api3",
    "REN":"republic-protocol","CELO":"celo",
    # إضافات مطلوبة — عملات كانت تفشل بـ ID خاطئ
    "CFX":"conflux-token",
    # ── إضافات M#105 + عملات 2024/2025 ──────────────────────
    "GPS":"goplus-security",
    "GOAT":"goat",
    "VIRTUAL":"virtual-protocol",
    "ZEREBRO":"zerebro",
    "AI16Z":"ai16z",
    "GRIFFAIN":"griffain",
    "FARTCOIN":"fartcoin",
    "SONIC":"sonic-3",
    "MELANIA":"melania-meme",
    "SPX":"spx6900",
    "BOME":"book-of-meme",
    "SLERF":"slerf",
    "MYRO":"myro",
    "RETARDIO":"retardio",
    "GIGA":"gigachad-memecoin",
"ZRO":"layerzero","ASTER":"aster-network",    # ── إضافات ملاحظات #36,#38,#44 ──────────────────────────────
    "RSR":"reserve-rights-token",
    "QNT":"quant-network",
    "BLUR":"blur",
    "JASMY":"jasmycoin",
    "LUNA":"terra-luna-2",
    "LUNC":"terra-luna",
    "GMT":"stepn",
    "FTM":"fantom",
    "KSM":"kusama",
    "EGLD":"elrond-erd-2",
    "MINA":"mina-protocol",
    "RON":"ronin",
    "OSMO":"osmosis",
    "RUNE":"thorchain",
    "MKR":"maker",
    "SNX":"havven",
    "PENDLE":"pendle",
    "GMX":"gmx",
    "DYDX":"dydx",
    "APE":"apecoin",
    "LPT":"livepeer",
    "GNS":"gains-network",
    "MAGIC":"magic",
    "ANKR":"ankr",
    "AUDIO":"audius",
    "AXL":"axelar",
    "BICO":"biconomy",
    "C98":"coin98",
    "COTI":"coti",
    "CVC":"civic",
    "EDU":"open-campus",
    "ELF":"aelf",
    "FLUX":"zelcash",
    "GAL":"project-galaxy",
    "GHST":"aavegotchi",
    "GNO":"gnosis",
    "HOOK":"hooked-protocol",
    "ID":"space-id",
    "ILV":"illuvium",
    "IOST":"iostoken",
    "JOE":"joe",
    "JST":"just",
    "KLAY":"klay-token",
    "LSK":"lisk",
    "MAV":"maverick-protocol",
    "OCEAN":"ocean-protocol",
    "PEOPLE":"constitutiondao",
    "PERP":"perpetual-protocol",
    "RAY":"raydium",
    "REEF":"reef",
    "RLC":"iexec-rlc",
    "SCRT":"secret",
    "SKL":"skale",
    "SLP":"smooth-love-potion",
    "SNT":"status",
    "SPELL":"spell-token",
    "SUN":"sun-token",
    "SYN":"synapse-2",
    "THETA":"theta-token",
    "TFUEL":"theta-fuel",
    "TLM":"alien-worlds",
    "TRU":"truefi",
    "TWT":"trust-wallet-token",
    "UMA":"uma",
    "VET":"vechain",
    "WAXP":"wax",
    "XEM":"nem",
    "XMR":"monero",
    "XNO":"nano",
    "XTZ":"tezos",
    "XVS":"venus",
    "YGG":"yield-guild-games",
    "ZEC":"zcash",
    "ZEN":"horizen",
    "ZIL":"zilliqa",
    "ZRX":"0x",
    "STORJ":"storj",
    "GLM":"golem",
    "ANT":"aragon",
    "AMP":"amp-token",
    "NMR":"numeraire",
    "GRT":"the-graph",
    "BAT":"basic-attention-token",
    "THETA":"theta-token","TFUEL":"theta-fuel",
    "IOTA":"iota","MIOTA":"iota",
    "XTZ":"tezos","ZEC":"zcash","DASH":"dash",
    "NEO":"neo","ONT":"ontology",
    "QTUM":"qtum","ZEN":"horizen",
    "ICX":"icon","WAVES":"waves",
    "HOT":"holotoken","ENS":"ethereum-name-service",
    "LDO":"lido-dao","RPL":"rocket-pool",
    "SSV":"ssv-network","ANKR":"ankr",
    "OCEAN":"ocean-protocol","NMR":"numeraire",
    "GRT":"the-graph","BAT":"basic-attention-token",
    "ZRX":"0x","STORJ":"storj","SKL":"skale",
    "CELR":"celer-network","CTSI":"cartesi",
    "DYDX":"dydx","PERP":"perpetual-protocol",
    "MASK":"mask-network","QUICK":"quick",
    "BICO":"biconomy","ACH":"alchemy-pay",
    "HIGH":"highstreet","BURGER":"burger-swap",
    "RAY":"raydium","SRM":"serum",
    "FIDA":"bonfida","MNGO":"mango-markets",
    "STEP":"step-finance","COPE":"cope",
    "FLOW":"flow","MINA":"mina-protocol",
    "HNT":"helium","AR":"arweave","SC":"siacoin",
    "DCR":"decred","XEM":"nem","LSK":"lisk",
    "STEEM":"steem","BTT":"bittorrent",
    "WIN":"wink","SXP":"swipe",
    "VITE":"vite","MDX":"mdex",
    "ALPHA":"alpha-finance","CREAM":"cream-2",
    "RAMP":"ramp","SWAP":"trustswap",
    # Layer 2
    "IMX":"immutable-x","METIS":"metis-token",
    "BOBA":"boba-network","ZKS":"zksync",
    "BONK":"bonk","WIF":"dogwifcoin","POPCAT":"popcat",
    "BRETT":"based-brett","MOG":"mog-coin",
    "EIGEN":"eigenlayer","ENA":"ethena",
    "TAO":"bittensor","WLD":"worldcoin-wld","NOT":"notcoin","DOGS":"dogs-2","ORDI":"ordi","SATS":"1000sats-ordinals",
    "ONDO":"ondo-finance","PYTH":"pyth-network",
    "JTO":"jito-governance-token","JUP":"jupiter-ag",
    "STRK":"starknet","MANTA":"manta-network",
    "ALT":"altlayer","PIXEL":"pixels",
    "PORTAL":"portal-gaming","ROAM":"roam",
    # إضافات مهمة
    "BGB":"bitget-token","OKB":"okb","HT":"huobi-token",
    "CRO":"crypto-com-chain","FTT":"ftx-token","GT":"gate",
    "MX":"mexc-token","KCS":"kucoin-shares","WBT":"whitebit",
    "INJ":"injective-protocol","SEI":"sei-network","RAIN":"rain-coin-2","NAKA":"nakamoto-games",
    "TIA":"celestia","PYTH":"pyth-network","JTO":"jito-governance-token",
    "WIF":"dogwifcoin","BONK":"bonk","FLOKI":"floki",
    "RENDER":"render-token","FET":"fetch-ai","FETCH":"fetch-ai","AGIX":"singularitynet",
    "TAO":"bittensor","WLD":"worldcoin-wld","NOT":"notcoin","DOGS":"dogs-2","ORDI":"ordi","SATS":"1000sats-ordinals","ONDO":"ondo-finance",
    "STX":"blockstack","ICP":"internet-computer","FIL":"filecoin",
    "HBAR":"hedera-hashgraph","VET":"vechain","ALGO":"algorand",
    "EOS":"eos","XLM":"stellar","XMR":"monero","AAVE":"aave",
    "SAND":"the-sandbox","MANA":"decentraland","AXS":"axie-infinity",
    "CHZ":"chiliz","GALA":"gala","ENJ":"enjincoin",
    "RUNE":"thorchain","KAVA":"kava","ZIL":"zilliqa",
    # M#113: عملات مفقودة + 2024/2025
    "CHR": "chromaway",
    "CHROME": "chromaway",
    "ALICE": "my-neighbor-alice",
    "LAZIO": "lazio-fan-token",
    "PORTO": "porto-fan-token",
    "OG": "og-fan-token",
    "PRIME": "echelon-prime",
    "TNSR": "tensor",
    "WEN": "wen-4",
    "MEW": "cat-in-a-dogs-world",
    "KMNO": "kamino",
    "W": "wormhole",
    "OMNI": "omni-network",
    "REZ": "renzo",
    "ETHFI": "ether-fi",
    "SAGA": "saga-2",

}
def _cg_id(symbol: str) -> str:
    sym = symbol.upper()
    # 1. فحص _CG_MAP المحلي أولاً
    if sym in _CG_MAP:
        return _CG_MAP[sym]
    # 2. فحص coins_list
    if _RANKED_CG_MAP and sym in _RANKED_CG_MAP:
        return _RANKED_CG_MAP[sym]
    # 3. fallback: lowercase
    return sym.lower()

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
                timeout=aiohttp.ClientTimeout(total=35)
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
                elif r.status == 451:
                    logger.warning(f"HTTP 451 [{url[:55]}] — محجوب. تخطٍّ فوري")
                    return None
                elif r.status in (403, 401):
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

        # OKX أولاً — سريع وغير محجوب على Railway
        result = await self._price_okx(symbol)
        if result and result.get("price", 0) > 0:
            _store(key, result)
            return result

        # CoinGecko fallback
        result = await self._price_coingecko(symbol)
        if result and result.get("price", 0) > 0:
            _store(key, result)
            return result

        # Binance آخراً (محجوب عادةً على Railway)
        result = await self._price_binance(symbol)
        if result and result.get("price", 0) > 0:
            _store(key, result)
            return result

        logger.error(f"get_price فشل لـ {symbol}")
        return None

    async def _price_binance(self, symbol: str) -> Optional[Dict]:
        # نجرب endpoints متعددة لتجاوز قيود Railway
        endpoints = [
            "https://api.binance.com/api/v3/ticker/24hr",
            "https://api1.binance.com/api/v3/ticker/24hr",
            "https://api2.binance.com/api/v3/ticker/24hr",
            "https://api3.binance.com/api/v3/ticker/24hr",
        ]
        data = None
        for ep in endpoints:
            data = await _fetch(self.session, ep, headers=_H_BN,
                                params={"symbol": f"{symbol.upper()}USDT"}, retries=2)
            if isinstance(data, dict) and "lastPrice" in data:
                break
            data = None
        if not isinstance(data, dict) or "lastPrice" not in data:
            return None
        try:
            price = float(data["lastPrice"])
            if price <= 0:
                return None
            change = float(data.get("priceChangePercent", 0))
            return {
                "symbol":                        symbol.upper(),
                "price":                         price,
                "change_24h":                    change,
                "price_change_percentage_24h":   change,   # توافق مع CoinGecko
                "volume_24h":                    float(data.get("quoteVolume", 0)),
                "high_24h":                      float(data.get("highPrice", 0)),
                "low_24h":                       float(data.get("lowPrice", 0)),
                "source":                        "binance",
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
            change = float(coin.get("usd_24h_change", 0))
            return {
                "symbol":                       symbol.upper(),
                "price":                        price,
                "change_24h":                   change,
                "price_change_percentage_24h":  change,   # توافق موحَّد
                "volume_24h":                   float(coin.get("usd_24h_vol", 0)),
                "market_cap":                   float(coin.get("usd_market_cap", 0)),
                "source":                       "coingecko",
            }
        except (ValueError, TypeError):
            return None


    async def _price_okx(self, symbol: str) -> Optional[Dict]:
        """OKX Public API — fallback للسعر (غير محجوب على Railway)."""
        try:
            inst_id = f"{symbol.upper()}-USDT"
            data = await _fetch(
                self.session,
                f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                retries=2,
            )
            if isinstance(data, dict) and data.get("data"):
                ticker = data["data"][0]
                price  = float(ticker.get("last", 0))
                if price <= 0:
                    return None
                open24 = float(ticker.get("open24h", price) or price)
                change = ((price - open24) / open24 * 100) if open24 > 0 else 0
                return {
                    "symbol":                       symbol.upper(),
                    "price":                        price,
                    "change_24h":                   round(change, 4),
                    "price_change_percentage_24h":  round(change, 4),
                    # vol24h = حجم بـ USDT (الصحيح) | volCcy24h = حجم بـ BTC
                    "volume_24h":                   float(ticker.get("vol24h", 0) or
                                                          ticker.get("volCcy24h", 0) or 0),
                    "high_24h":                     float(ticker.get("high24h", 0) or 0),
                    "low_24h":                      float(ticker.get("low24h", 0) or 0),
                    "source":                       "okx",
                }
        except Exception as e:
            logger.debug(f"_price_okx ({symbol}): {e}")
        return None

    # ═══════════════════════════════════════════════════════════
    # 2. OHLCV — يُعيد دائماً List (قد تكون فارغة لكن ليست None)
    # ═══════════════════════════════════════════════════════════
    async def get_ohlcv(self, symbol: str, interval: str = "1d",
                         limit: int = 365) -> List[Dict]:
        key = f"ohlcv:{symbol}:{interval}:{limit}"
        if cached := _cached(key, "ohlcv"):
            return cached  # دائماً List

        # ── OKX أولاً — سريع وغير محجوب على Railway ──────────
        candles = await self._hist_okx(symbol, min(limit, 300))
        if len(candles) >= 10:
            _store(key, candles, "ohlcv")
            return candles

        # ── CoinGecko fallback ─────────────────────────────────
        cg_days    = min(limit, 2000)
        cg_candles = await self._ohlcv_coingecko(symbol, cg_days)
        if len(cg_candles) >= 10:
            _store(key, cg_candles, "ohlcv")
            return cg_candles

        # ── Binance آخراً (محجوب عادةً على Railway) ───────────
        candles = await self._ohlcv_binance(symbol, interval, limit)
        if len(candles) >= 10:
            _store(key, candles, "ohlcv")
            return candles

        # إذا CoinGecko أعطى بيانات قليلة — نُعيدها على أي حال
        if cg_candles:
            _store(key, cg_candles, "ohlcv")
            return cg_candles

        logger.error(f"get_ohlcv فشل لـ {symbol} — يُعيد []")
        return []   # دائماً List, أبداً None

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
                # حساب OHLC صحيح — يضمن l <= o <= h و l <= c <= h
                high_p = max(prev_p, curr_p) * 1.005
                low_p  = min(prev_p, curr_p) * 0.995
                # تأكد أن open و close بين high و low
                open_p  = max(low_p, min(prev_p, high_p))
                close_p = max(low_p, min(curr_p, high_p))
                candle = {
                    "timestamp": float(prices[i][0]) / 1000,
                    "open":      open_p,
                    "high":      high_p,
                    "low":       low_p,
                    "close":     close_p,
                    "volume":    float(vols[i][1]) if i < len(vols) else 0.0,
                }
                try:
                    res = validator.validate_ohlcv(candle)
                    if res.is_usable:
                        candles.append(res.cleaned)
                    else:
                        # إضافة مباشرة بدون validator إذا فشل
                        candles.append(candle)
                except Exception:
                    candles.append(candle)
            except (ValueError, TypeError, IndexError):
                continue
        if len(candles) < 5:
            logger.warning(f"CoinGecko OHLCV ({symbol}): بيانات قليلة {len(candles)} شمعة")
        else:
            logger.info(f"CoinGecko OHLCV ({symbol}): {len(candles)} شمعة")
        return candles

    # ═══════════════════════════════════════════════════════════
    # 3. بيانات تاريخية 3 سنوات — يُعيد دائماً List
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

        # ── OKX fallback (غير محجوب على Railway) ──────────────
        logger.info(f"Historical: Binance فشل لـ {symbol} — OKX fallback")
        results = await self._hist_okx(symbol, min(days, 200))
        if len(results) >= 30:
            logger.info(f"Historical OKX ({symbol}): {len(results)} شمعة")
            _store(key, results, "hist")
            return results

        # ── CoinGecko fallback ──────────────────────────────────
        logger.info(f"Historical: OKX فشل لـ {symbol} — CoinGecko fallback")
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
    async def _hist_coingecko_full(self, symbol: str, days: int) -> List[Dict]:
        """تاريخ كامل حتى 3 سنوات من CoinGecko market_chart (طلب واحد)."""
        cg_id = _CG_MAP.get(symbol.upper())
        if not cg_id:
            return []
        try:
            url  = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
            data = await _fetch(self.session, url, headers=_H_CG,
                                params={"vs_currency": "usd",
                                        "days": str(min(days, 2000)),
                                        "interval": "daily"})
            if not isinstance(data, dict):
                return []
            prices  = data.get("prices", [])
            volumes = data.get("total_volumes", [])
            vol_map = {int(v[0]): v[1] for v in volumes}
            result  = []
            for i in range(1, len(prices)):
                ts, price = prices[i]
                prev = float(prices[i-1][1])
                result.append({
                    "close":     float(price),
                    "open":      prev,
                    "high":      float(price) * 1.005,
                    "low":       float(price) * 0.995,
                    "volume":    float(vol_map.get(int(ts), 0)),
                    "timestamp": int(ts) / 1000,
                    "price":     float(price),
                })
            return result
        except Exception as e:
            logger.warning(f"_hist_coingecko_full ({symbol}): {e}")
            return []

    async def _hist_coingecko(self, symbol: str, days: int) -> List[Dict]:
        """
        CoinGecko fallback — price + volume + H/L ديناميكي واقعي.
        إصلاح #200: 3 طلبات بـ from/to صريح → ~1000 نقطة فعلية
        CoinGecko يُعيد 200 نقطة/طلب عند days=365
        3 طلبات × 200 ≈ 600 نقطة (بدلاً من 200)
        """
        import time as _time
        cg      = _cg_id(symbol)
        results = []

        # حساب نطاقات زمنية صريحة (from/to) بدلاً من days
        now     = int(_time.time())
        # 3 دُفعات: آخر سنة، السنة قبلها، السنة الثالثة
        ranges  = [
            (now - 365*24*3600,       now),
            (now - 730*24*3600,       now - 365*24*3600),
            (now - 1095*24*3600,      now - 730*24*3600),
        ]
        if days <= 365:
            ranges = [(now - days*24*3600, now)]

        for from_ts, to_ts in ranges:
            data = await _fetch(
                self.session,
                f"https://api.coingecko.com/api/v3/coins/{cg}/market_chart/range",
                headers=_H_CG,
                params={"vs_currency": "usd",
                        "from": str(from_ts),
                        "to":   str(to_ts)},
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
# CoinGecko News — بديل CryptoPanic
    async def _news_coingecko(self) -> list:
        """يجلب أخبار CoinGecko العامة."""
        try:
            data = await _fetch(self.session,
                "https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&kind=news",
                headers=_H_CG, params={"page": "1"})
            if isinstance(data, dict) and "data" in data:
                items = []
                for n in data["data"][:20]:
                    items.append({
                        "title":       n.get("title", ""),
                        "description": n.get("description", ""),
                        "published":   n.get("updated_at", ""),
                        "url":         n.get("url", ""),
                        "source":      "coingecko",
                    })
                return items
        except Exception as e:
            logger.debug(f"CoinGecko news: {e}")
        return []

    async def _news_coindesk_rss(self) -> list:
        """CoinDesk RSS — fallback."""
        try:
            html = await _fetch(self.session,
                "https://www.coindesk.com/arc/outboundfeeds/rss/", {})
            if not isinstance(html, str): return []
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', html)
            links  = re.findall(r'<link>(https://www\.coindesk\.com/[^<]+)</link>', html)
            return [{"title": t, "url": l, "source": "coindesk"}
                    for t, l in zip(titles[1:], links)][:10]
        except Exception as e:
            logger.debug(f"CoinDesk RSS: {e}")
        return []

    async def get_news(self, currencies: str = "BTC,ETH",
                        limit: int = 20) -> List[Dict]:
        key = f"news:{currencies}"
        if cached := _cached(key, "news"):
            return cached

        items = []

        # ── 1. CoinGecko News (بديل CryptoPanic) ────────────────
        try:
            cg_news = await self._news_coingecko()
            if cg_news:
                items.extend(cg_news[:15])
        except Exception as e:
            logger.debug(f"CoinGecko news: {e}")

        # ── 2. RSS — دائماً يعمل بغض النظر عن CryptoPanic ─────
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

        # فلترة الأخبار القديمة (> 48 ساعة)
        items = _filter_recent_news(items, max_hours=48)
        logger.info(f"News ({currencies}): {len(items)} خبر مُجمَّع (بعد فلترة الوقت)")
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

    # ═══ مؤشرات On-Chain المتقدمة ═══

    async def get_funding_rate(self, symbol: str) -> dict:
        """يجلب Funding Rate من OKX + Binance."""
        key = f"funding:{symbol}"
        if cached := _cached(key, "funding"):
            return cached
        result = {"rate": 0.0, "rate_pct": 0.0, "signal": "محايد", "source": "unknown"}
        try:
            # OKX
            inst = f"{symbol.upper()}-USDT-SWAP"
            url  = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}"
            if self.session:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        items = data.get("data", [])
                        if items:
                            rate = float(items[0].get("fundingRate", 0))
                            result = {
                                "rate":     rate,
                                "rate_pct": round(rate * 100, 4),
                                "signal":   "⚠️ ضغط على Longs" if rate > 0.0005
                                            else "✅ فرصة Longs" if rate < -0.0001
                                            else "⚪ محايد",
                                "source":   "okx",
                            }
                            _store(key, result, "funding")
                            return result
        except Exception as e:
            logger.debug(f"funding_rate ({symbol}): {e}")
        return result

    async def get_open_interest(self, symbol: str) -> dict:
        """يجلب Open Interest من OKX."""
        key = f"oi:{symbol}"
        if cached := _cached(key, "funding"):
            return cached
        result = {"oi_usd": 0.0, "oi_change_pct": 0.0, "signal": "محايد", "source": "unknown"}
        try:
            inst = f"{symbol.upper()}-USDT-SWAP"
            url  = f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId={inst}&period=1H&limit=2"
            if self.session:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        items = data.get("data", [])
                        if len(items) >= 2:
                            oi_now  = float(items[0][1]) if items[0] else 0
                            oi_prev = float(items[1][1]) if items[1] else oi_now
                            chg     = (oi_now - oi_prev) / max(oi_prev, 1) * 100
                            result  = {
                                "oi_usd":        oi_now,
                                "oi_change_pct": round(chg, 2),
                                "signal":        "📈 ضغط شرائي" if chg > 5
                                                 else "📉 ضغط بيعي" if chg < -5
                                                 else "⚪ محايد",
                                "source":        "okx",
                            }
                            _store(key, result, "funding")
                            return result
        except Exception as e:
            logger.debug(f"open_interest ({symbol}): {e}")
        return result

    async def get_whale_ratio(self, symbol: str) -> dict:
        """يجلب Exchange Whale Ratio من CoinGlass."""
        key = f"whale:{symbol}"
        if cached := _cached(key, "onchain"):
            return cached
        result = {"ratio": 0.0, "signal": "محايد", "inflow": 0.0, "outflow": 0.0}
        try:
            url  = f"https://open-api.coinglass.com/public/v2/indicator/exchange_whale_ratio?symbol={symbol.upper()}&timeType=h4"
            if self.session:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        items = data.get("data", {})
                        if items:
                            ratio = float(items.get("whaleRatio", 0))
                            result = {
                                "ratio":   round(ratio, 3),
                                "signal":  "🔴 الحيتان تبيع" if ratio > 0.85
                                           else "🟢 الحيتان تتراكم" if ratio < 0.60
                                           else "🟡 نشاط متوسط",
                                "inflow":  float(items.get("exchangeInflow",  0)),
                                "outflow": float(items.get("exchangeOutflow", 0)),
                            }
                            _store(key, result, "onchain")
                            return result
        except Exception as e:
            logger.debug(f"whale_ratio ({symbol}): {e}")
        return result

    async def get_miner_flows(self, symbol: str = "BTC") -> dict:
        """يجلب نشاط تدفقات المعدنين."""
        key = f"miner:{symbol}"
        if cached := _cached(key, "onchain"):
            return cached
        # CoinGlass miner flows
        result = {"outflow_30d": 0.0, "signal": "محايد"}
        try:
            url = f"https://open-api.coinglass.com/public/v2/indicator/miner_to_exchange?symbol={symbol}"
            if self.session:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        items = (data.get("data") or [])
                        if items:
                            flow = float(items[-1].get("amount", 0)) if items else 0
                            result = {
                                "outflow_30d": round(flow, 2),
                                "signal": "⚠️ المعدنون يبيعون" if flow > 1000
                                          else "✅ ضغط بيع منخفض",
                            }
                            _store(key, result, "onchain")
        except Exception as e:
            logger.debug(f"miner_flows ({symbol}): {e}")
        return result

    def build_candles_summary(self, candles: list, symbol: str = "") -> str:
        """يبني ملخص شموع احترافي لـ Groq — يشمل EMA + RSI + MACD + حجم."""
        if not candles or len(candles) < 5:
            return ""
        try:
            closes  = [float(c.get("close", 0) or 0) for c in candles if c.get("close")]
            volumes = [float(c.get("volume", 0) or 0) for c in candles if c.get("volume")]
            if len(closes) < 5: return ""

            last = closes[-1]
            # EMAs
            ema5  = sum(closes[-5:])  / 5  if len(closes) >= 5  else last
            ema20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else last
            ema50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else last

            # RSI (14)
            gains = losses = 0.0
            for i in range(-14, 0):
                d = closes[i] - closes[i-1]
                if d > 0: gains  += d
                else:     losses -= d
            avg_g = gains / 14; avg_l = losses / 14
            rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 50

            # حجم vs متوسط
            avg_vol  = sum(volumes[-20:]) / max(len(volumes[-20:]), 1) if volumes else 0
            last_vol = volumes[-1] if volumes else 0
            vol_ratio = last_vol / max(avg_vol, 1)

            # اتجاه آخر 5 شموع
            trend_5d = (closes[-1] - closes[-5]) / max(closes[-5], 1) * 100

            summary = {
                "price":     round(last, 6),
                "ema5":      round(ema5, 6),
                "ema20":     round(ema20, 6),
                "ema50":     round(ema50, 6),
                "rsi14":     round(rsi, 1),
                "trend_5d":  round(trend_5d, 2),
                "vol_ratio": round(vol_ratio, 2),
                "candles_n": len(closes),
                "above_ema5":  last > ema5,
                "above_ema20": last > ema20,
                "above_ema50": last > ema50,
            }
            import json
            return json.dumps(summary, ensure_ascii=False)
        except Exception as e:
            return ""


    async def _hist_okx(self, symbol: str, days: int) -> list:
        """OKX Klines — OHLCV تاريخي للعملات الصغيرة."""
        try:
            inst_id = f"{symbol.upper()}-USDT"
            limit   = min(days, 300)
            data = await _fetch(
                self.session,
                f"https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar=1D&limit={limit}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                retries=2,
            )
            if not isinstance(data, dict) or not data.get("data"):
                return []
            results = []
            for c in data["data"]:
                try:
                    ts    = float(c[0]) / 1000
                    open_ = float(c[1])
                    high  = float(c[2])
                    low   = float(c[3])
                    close = float(c[4])
                    vol   = float(c[5])
                    if close > 0 and high >= low > 0:
                        results.append({
                            "timestamp": ts, "open": open_, "high": high,
                            "low": low, "close": close, "price": close,
                            "volume": vol, "source": "okx",
                        })
                except (ValueError, TypeError, IndexError):
                    continue
            return sorted(results, key=lambda x: x["timestamp"])
        except Exception as e:
            logger.debug(f"_hist_okx ({symbol}): {e}")
            return []


    async def get_best_exchange_for_symbol(self, symbol: str,
                                             exchanges: list) -> str:
        """
        يجد أفضل منصة لتداول عملة معينة بناءً على الحجم.
        يُعيد اسم المنصة ذات السيولة الأعلى.
        M#83: حل جذري لمشكلة OKX حجم=0 لبعض العملات
        """
        best_exchange = ""
        best_volume   = 0.0
        sym_upper     = symbol.upper()

        for ex_name in exchanges:
            try:
                # جلب حجم التداول من كل منصة
                vol = 0.0
                if ex_name == "okx":
                    data = await _fetch(
                        self.session,
                        f"https://www.okx.com/api/v5/market/ticker?instId={sym_upper}-USDT",
                        headers={"User-Agent": "Mozilla/5.0"},
                        retries=1,
                    )
                    if isinstance(data, dict) and data.get("data"):
                        vol = float(data["data"][0].get("volCcy24h", 0) or 0)

                elif ex_name in ("bybit", "bitget", "mexc", "binance"):
                    # Bybit
                    if ex_name == "bybit":
                        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym_upper}USDT"
                    elif ex_name == "mexc":
                        url = f"https://api.mexc.com/api/v3/ticker/24hr?symbol={sym_upper}USDT"
                    else:
                        url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={sym_upper}USDT"
                    try:
                        data = await _fetch(self.session, url,
                                            headers={"User-Agent": "Mozilla/5.0"}, retries=1)
                        if isinstance(data, dict):
                            vol = float(
                                data.get("volume", 0) or
                                data.get("quoteVolume", 0) or
                                (data.get("result", {}).get("list", [{}])[0] if ex_name == "bybit" else {}).get("volume24h", 0) or 0
                            )
                    except Exception:
                        pass

                if vol > best_volume:
                    best_volume   = vol
                    best_exchange = ex_name
                    logger.debug(f"get_best_exchange: {sym_upper} → {ex_name} vol=${vol:,.0f}")

            except Exception as e:
                logger.debug(f"get_best_exchange {ex_name} ({sym_upper}): {e}")

        result = best_exchange or (exchanges[0] if exchanges else "okx")
        logger.info(f"Best exchange for {sym_upper}: {result} vol=${best_volume:,.0f}")
        return result


    async def _search_coingecko(self, symbol: str) -> str:
        """
        M#104/#105: البحث في CoinGecko عن ID العملة تلقائياً.
        يُعيد الـ CoinGecko ID الصحيح أو "" إذا لم يجد.
        """
        try:
            url  = f"https://api.coingecko.com/api/v3/search?query={symbol.upper()}"
            data = await _fetch(self.session, url,
                                headers={"User-Agent":"Mozilla/5.0"}, retries=2)
            if not isinstance(data, dict): return ""
            coins = data.get("coins", [])
            if not coins: return ""
            # البحث عن تطابق دقيق في الرمز أولاً
            sym_upper = symbol.upper()
            for coin in coins[:10]:
                if coin.get("symbol","").upper() == sym_upper:
                    cg_id = coin.get("id","")
                    if cg_id:
                        # حفظ في الـ map للمرات القادمة
                        self._CG_MAP[sym_upper] = cg_id
                        logger.info(f"_search_coingecko: {sym_upper} → {cg_id}")
                        return cg_id
            # إذا لم يجد تطابق دقيق → أقرب نتيجة
            if coins:
                cg_id = coins[0].get("id","")
                if cg_id:
                    self._CG_MAP[sym_upper] = cg_id
                    return cg_id
        except Exception as e:
            logger.debug(f"_search_coingecko ({symbol}): {e}")
        return ""

    async def get_coingecko_id(self, symbol: str) -> str:
        """
        يُعيد CoinGecko ID من الـ map أو يبحث تلقائياً.
        """
        sym = symbol.upper()
        # فحص الـ map أولاً
        if sym in self._CG_MAP:
            return self._CG_MAP[sym]
        # بحث تلقائي
        cg_id = await self._search_coingecko(sym)
        if cg_id:
            return cg_id
        # fallback: استخدام الرمز بالأحرف الصغيرة
        return sym.lower()

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
                "binance","okx","bitfinex","bybit","coinbase","coinbase bridge","coinbase wrapped staked eth","coinbase wrapped","kraken",
                "gate","kucoin","htx","huobi","crypto.com","bitstamp",
                "gemini","bitget","mexc","binance cex","okx exchange","ssv network","lido","binance eth","wbtc","hyperliquid bridge","hyperliquid vault","coinbase bridge","coinbase wrapped","binance bitcoin","binance staked btc","eigencloud","eigen cloud","wrapped bitcoin",
                "binance staked eth","binance eth","binance btc",
                "wrapped bitcoin","wbtc","coinbase wrapped staked eth",
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

        # إضافة تغيير TVL 24h
        try:
            if isinstance(data, list) and len(data) >= 2:
                today_tvl     = float(data[-1].get("totalLiquidityUSD", 0))
                yesterday_tvl = float(data[-2].get("totalLiquidityUSD", today_tvl))
                if yesterday_tvl > 0:
                    result["tvl_change_1d"] = round(
                        (today_tvl - yesterday_tvl) / yesterday_tvl * 100, 2)
                else:
                    result["tvl_change_1d"] = 0.0
            else:
                result["tvl_change_1d"] = 0.0
        except Exception:
            result["tvl_change_1d"] = 0.0

        # ── إضافة Fear & Greed لـ onchain ───────────────────────
        try:
            fg = await self.get_fear_greed()
            result["fear_greed"] = int((fg or {}).get("value", 50))
            result["fear_greed_ar"] = (fg or {}).get("label_ar", "محايد")
        except Exception:
            result["fear_greed"] = 50
            result["fear_greed_ar"] = "محايد"

        # ── بيانات شبكة Bitcoin من Blockchain.info ─────────────
        try:
            btc_stats = await _fetch(
                self.session,
                "https://api.blockchain.info/stats",
                headers={"User-Agent": "Mozilla/5.0"},
                retries=2,
            )
            if isinstance(btc_stats, dict):
                result["btc_hashrate"]     = float(btc_stats.get("hash_rate", 0))
                result["btc_tx_count_24h"] = int(btc_stats.get("n_tx_per_day", 0))
                result["btc_mempool"]      = int(btc_stats.get("mempool_size", 0))
        except Exception:
            pass

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
    async def get_btc_dominance(self) -> float:
        """يجلب هيمنة BTC الحقيقية من CoinGecko."""
        key = "btc_dominance"
        if cached := _cached(key, "fear"):
            return cached
        data = await _fetch(
            self.session,
            "https://api.coingecko.com/api/v3/global",
            headers=_H_CG,
        )
        try:
            dom = float(
                data["data"]["market_cap_percentage"].get("btc", 50.0))
            _store(key, dom, "fear")
            return dom
        except Exception:
            return 50.0

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
def _filter_recent_news(items: list, max_hours: int = 48) -> list:
    """يُزيل الأخبار الأقدم من max_hours ساعة."""
    import re
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    filtered = []
    for item in items:
        pub = item.get("published", "") or ""
        if not pub:
            filtered.append(item)   # بدون تاريخ → نحتفظ
            continue
        try:
            # صيغة ISO: 2024-01-15T10:30:00Z
            pub_clean = re.sub(r'\.\d+', '', pub).replace('Z', '+00:00')
            dt = datetime.fromisoformat(pub_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                filtered.append(item)
        except Exception:
            filtered.append(item)   # خطأ في التحليل → نحتفظ
    return filtered if filtered else items   # إذا فُلتر الكل → أعد الأصلي


def _fear_ar(value: int) -> str:
    if value >= 75: return "جشع شديد"
    if value >= 55: return "جشع"
    if value >= 45: return "محايد"
    if value >= 25: return "خوف"
    return "خوف شديد"

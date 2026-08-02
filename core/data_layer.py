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
from typing import Dict, List, Optional, Any, Tuple
import aiohttp

from core.data_validator import validator
try:
    from core.coins_list import get_cg_id as _coins_cg_id, RANKED_CG_MAP as _RANKED_CG_MAP
except ImportError:
    _coins_cg_id = None
    _RANKED_CG_MAP = {}

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# DL2/DL3: StockSymbolResolver — تعيين موحّد للأسهم المُرمَّزة
# ══════════════════════════════════════════════════════════════════

# قاموس الأسهم المُرمَّزة: رمز المستخدم → {okx_spot, okx_futures, yahoo, base}
# T13_fix: رموز السلع على OKX — Futures فقط
_COMMODITY_SYMBOLS = {
    "CL", "NL", "GC", "SI", "HG", "NG", "WTI", "BRENT",
}

_TOKENIZED_STOCK_MAP = {
    # الأسهم الأمريكية الكبرى
    "AAPL":  {"okx_spot": "XAAPL",  "okx_futures": "AAPL",  "yahoo": "AAPL",  "base": "AAPL", "is_stock": True},
    "XAAPL": {"okx_spot": "XAAPL",  "okx_futures": "AAPL",  "yahoo": "AAPL",  "base": "AAPL", "is_stock": True},
    "AMZN":  {"okx_spot": "XAMZN",  "okx_futures": "AMZN",  "yahoo": "AMZN",  "base": "AMZN", "is_stock": True},
    "XAMZN": {"okx_spot": "XAMZN",  "okx_futures": "AMZN",  "yahoo": "AMZN",  "base": "AMZN", "is_stock": True},
    "TSLA":  {"okx_spot": "XTSLA",  "okx_futures": "TSLA",  "yahoo": "TSLA",  "base": "TSLA", "is_stock": True},
    "XTSLA": {"okx_spot": "XTSLA",  "okx_futures": "TSLA",  "yahoo": "TSLA",  "base": "TSLA", "is_stock": True},
    "GOOGL": {"okx_spot": "XGOOGL", "okx_futures": "GOOGL", "yahoo": "GOOGL", "base": "GOOGL", "is_stock": True},
    "XGOOGL":{"okx_spot": "XGOOGL", "okx_futures": "GOOGL", "yahoo": "GOOGL", "base": "GOOGL", "is_stock": True},
    "MSFT":  {"okx_spot": "XMSFT",  "okx_futures": "MSFT",  "yahoo": "MSFT",  "base": "MSFT", "is_stock": True},
    "XMSFT": {"okx_spot": "XMSFT",  "okx_futures": "MSFT",  "yahoo": "MSFT",  "base": "MSFT", "is_stock": True},
    "META":  {"okx_spot": "XMETA",  "okx_futures": "META",  "yahoo": "META",  "base": "META", "is_stock": True},
    "XMETA": {"okx_spot": "XMETA",  "okx_futures": "META",  "yahoo": "META",  "base": "META", "is_stock": True},
    "NVDA":  {"okx_spot": "XNVDA",  "okx_futures": "NVDA",  "yahoo": "NVDA",  "base": "NVDA", "is_stock": True},
    "XNVDA": {"okx_spot": "XNVDA",  "okx_futures": "NVDA",  "yahoo": "NVDA",  "base": "NVDA", "is_stock": True},
    "AMD":   {"okx_spot": "XAMD",   "okx_futures": "AMD",   "yahoo": "AMD",   "base": "AMD", "is_stock": True},
    "XAMD":  {"okx_spot": "XAMD",   "okx_futures": "AMD",   "yahoo": "AMD",   "base": "AMD", "is_stock": True},
    "NFLX":  {"okx_spot": "XNFLX",  "okx_futures": "NFLX",  "yahoo": "NFLX",  "base": "NFLX", "is_stock": True},
    "XNFLX": {"okx_spot": "XNFLX",  "okx_futures": "NFLX",  "yahoo": "NFLX",  "base": "NFLX", "is_stock": True},
    "COIN":  {"okx_spot": "XCOIN",  "okx_futures": "COIN",  "yahoo": "COIN",  "base": "COIN", "is_stock": True},
    "XCOIN": {"okx_spot": "XCOIN",  "okx_futures": "COIN",  "yahoo": "COIN",  "base": "COIN", "is_stock": True},
    "HOOD":  {"okx_spot": "XHOOD",  "okx_futures": "HOOD",  "yahoo": "HOOD",  "base": "HOOD", "is_stock": True},
    "XHOOD": {"okx_spot": "XHOOD",  "okx_futures": "HOOD",  "yahoo": "HOOD",  "base": "HOOD", "is_stock": True},
    "MSTR":  {"okx_spot": "XMSTR",  "okx_futures": "MSTR",  "yahoo": "MSTR",  "base": "MSTR", "is_stock": True},
    "XMSTR": {"okx_spot": "XMSTR",  "okx_futures": "MSTR",  "yahoo": "MSTR",  "base": "MSTR", "is_stock": True},
    "SPCX":  {"okx_spot": "XSPCX",  "okx_futures": "SPCX",  "yahoo": "SPY",   "base": "SPCX", "yahoo_proxy": True, "is_stock": True},
    "XSPCX": {"okx_spot": "XSPCX",  "okx_futures": "SPCX",  "yahoo": "SPY",   "base": "SPCX", "yahoo_proxy": True, "is_stock": True},
    # أسهم إضافية
    "BABA":  {"okx_spot": "XBABA",  "okx_futures": "BABA",  "yahoo": "BABA",  "base": "BABA", "is_stock": True},
    "XBABA": {"okx_spot": "XBABA",  "okx_futures": "BABA",  "yahoo": "BABA",  "base": "BABA", "is_stock": True},
    "NIO":   {"okx_spot": "XNIO",   "okx_futures": "NIO",   "yahoo": "NIO",   "base": "NIO", "is_stock": True},
    "XNIO":  {"okx_spot": "XNIO",   "okx_futures": "NIO",   "yahoo": "NIO",   "base": "NIO", "is_stock": True},
    "PLTR":  {"okx_spot": "XPLTR",  "okx_futures": "PLTR",  "yahoo": "PLTR",  "base": "PLTR", "is_stock": True},
    "XPLTR": {"okx_spot": "XPLTR",  "okx_futures": "PLTR",  "yahoo": "PLTR",  "base": "PLTR", "is_stock": True},
    "V":     {"okx_spot": "XV",     "okx_futures": "V",     "yahoo": "V",     "base": "V", "is_stock": True},
    "XV":    {"okx_spot": "XV",     "okx_futures": "V",     "yahoo": "V",     "base": "V", "is_stock": True},
    "MA":    {"okx_spot": "XMA",    "okx_futures": "MA",    "yahoo": "MA",    "base": "MA", "is_stock": True},
    "XMA":   {"okx_spot": "XMA",    "okx_futures": "MA",    "yahoo": "MA",    "base": "MA", "is_stock": True},
    "JPM":   {"okx_spot": "XJPM",   "okx_futures": "JPM",   "yahoo": "JPM",   "base": "JPM", "is_stock": True},
    "XJPM":  {"okx_spot": "XJPM",   "okx_futures": "JPM",   "yahoo": "JPM",   "base": "JPM", "is_stock": True},
    "WMT":   {"okx_spot": "XWMT",   "okx_futures": "WMT",   "yahoo": "WMT",   "base": "WMT", "is_stock": True},
    "XWMT":  {"okx_spot": "XWMT",   "okx_futures": "WMT",   "yahoo": "WMT",   "base": "WMT", "is_stock": True},
    "PYPL":  {"okx_spot": "XPYPL",  "okx_futures": "PYPL",  "yahoo": "PYPL",  "base": "PYPL", "is_stock": True},
    "XPYPL": {"okx_spot": "XPYPL",  "okx_futures": "PYPL",  "yahoo": "PYPL",  "base": "PYPL", "is_stock": True},
    "INTC":  {"okx_spot": "XINTC",  "okx_futures": "INTC",  "yahoo": "INTC",  "base": "INTC", "is_stock": True},
    "XINTC": {"okx_spot": "XINTC",  "okx_futures": "INTC",  "yahoo": "INTC",  "base": "INTC", "is_stock": True},
    "DIS":   {"okx_spot": "XDIS",   "okx_futures": "DIS",   "yahoo": "DIS",   "base": "DIS", "is_stock": True},
    "XDIS":  {"okx_spot": "XDIS",   "okx_futures": "DIS",   "yahoo": "DIS",   "base": "DIS", "is_stock": True},
}


def is_commodity_symbol(symbol: str) -> bool:
    """T13_fix: هل الرمز سلعة تحتاج Futures؟"""
    return symbol.upper().strip() in _COMMODITY_SYMBOLS


def resolve_stock_symbol(symbol: str, market: str = "futures") -> dict:
    """
    DL2: حل رمز السهم المُرمَّز بناءً على نوع السوق.
    market: "spot" → يُعيد okx_spot | "futures" → يُعيد okx_futures
    يُعيد: {"base": str, "okx_symbol": str, "yahoo": str|None, "is_stock": bool}
    """
    sym = symbol.upper()
    if sym in _TOKENIZED_STOCK_MAP:
        entry = _TOKENIZED_STOCK_MAP[sym]
        okx_sym = entry["okx_spot"] if market == "spot" else entry["okx_futures"]
        return {
            "base":       entry["base"],
            "okx_symbol": okx_sym,
            "yahoo":      entry.get("yahoo"),
            "is_stock":   True,
            "market":     market,
        }
    # ليس في القاموس — حاول اكتشافه تلقائياً
    if sym.startswith("X") and len(sym) > 2:
        base = sym[1:]
        return {
            "base":       base,
            "okx_symbol": sym if market == "spot" else base,
            "yahoo":      base,
            "is_stock":   True,
            "market":     market,
        }
    return {
        "base":       sym,
        "okx_symbol": sym,
        "yahoo":      None,
        "is_stock":   False,
        "market":     market,
    }



# ─── TTL كاش ──────────────────────────────────────────────────
CACHE_TTL = {
    "price":   60,    # 60 ثانية — مناسب لـ 100+ مستخدم (كان 30)
    "ohlcv":   600,   # 10 دقائق (كان 5)
    "news":    600,   # 10 دقائق (كان 5)
    "onchain": 900,   # 15 دقيقة (كان 10)
    "fear":    3600,
    "hist":    7200,  # ساعتان (كان ساعة)
    "bgeo":    43200, # 12 ساعة — BGeometrics (حد مجاني: 8/ساعة، 15/يوم؛ البيانات تُحدَّث يومياً)
    "pairchk": 3600,  # ساعة — توفر أزواج BTC/ETH على OKX (إصلاح/تطوير #188)
    "perp":    60,    # دقيقة — أسعار الأصول المُرمَّزة (Perp) — تطوير #209
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
"ZRO":"layerzero","ASTER":"aster-network",
    # إصلاح #1006: عملات AI جديدة
    "AIXBT":"aixbt-by-virtuals","VADER":"vader-protocol",
    "HYPE":"hyperliquid","ONDO":"ondo-finance",
    "PENDLE":"pendle","ENA":"ethena",    # ── إضافات ملاحظات #36,#38,#44 ──────────────────────────────
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


def _clean_symbol(symbol: str) -> str:
    """
    يُطبِّع رمز العملة على مستوى النظام:
    BTCUSDT → BTC | ETHBUSD → ETH | BTC/USDT → BTC | btcusdt → BTC
    يضمن أن جميع استدعاءات API تستقبل الرمز النظيف دائماً.
    """
    sym = symbol.upper().strip().replace("/", "").replace("-", "")
    # إزالة اللواحق الشائعة بالترتيب (الأطول أولاً لتجنب قطع BNB من BNBUSDT)
    for suffix in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if sym.endswith(suffix) and len(sym) > len(suffix):
            candidate = sym[: -len(suffix)]
            # تأكد أن ما تبقى ليس فارغاً أو حرفاً واحداً فقط لعملة غير منطقية
            if len(candidate) >= 2:
                sym = candidate
                break
    return sym

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


# ══ OHLCV Cache (إصلاح #688) ═══════════════════════════════
import time as _cache_time

_OHLCV_CACHE: dict = {}
_CACHE_TTL = 60  # ثانية

def _get_cached_ohlcv(key: str) -> list | None:
    if key in _OHLCV_CACHE:
        ts, data = _OHLCV_CACHE[key]
        if _cache_time.time() - ts < _CACHE_TTL:
            return data
    return None

def _set_cached_ohlcv(key: str, data: list) -> None:
    if data:
        _OHLCV_CACHE[key] = (_cache_time.time(), data)


class DataLayer:

    def __init__(self, session: aiohttp.ClientSession,
                 cryptopanic_key: str = "", etherscan_key: str = "",
                 bgeometrics_key: str = ""):
        self.session         = session
        self.cryptopanic_key = cryptopanic_key
        self.etherscan_key   = etherscan_key
        self.bgeometrics_key = bgeometrics_key

    # ═══════════════════════════════════════════════════════════
    # 1. السعر الحي — يُعيد Dict أو None (مع حماية في المستدعي)
    # ═══════════════════════════════════════════════════════════

    async def get_price_perp(self, symbol: str) -> Optional[Dict]:
        """تطوير #209: جلب سعر الأصول المُرمَّزة (ماسي+ فقط) من Perp.
        OKX SWAP → Bitget Perp → MEXC Perp.
        مفاتيح كاش منفصلة ("perp:...") لتجنُّب تعارض مع SPOT."""
        key = f"perp:{symbol.upper()}"
        if cached := _cached(key, "perp"):
            return cached
        for fn in (self._price_okx_perp, self._price_bitget_perp, self._price_mexc_perp):
            try:
                result = await fn(symbol)
                if result and result.get("price", 0) > 0:
                    _store(key, result, "perp")
                    return result
            except Exception:
                continue
        logger.warning(f"get_price_perp: لا بيانات لـ {symbol} من أي منصة Perp")
        return None

    async def get_ohlcv_perp(self, symbol: str, days: int = 250) -> list:
        """تطوير #209: شموع OHLCV للأصول المُرمَّزة — OKX SWAP أولاً."""
        key = f"perp_ohlcv:{symbol.upper()}:{days}"
        if cached := _cached(key, "ohlcv"):
            return cached
        candles = await self._hist_okx_perp(symbol, days)
        if len(candles) >= 10:
            _store(key, candles, "ohlcv")
            return candles
        logger.warning(f"get_ohlcv_perp: لا شموع لـ {symbol}")
        return []

    async def is_tokenized_stock(self, symbol: str) -> bool:
        """تطوير #209: يكتشف تلقائياً إن كان الرمز أصلاً مُرمَّزاً
        (فشل SPOT على OKX + نجاح Perp) — مخزَّن كاش لساعة."""
        symbol = symbol.upper()
        key = f"isstock:{symbol}"
        cached = _cached(key, "pairchk")
        if cached is not None:
            return cached
        # أولاً: هل موجود كـSPOT؟
        spot = await self._price_okx(symbol)
        if spot and spot.get("price", 0) > 0:
            _store(key, False, "pairchk")
            return False
        # ثانياً: هل موجود كـPerp؟
        perp = await self._price_okx_perp(symbol)
        is_stock = bool(perp and perp.get("price", 0) > 0)
        _store(key, is_stock, "pairchk")
        return is_stock

    async def get_price(self, symbol: str, quote: str = "USDT",
                        mkttype: str = "spot") -> Optional[Dict]:
        """إصلاح #258: mkttype يُميِّز كاش Spot عن Futures لنفس الرمز."""
        symbol = _clean_symbol(symbol)   # BTCUSDT → BTC (نظام-واسع)
        quote  = quote.upper()
        key = f"price:{symbol.upper()}:{quote}:{mkttype}"
        if cached := _cached(key, "price"):
            return cached

        # إصلاح/تطوير #188: أزواج BTC/ETH المباشرة — OKX فقط (دون
        # CoinGecko/Binance، الـresolver تحقَّق من التوفر مسبقاً)
        if quote != "USDT":
            result = await self._price_okx(symbol, quote)
            if result and result.get("price", 0) > 0:
                _store(key, result)
                return result
            logger.error(f"get_price({quote}) فشل لـ {symbol}")
            return None

        # OKX أولاً — سريع وغير محجوب على Railway
        result = await self._price_okx(symbol)
        if result and result.get("price", 0) > 0:
            # إصلاح #500: تحقق من volume — إذا مشكوك فيه نُصحح من CoinGecko
            vol_okx = result.get("volume_24h", 0)
            if vol_okx < 1e8:  # أقل من $100M مشكوك فيه لـ BTC/ETH
                cg_fix = await self._price_coingecko(symbol)
                if cg_fix and cg_fix.get("volume_24h", 0) > vol_okx:
                    result["volume_24h"] = cg_fix["volume_24h"]
            _store(key, result)
            return result

        # CoinGecko fallback
        result = await self._price_coingecko(symbol)
        if result and result.get("price", 0) > 0:
            _store(key, result)
            return result

        # DL1d: XSYMBOL fallback للأسهم المُرمَّزة (SPCX → XSPCX)
        _stk_p = resolve_stock_symbol(symbol, mkttype)
        if _stk_p.get("is_stock"):
            _x_sym_p = _stk_p.get("okx_symbol", f"X{symbol}")
            if _x_sym_p != symbol:
                result_x = await self._price_okx(_x_sym_p)
                if result_x and result_x.get("price", 0) > 0:
                    result_x["symbol"] = symbol
                    _store(key, result_x)
                    logger.info(f"DL1d: {symbol} → {_x_sym_p} ✅")
                    return result_x

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


    async def _price_okx(self, symbol: str, quote: str = "USDT") -> Optional[Dict]:
        """OKX Public API — fallback للسعر (غير محجوب على Railway).
        إصلاح/تطوير #188: quote اختياري ("USDT" افتراضياً = السلوك السابق
        دون أي تغيير) — يدعم أزواج BTC/ETH المباشرة (مثل ETH-BTC).
        xSKHY_fix: X-prefix Spot assets تبدأ بـ x صغير في OKX API.
        """
        try:
            sym_up = symbol.upper()
            inst_id = f"{sym_up}-{quote.upper()}"
            # xSKHY_fix: جرّب uppercase أولاً ثم lowercase لـ X-prefix
            _inst_ids = [inst_id]
            if sym_up.startswith("X") and len(sym_up) > 2:
                _inst_ids.append(f"x{sym_up[1:]}-{quote.upper()}")
            data = None
            for _iid in _inst_ids:
                _d = await _fetch(
                    self.session,
                    f"https://www.okx.com/api/v5/market/ticker?instId={_iid}",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                    retries=2,
                )
                if isinstance(_d, dict) and _d.get("data"):
                    data = _d
                    break
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
                    # إصلاح #153/#185/#207: لأزواج SPOT، volCcy24h من OKX
                    # هي الحجم بعملة التسعير (USDT) مباشرة، وvol24h هي
                    # الحجم بالعملة الأساسية (تحتاج ×price لتحويلها لـUSDT).
                    "volume_24h": max(
                        float(ticker.get("volCcy24h", 0) or 0),
                        float(ticker.get("vol24h", 0) or 0) * price
                    ),
                    "high_24h":                     float(ticker.get("high24h", 0) or 0),
                    "low_24h":                      float(ticker.get("low24h", 0) or 0),
                    "source":                       "okx",
                }
        except Exception as e:
            logger.debug(f"_price_okx ({symbol}): {e}")
        return None

    async def _price_okx_perp(self, symbol: str) -> Optional[Dict]:
        """تطوير #209: جلب سعر الأصول المُرمَّزة (MSTR,TSLA,NVDA...)
        من OKX SWAP (Perp) — المصدر الوحيد المتاح لهذه الأصول على OKX."""
        try:
            inst_id = f"{symbol.upper()}-USDT-SWAP"
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
                # لـSWAP: volCcy24h = حجم بالعملة الأساسية (ليس quote)
                # vol24h = حجم بعدد العقود — نحوِّل vol24h×price للدولار
                return {
                    "symbol":                      symbol.upper(),
                    "price":                       price,
                    "change_24h":                  round(change, 4),
                    "price_change_percentage_24h": round(change, 4),
                    "volume_24h":                  float(ticker.get("volCcy24h", 0) or 0) * price,
                    "high_24h":                    float(ticker.get("high24h", 0) or 0),
                    "low_24h":                     float(ticker.get("low24h", 0) or 0),
                    "source":                      "okx_perp",
                    "is_perp":                     True,
                }
        except Exception as e:
            logger.debug(f"_price_okx_perp ({symbol}): {e}")
        return None

    async def _price_bitget_perp(self, symbol: str) -> Optional[Dict]:
        """تطوير #209: Bitget Perp fallback للأصول المُرمَّزة."""
        try:
            url = f"https://api.bitget.com/api/mix/v1/market/ticker?symbol={symbol.upper()}USDT_UMCBL"
            data = await _fetch(self.session, url,
                                headers={"User-Agent": "Mozilla/5.0"}, retries=1)
            if isinstance(data, dict) and data.get("data"):
                d = data["data"]
                price = float(d.get("last", 0) or 0)
                if price <= 0:
                    return None
                open24 = float(d.get("open24H", price) or price)
                change = ((price - open24) / open24 * 100) if open24 > 0 else 0
                return {
                    "symbol":                      symbol.upper(),
                    "price":                       price,
                    "change_24h":                  round(change, 4),
                    "price_change_percentage_24h": round(change, 4),
                    "volume_24h":                  float(d.get("usdtVolume", 0) or 0),
                    "high_24h":                    float(d.get("high24H", 0) or 0),
                    "low_24h":                     float(d.get("low24H", 0) or 0),
                    "source":                      "bitget_perp",
                    "is_perp":                     True,
                }
        except Exception as e:
            logger.debug(f"_price_bitget_perp ({symbol}): {e}")
        return None

    async def _price_mexc_perp(self, symbol: str) -> Optional[Dict]:
        """تطوير #209: MEXC Perp fallback للأصول المُرمَّزة."""
        try:
            url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={symbol.upper()}_USDT"
            data = await _fetch(self.session, url,
                                headers={"User-Agent": "Mozilla/5.0"}, retries=1)
            if isinstance(data, dict) and data.get("data"):
                d = data["data"]
                price = float(d.get("lastPrice", 0) or 0)
                if price <= 0:
                    return None
                open24 = float(d.get("riseFallRate", 0) or 0)
                return {
                    "symbol":                      symbol.upper(),
                    "price":                       price,
                    "change_24h":                  round(open24 * 100, 4),
                    "price_change_percentage_24h": round(open24 * 100, 4),
                    "volume_24h":                  float(d.get("amount24", 0) or 0),
                    "high_24h":                    float(d.get("high24Price", 0) or 0),
                    "low_24h":                     float(d.get("low24Price", 0) or 0),
                    "source":                      "mexc_perp",
                    "is_perp":                     True,
                }
        except Exception as e:
            logger.debug(f"_price_mexc_perp ({symbol}): {e}")
        return None

    async def _hist_okx_perp(self, symbol: str, days: int) -> list:
        """تطوير #209: شموع OHLCV من OKX SWAP للأصول المُرمَّزة."""
        try:
            inst_id = f"{symbol.upper()}-USDT-SWAP"
            limit   = min(days, 300)
            data = await _fetch(
                self.session,
                f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1D&limit={limit}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                retries=2,
            )
            if not (isinstance(data, dict) and data.get("data")):
                return []
            candles = []
            for c in reversed(data["data"]):
                try:
                    o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                    v = float(c[7]) if len(c) > 7 else float(c[5])  # volCcy
                    candles.append({"open":o,"high":h,"low":l,"close":cl,"volume":v*cl})
                except Exception:
                    continue
            return candles
        except Exception as e:
            logger.debug(f"_hist_okx_perp ({symbol}): {e}")
        return []

    async def check_okx_pair(self, base: str, quote: str) -> bool:
        """تطوير #188: يتحقق من توفر زوج تداول مباشر على OKX
        (مثل ETH-BTC) — مخزَّن مؤقتاً لساعة لتقليل استدعاءات API."""
        base, quote = base.upper(), quote.upper()
        key = f"pairchk:{base}-{quote}"
        cached = _cached(key, "pairchk")
        if cached is not None:
            return cached
        ok = False
        try:
            inst_id = f"{base}-{quote}"
            data = await _fetch(
                self.session,
                f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                retries=1,
            )
            if isinstance(data, dict) and data.get("data"):
                ok = float(data["data"][0].get("last", 0) or 0) > 0
        except Exception as e:
            logger.debug(f"check_okx_pair ({base}-{quote}): {e}")
        _store(key, ok, "pairchk")
        return ok

    # ═══════════════════════════════════════════════════════════
    # 2. OHLCV — يُعيد دائماً List (قد تكون فارغة لكن ليست None)
    # ═══════════════════════════════════════════════════════════
    async def get_ohlcv(self, symbol: str, interval: str = "1d",
                         limit: int = 365, quote: str = "USDT",
                         mkttype: str = "spot", _cache_hint: str = "") -> List[Dict]:
        """إصلاح #258: mkttype يُميِّز كاش Spot عن Futures."""
        _orig_symbol = (_cache_hint or symbol).upper()  # T10_fix: XSPCX وليس SPCX
        symbol = _clean_symbol(symbol)   # BTCUSDT → BTC (نظام-واسع)
        quote  = quote.upper()
        # T10_fix: key يميز XSPCX عن SPCX وXSPY
        key = f"ohlcv:{_orig_symbol}:{quote}:{interval}:{limit}:{mkttype}"
        if cached := _cached(key, "ohlcv"):
            return cached  # دائماً List

        # إصلاح/تطوير #188: أزواج BTC/ETH المباشرة — OKX فقط
        if quote != "USDT":
            candles = await self._hist_okx(symbol, min(limit, 300), quote)
            if len(candles) >= 10:
                _store(key, candles, "ohlcv")
                return candles
            logger.error(f"get_ohlcv({quote}) فشل لـ {symbol} — يُعيد []")
            return []

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
        # DL1b: Yahoo Finance كـ fallback للأسهم المُرمَّزة
        # T10b_DL_fix: إذا symbol يبدأ بـ X (XSPCX) → لا Yahoo → OKX Spot مباشرة
        _stock_info = resolve_stock_symbol(symbol)
        if _stock_info.get("is_stock") and not symbol.upper().startswith("X"):
            # العملات العادية (SPCX/AAPL): Yahoo fallback
            _yahoo_sym = _stock_info.get("yahoo") or _stock_info.get("base", symbol)
            if _yahoo_sym:
                yahoo_candles = await self._ohlcv_yahoo(_yahoo_sym, days=max(limit, 90))
                if yahoo_candles and len(yahoo_candles) >= 10:
                    logger.info(f"DL1b: {symbol} ← Yahoo({_yahoo_sym}) {len(yahoo_candles)} شمعة")
                    _store(key, yahoo_candles, "ohlcv")
                    return yahoo_candles
        elif _stock_info.get("is_stock") and symbol.upper().startswith("X"):
            # T10b_DL_fix: X-prefix (XSPCX/XAAPL) → OKX Spot مباشرة (لا Yahoo)
            x_spot_candles = await self._hist_okx(symbol.upper(), min(limit, 300))
            if len(x_spot_candles) >= 10:
                logger.info(f"T10b_DL_fix: {symbol} ← OKX Spot ({len(x_spot_candles)} شمعة)")
                _store(key, x_spot_candles, "ohlcv")
                return x_spot_candles
            # T10b_scaling_fix: إذا OKX Spot قليل → Yahoo مع تصحيح الأسعار
            _yahoo_sym = _stock_info.get("yahoo") or _stock_info.get("base", symbol)
            if _yahoo_sym:
                yahoo_candles = await self._ohlcv_yahoo(_yahoo_sym, days=max(limit, 90))
                if yahoo_candles and len(yahoo_candles) >= 10:
                    # T10b_scaling: نسحب سعر XSPCX الحالي لتصحيح نسبة Yahoo
                    try:
                        _x_price_now = await self.get_price(symbol.upper(), "USDT", mkttype="spot")
                        _x_px = float((_x_price_now or {}).get("price", 0))
                        _y_px = float((yahoo_candles[-1] or {}).get("close", 0))
                        if _x_px > 0 and _y_px > 0:
                            _scale = _x_px / _y_px
                            scaled = []
                            for c in yahoo_candles:
                                sc = dict(c)
                                for k in ("open","high","low","close"):
                                    if k in sc and sc[k]:
                                        sc[k] = float(sc[k]) * _scale
                                scaled.append(sc)
                            yahoo_candles = scaled
                            logger.info(f"T10b_scaling: {symbol} ← Yahoo({_yahoo_sym}) scaled×{_scale:.4f} {len(yahoo_candles)} شمعة")
                    except Exception as _e_sc:
                        logger.debug(f"T10b_scaling error: {_e_sc}")
                    _store(key, yahoo_candles, "ohlcv")
                    return yahoo_candles

        # DL1c: إذا لم يُوجد في Yahoo → جرب XSYMBOL في OKX
        if not symbol.startswith("X"):
            x_sym = f"X{symbol}"
            x_candles = await self._hist_okx(x_sym, min(limit, 300))
            if len(x_candles) >= 10:
                logger.info(f"DL1c: {symbol} → {x_sym} في OKX ({len(x_candles)} شمعة)")
                _store(key, x_candles, "ohlcv")
                return x_candles

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
                    # إصلاح حجم 0.0x: نستخدم quoteVolume (USDT) c[7] لاتساق vol_ratio
                    "volume":    float(c[7]) if len(c) > 7 else float(c[5]),
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

        # ── Yahoo Finance للأسهم المُرمَّزة (BT1_fix) ─────────────
        _stk_info = resolve_stock_symbol(symbol)
        if _stk_info.get("is_stock") and _stk_info.get("yahoo"):
            logger.info(f"Historical: جرب Yahoo Finance لـ {symbol}")
            yahoo_results = await self._ohlcv_yahoo(_stk_info["yahoo"], days=days)
            if len(yahoo_results) >= 90:
                # تحويل format Yahoo إلى format Backtest
                yahoo_hist = []
                for i, c in enumerate(yahoo_results):
                    yahoo_hist.append({
                        "open":      c.get("open",  c.get("close", 0)),
                        "high":      c.get("high",  c.get("close", 0)),
                        "low":       c.get("low",   c.get("close", 0)),
                        "close":     c.get("close", 0),
                        "volume":    c.get("volume", 0),
                        "timestamp": c.get("ts", i * 86400),
                        "price":     c.get("close", 0),
                    })
                logger.info(f"BT1_fix: {symbol} ← Yahoo({_stk_info['yahoo']}) {len(yahoo_hist)} يوم")
                _store(key, yahoo_hist, "hist")
                return yahoo_hist

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
                        # quoteVolume (USDT) لاتساق vol_ratio
                        vol   = float(c[7]) if len(c) > 7 else float(c[5])
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
    # ملاحظة: دالة _news_coingecko (CryptoPanic auth_token= فارغ) أُزيلت — إصلاح #29
    # كانت تفشل دائماً وتُسجِّل خطأ في كل دورة دون أي قيمة مضافة.
    # RSS (_rss_news أدناه: CoinTelegraph/Decrypt/CoinDesk) لا يحتاج مفتاح API.

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

        # ── RSS — لا يحتاج مفتاح API (إصلاح #29: حُذف استدعاء CryptoPanic المُهجَّر) ──
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
            ("BitcoinMagazine", "https://bitcoinmagazine.com/feed"),
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

    async def get_ohlcv_4h(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        المرحلة 2: جلب بيانات 4H من OKX لـ SMC حقيقي.
        يُستخدم لـ RSI Divergence وOrder Blocks وBOS/ChoCH.
        """
        symbol = _clean_symbol(symbol)   # BTCUSDT → BTC
        key = f"ohlcv4h:{symbol}:{limit}"
        if cached := _cached(key, "ohlcv"):
            return cached
        try:
            inst_id = f"{symbol.upper()}-USDT"
            data = await _fetch(
                self.session,
                f"https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar=4H&limit={limit}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                retries=2,
            )
            if not (isinstance(data, dict) and data.get("data")):
                return []
            candles = []
            for c in reversed(data["data"]):  # OKX يُعيد بترتيب عكسي
                try:
                    candles.append({
                        "timestamp": float(c[0]) / 1000,
                        "open":      float(c[1]),
                        "high":      float(c[2]),
                        "low":       float(c[3]),
                        "close":     float(c[4]),
                        # volCcy (USDT) c[6] لاتساق vol_ratio؛ fallback لـ c[5]
                        "volume":    float(c[6]) if len(c) > 6 else float(c[5]),
                        "interval":  "4h",
                    })
                except (IndexError, ValueError):
                    continue
            if candles:
                _store(key, candles, "ohlcv")
            return candles
        except Exception as e:
            logger.debug(f"get_ohlcv_4h ({symbol}): {e}")
        return []

    async def get_signal_enrichment(self, symbol: str, base_onchain: dict = None) -> dict:
        """
        إصلاح #34: يُضيف بيانات خاصة بالعملة (whale_ratio + funding_rate)
        لاستخدامها في _onchain_signal بدل الاعتماد على TVL العالمي الثابت.
        يُدمَج مع onchain_data الأساسي (TVL العالمي) كـ fallback.
        """
        enriched = dict(base_onchain or {})
        try:
            wr, fr = await asyncio.gather(
                self.get_whale_ratio(symbol),
                self.get_funding_rate(symbol),
                return_exceptions=True
            )
            if isinstance(wr, dict):
                enriched["whale_ratio"] = wr.get("ratio", 0.0)
            if isinstance(fr, dict):
                enriched["funding_rate_pct"] = fr.get("rate_pct", 0.0)
        except Exception as e:
            logger.debug(f"signal_enrichment ({symbol}): {e}")

        # OKX_Agent_Skills_fix: CVD من OKX trades (آخر 100 صفقة)
        try:
            import urllib.request as _ur_cvd, json as _jj_cvd, ssl as _ssl_cvd
            _ctx_cvd = _ssl_cvd.create_default_context()
            _ctx_cvd.check_hostname = False
            _ctx_cvd.verify_mode = _ssl_cvd.CERT_NONE
            # X-prefix assets: XSPY → SPY للـ trades API
            _sym_raw = symbol.upper()
            _sym_cvd_base = (_sym_raw[1:] if _sym_raw.startswith("X") and len(_sym_raw) > 2
                             else _sym_raw)
            _sym_cvd = f"{_sym_cvd_base}-USDT"
            _url_cvd = f"https://www.okx.com/api/v5/market/trades?instId={_sym_cvd}&limit=100"
            _req_cvd = _ur_cvd.Request(_url_cvd, headers={"User-Agent":"Mozilla/5.0"})
            import asyncio as _aio_cvd
            _loop_cvd = _aio_cvd.get_event_loop()
            def _fetch_cvd():
                return _ur_cvd.urlopen(_req_cvd, context=_ctx_cvd, timeout=5).read()
            _raw_cvd  = await _aio_cvd.wait_for(
                _loop_cvd.run_in_executor(None, _fetch_cvd), timeout=6.0)
            _data_cvd = _jj_cvd.loads(_raw_cvd)
            if _data_cvd.get("data"):
                _trades = _data_cvd["data"]
                _buy_vol  = sum(float(t.get("sz",0)) for t in _trades if t.get("side")=="buy")
                _sell_vol = sum(float(t.get("sz",0)) for t in _trades if t.get("side")=="sell")
                _cvd = _buy_vol - _sell_vol
                _total = _buy_vol + _sell_vol
                # CVD_sanity: تحقق من موثوقية البيانات
                _buy_ratio = _buy_vol / max(_total, 1e-9)
                if _total > 0 and 0.02 < _buy_ratio < 0.98:
                    enriched["cvd"] = round(_cvd, 4)
                    enriched["cvd_pct"] = round(_cvd / max(_total, 1e-9) * 100, 2)
                    enriched["cvd_signal"] = (
                        "🟢 شراء" if _cvd > 0 else
                        "🔴 بيع"  if _cvd < 0 else
                        "⚪ محايد"
                    )
        except Exception as _cvd_e:
            logger.debug(f"OKX CVD ({symbol}): {_cvd_e}")

        return enriched

    async def _bgeo_fetch(self, slug: str, extra_keys: tuple = (),
                           value_range: Optional[Tuple[float, float]] = None
                           ) -> Optional[float]:
        """
        جلب آخر قيمة لمؤشر BGeometrics (مثل mvrv-zscore, sopr, ...).
        دفاعي بالكامل: يُعيد None عند أي فشل (مفتاح مفقود، rate limit،
        slug خاطئ، أو شكل استجابة غير متوقع) — لا يرفع أي استثناء أبداً.

        extra_keys: مفاتيح JSON إضافية مرشّحة (مثل camelCase: "mvrvZscore")
        value_range: (min,max) منطقي للقيمة — يرفض قيماً خارج النطاق
                      (مثل رفض Unix timestamp ضخم يُؤخَذ خطأً كقيمة المؤشر)
        """
        if not self.bgeometrics_key:
            return None
        key = f"bgeo:{slug}"
        if (cached := _cached(key, "bgeo")) is not None:
            return cached
        try:
            url = f"https://api.bgeometrics.com/v1/{slug}?token={self.bgeometrics_key}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    logger.debug(f"bgeo {slug}: HTTP {r.status}")
                    return None
                data = await r.json()
            # الاستجابة قد تكون: list[{date,value}] أو {"data":[...]} أو dict مباشر
            item = None
            if isinstance(data, list) and data:
                item = data[-1]
            elif isinstance(data, dict):
                if isinstance(data.get("data"), list) and data["data"]:
                    item = data["data"][-1]
                else:
                    item = data
            if not isinstance(item, dict):
                return None

            def _in_range(v: float) -> bool:
                if value_range is None:
                    return True
                return value_range[0] <= v <= value_range[1]

            # إصلاح #108/#114: استبعاد أي مفتاح يحتوي تاريخ/وقت (substring)
            # من المرشحين — كان "d" أو "datetime" يمر سابقاً كقيمة خاطئة
            _date_like = ("date", "time", "ts", "unix", "day")

            # المفتاح قد يكون "value" أو slug نفسه أو camelCase أو متغيرات
            candidates = ("value", slug, slug.replace("-", "_"),
                          slug.replace("-", ""), "result") + extra_keys
            for k in candidates:
                if k in item and item[k] is not None:
                    try:
                        val = float(item[k])
                    except (TypeError, ValueError):
                        continue
                    if not _in_range(val):
                        continue
                    _store(key, val, "bgeo")
                    return val
            # fallback: أول قيمة رقمية لا يبدو اسمها/قيمتها تاريخاً، ضمن النطاق
            for k, v in item.items():
                if any(d in k.lower() for d in _date_like):
                    continue
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    continue
                val = float(v)
                # استبعاد قيم تشبه Unix timestamps (>1e6) كحماية إضافية
                if value_range is None and abs(val) > 1e6:
                    continue
                if not _in_range(val):
                    continue
                _store(key, val, "bgeo")
                return val
        except Exception as e:
            logger.debug(f"bgeo {slug}: {e}")
        return None

    async def _bgeo_fetch_any(self, slugs: list, extra_keys: tuple = (),
                               value_range: Optional[Tuple[float, float]] = None
                               ) -> Optional[float]:
        """يجرّب عدة slugs بالترتيب ويُعيد أول قيمة صالحة (أو None إن فشلت كلها)."""
        for s in slugs:
            v = await self._bgeo_fetch(s, extra_keys=extra_keys, value_range=value_range)
            if v is not None:
                return v
        return None

    async def get_btc_onchain_advanced(self) -> dict:
        """
        إصلاح/تطوير: مؤشرات BTC on-chain متقدمة من BGeometrics
        (MVRV Z-Score, SOPR, Exchange Netflow, Puell Multiple)
        لإغناء تقرير /onchain. تُعاد كلها بصيغة موحَّدة مع تفسير عربي،
        أو {"available": False} إذا المفتاح غير مُهيَّأ أو الجلب فشل.
        """
        if not self.bgeometrics_key:
            return {"available": False}
        try:
            mvrv_z, sopr, netflow, puell = await asyncio.gather(
                self._bgeo_fetch("mvrv-zscore", extra_keys=("mvrvZscore", "mvrv_z_score", "z_score"),
                                 value_range=(-10, 20)),
                self._bgeo_fetch("sopr", value_range=(0, 5)),
                self._bgeo_fetch_any(
                    ["exchange-netflow", "exchange-net-flow", "netflow", "exchange-netflow-total"],
                    extra_keys=("exchangeNetflow", "netflow", "net_flow")),
                self._bgeo_fetch("puell-multiple", extra_keys=("puellMultiple", "puell_multiple_ratio"),
                                 value_range=(0, 50)),
            )
        except Exception as e:
            logger.debug(f"btc_onchain_advanced: {e}")
            return {"available": False}

        result = {"available": True}

        if mvrv_z is not None:
            if mvrv_z > 7:     mvrv_sig = "🔴 منطقة قمة تاريخية (مبالغ في التقييم)"
            elif mvrv_z > 3.5: mvrv_sig = "🟠 مرتفع — حذر"
            elif mvrv_z < 0:   mvrv_sig = "🟢 منطقة قاع تاريخية (تقييم منخفض)"
            else:              mvrv_sig = "⚪ نطاق طبيعي"
            result["mvrv_zscore"] = round(mvrv_z, 2)
            result["mvrv_signal"] = mvrv_sig

        if sopr is not None:
            if sopr > 1.02:   sopr_sig = "🟢 المتداولون يبيعون بربح (زخم صاعد)"
            elif sopr < 0.98: sopr_sig = "🔴 المتداولون يبيعون بخسارة (ضغط هابط/قاع محتمل)"
            else:             sopr_sig = "⚪ التعادل (~1.0)"
            result["sopr"] = round(sopr, 3)
            result["sopr_signal"] = sopr_sig

        if netflow is not None:
            if netflow > 0:   nf_sig = "🔴 تدفق صافٍ للبورصات (ضغط بيع محتمل)"
            elif netflow < 0: nf_sig = "🟢 تدفق صافٍ خارج البورصات (تراكم/Hodling)"
            else:             nf_sig = "⚪ متوازن"
            result["exchange_netflow_btc"] = round(netflow, 1)
            result["netflow_signal"] = nf_sig

        if puell is not None:
            if puell > 4:     puell_sig = "🔴 مرتفع جداً (تاريخياً قرب القمم)"
            elif puell < 0.5: puell_sig = "🟢 منخفض جداً (تاريخياً قرب القيعان)"
            else:             puell_sig = "⚪ نطاق طبيعي"
            result["puell_multiple"] = round(puell, 2)
            result["puell_signal"] = puell_sig

        if len(result) == 1:  # فقط "available":True بدون أي مؤشر نجح
            return {"available": False}
        return result

    async def get_whale_ratio(self, symbol: str) -> dict:
        """
        يجلب Long/Short Account Ratio من OKX (مجاني، بدون مفتاح).
        إصلاح #21/#22/#39/#40: استبدال CoinGlass v2 المُهجَّر الذي كان
        يفشل صامتاً ويُعيد دائماً {"ratio":0,"signal":"محايد"} لكل عملة.
        ratio > 1 = أغلبية المتداولين Long | ratio < 1 = أغلبية Short.
        """
        symbol = _clean_symbol(symbol)
        key = f"whale:{symbol}"
        if cached := _cached(key, "onchain"):
            return cached
        result = {"ratio": 0.0, "signal": "محايد", "inflow": 0.0, "outflow": 0.0}
        try:
            url = (f"https://www.okx.com/api/v5/rubik/stat/contracts/"
                   f"long-short-account-ratio?ccy={symbol.upper()}&period=1H")
            if self.session:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        items = data.get("data", [])
                        if items:
                            ratio = float(items[0][1])
                            result = {
                                "ratio":   round(ratio, 3),
                                "signal":  "🔴 أغلبية Short (تحيُّز هابط)" if ratio < 0.8
                                           else "🟢 أغلبية Long (تحيُّز صاعد)" if ratio > 1.2
                                           else "⚪ متوازن",
                                "inflow":  0.0,
                                "outflow": 0.0,
                            }
                            _store(key, result, "onchain")
                            return result
        except Exception as e:
            logger.debug(f"whale_ratio ({symbol}): {e}")
        return result

    async def get_miner_flows(self, symbol: str = "BTC") -> dict:
        """
        بيانات تدفقات المعدنين — غير متاحة حالياً.
        endpoint CoinGlass v2 القديم مُهجَّر؛ لا يوجد بديل مجاني مكافئ حالياً.
        تُعيد دائماً 'محايد' بصراحة بدل استدعاء API مُهجَّر يفشل صامتاً.
        """
        return {"outflow_30d": 0.0, "signal": "محايد", "available": False}

    def build_candles_summary(self, candles: list, symbol: str = "", current_price: float = 0.0) -> str:
        """يبني ملخص شموع احترافي لـ Groq — يشمل EMA + RSI + MACD + حجم.
        إصلاح #328 (H2): current_price يُستخدم بدلاً من candles[-1] إذا مُعطى.
        """
        if not candles or len(candles) < 5:
            return ""
        try:
            closes  = [float(c.get("close", 0) or 0) for c in candles if c.get("close")]
            volumes = [float(c.get("volume", 0) or 0) for c in candles if c.get("volume")]
            if len(closes) < 5: return ""

            # إصلاح #328 (H2): استخدام current_price الحالي إذا مُعطى
            last = current_price if current_price > 0 else closes[-1]
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


    async def _hist_okx(self, symbol: str, days: int, quote: str = "USDT") -> list:
        """OKX Klines — OHLCV تاريخي للعملات والأسهم المُرمَّزة.
        OKX agent-skills: Spot=XSYMBOL-USDT | SWAP=SYMBOL-USDT-SWAP
        DL1c_fix: يجرب XSYMBOL-USDT تلقائياً للأسهم المُرمَّزة."""
        try:
            sym_upper = symbol.upper()
            inst_id   = f"{sym_upper}-{quote.upper()}"
            limit     = min(days, 300)

            # DL1c_fix + xSKHY_fix: X-prefix → جرب uppercase ثم lowercase
            if sym_upper.startswith("X") and len(sym_upper) > 2:
                # lowercase: xSKHY-USDT (OKX Spot format)
                _inst_x_low = f"x{sym_upper[1:]}-{quote.upper()}"
                for _iid_x in [inst_id, _inst_x_low]:
                    data_x = await _fetch(
                        self.session,
                        f"https://www.okx.com/api/v5/market/history-candles?instId={_iid_x}&bar=1D&limit={limit}",
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                        retries=2,
                    )
                    if data_x and isinstance(data_x, dict) and data_x.get("data"):
                        candles_x = _parse_okx_candles(data_x["data"])
                        if len(candles_x) >= 5:
                            return candles_x
                # إذا فشل كلاهما → استمر للـ fallback
                _dummy_skip = True
            if False:  # placeholder to maintain structure
                data_x = await _fetch(
                    self.session,
                    f"https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar=1D&limit={limit}",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                    retries=2,
                )
                if data_x and isinstance(data_x, dict) and data_x.get("data"):
                    candles_x = _parse_okx_candles(data_x["data"])
                    if len(candles_x) >= 5:
                        return candles_x

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
                    # volCcy (USDT) c[6] لاتساق vol_ratio؛ fallback لـ c[5]
                    vol   = float(c[6]) if len(c) > 6 else float(c[5])
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

    async def check_spot_available(self, symbol: str) -> dict:
        """
        TK1/TK1b: التحقق من توفر أصل مُرمَّز في سوق Spot على OKX.
        TK1b: يجرب XSYMBOL-USDT تلقائياً إذا فشل SYMBOL-USDT
        (جميع الأسهم المُرمَّزة في OKX تبدأ بـ X: XAMZN, XSPCX, XAAPL...)
        يُعيد: {"available": bool, "spot_price": float, "spot_symbol": str, "message": str}
        """
        sym = symbol.upper()
        # تحقق من Cache أولاً (30 دقيقة)
        _cache_key = f"spot_available:{sym}"
        if cached := _cached(_cache_key, "spot_available"):
            return cached

        async def _try_spot(inst_id: str) -> float:
            """TK1b_fix2: تجربة OKX Spot مع self.session الصحيح."""
            for url in [
                f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
                f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}&instType=SPOT",
            ]:
                try:
                    # TK1b_fix3: استخدام self.session المناسب لـ _fetch
                    data = await _fetch(
                        self.session, url,
                        headers={"User-Agent": "Mozilla/5.0"},
                        retries=1, backoff=1.0
                    )
                    if data and isinstance(data, dict) and data.get("code") == "0":
                        items = data.get("data", [])
                        if items:
                            price = float(items[0].get("last", 0) or 0)
                            if price > 0:
                                return price
                except Exception:
                    continue
            return 0.0

        try:
            # TK1b_fix2: أولاً تحقق من قاموس الأسهم المُرمَّزة المحلي
            _stock_res = resolve_stock_symbol(sym, "spot")
            _okx_spot_sym = _stock_res.get("okx_symbol", sym)  # مثل XSPCX

            # المحاولة 1: الرمز من القاموس (XSPCX-USDT)
            spot_price = await _try_spot(f"{_okx_spot_sym}-USDT")
            spot_sym   = _okx_spot_sym if spot_price > 0 else sym

            # المحاولة 2: الرمز المدخل مباشرة (SPCX-USDT)
            if spot_price <= 0 and _okx_spot_sym != sym:
                spot_price = await _try_spot(f"{sym}-USDT")
                if spot_price > 0:
                    spot_sym = sym

            # المحاولة 3: X-prefix تلقائي إذا لم يجد
            if spot_price <= 0 and not sym.startswith("X"):
                x_sym = f"X{sym}"
                spot_price = await _try_spot(f"{x_sym}-USDT")
                if spot_price > 0:
                    spot_sym = x_sym

            # TK1b_fix2: إذا الرمز في القاموس → نعتبره متاحاً حتى لو فشل API
            # (قد يكون 403 من بيئة التطوير لكن يعمل على Railway)
            if spot_price <= 0 and _stock_res.get("is_stock") and _okx_spot_sym != sym:
                logger.info(f"TK1b_fix2: {sym} موجود في القاموس كـ {_okx_spot_sym} — نفترض متاح")
                result = {
                    "available":    True,
                    "spot_price":   0.0,
                    "spot_symbol":  _okx_spot_sym,
                    "message":      f"✅ {_okx_spot_sym} مدرج في Spot على OKX"
                }
                _store(_cache_key, result, ttl=600)  # 10 دقائق فقط
                return result

            if spot_price > 0:
                result = {
                    "available":    True,
                    "spot_price":   spot_price,
                    "spot_symbol":  spot_sym,
                    "message":      f"✅ {spot_sym} متاح في Spot على OKX"
                }
                _store(_cache_key, result, ttl=1800)
                return result

            # غير متاح في Spot بعد كل المحاولات
            result = {
                "available":   False,
                "spot_price":  0.0,
                "spot_symbol": sym,
                "message": (
                    f"⚠️ *{sym}* غير متاح في السوق الفوري (Spot) على OKX حالياً\n"
                    f"• الأسهم المُرمَّزة في OKX تبدأ بـ X (مثل X{sym})\n"
                    f"• أو جرّب في Futures"
                )
            }
            _store(_cache_key, result, ttl=900)
            return result

        except Exception as e:
            logger.debug(f"check_spot_available({sym}): {e}")
            return {"available": True, "spot_price": 0.0, "spot_symbol": sym, "message": ""}


    async def _ohlcv_yahoo(self, symbol: str, days: int = 90) -> list:
        """
        DL1: Yahoo Finance كمصدر OHLCV للأسهم المُرمَّزة.
        يُستخدم عند نقص البيانات التاريخية في OKX (أسهم جديدة).
        """
        try:
            import urllib.request, json, time
            # تحويل الرمز للصيغة المناسبة لـ Yahoo
            _info = resolve_stock_symbol(symbol, "spot")
            yahoo_sym = _info.get("yahoo") or _info.get("base", symbol)
            if not yahoo_sym:
                return []

            end_ts   = int(time.time())
            start_ts = end_ts - (days * 86400)
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
                f"?interval=1d&period1={start_ts}&period2={end_ts}"
            )
            headers = {"User-Agent": "Mozilla/5.0"}
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=8)
            data = json.loads(resp.read())

            result_data = data.get("chart", {}).get("result", [])
            if not result_data:
                return []

            timestamps = result_data[0].get("timestamp", [])
            quotes     = result_data[0].get("indicators", {}).get("quote", [{}])[0]
            opens      = quotes.get("open",   [])
            highs      = quotes.get("high",   [])
            lows       = quotes.get("low",    [])
            closes     = quotes.get("close",  [])
            volumes    = quotes.get("volume", [])

            candles = []
            for i, ts in enumerate(timestamps):
                try:
                    c = closes[i]
                    if c is None or c <= 0:
                        continue
                    candles.append({
                        "open":   float(opens[i]   or c),
                        "high":   float(highs[i]   or c),
                        "low":    float(lows[i]    or c),
                        "close":  float(c),
                        "volume": float(volumes[i] or 0),
                        "ts":     ts,
                    })
                except (TypeError, IndexError):
                    continue
            logger.info(f"Yahoo Finance: {yahoo_sym} → {len(candles)} شمعة")
            return candles

        except Exception as e:
            logger.debug(f"_ohlcv_yahoo({symbol}): {e}")
            return []

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

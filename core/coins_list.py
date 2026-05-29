"""
قائمة Top 500 عملة — رائد التداول الذكي
القائمة ثابتة مع تحديث شهري تلقائي من CoinGecko/CoinMarketCap
آخر تحديث: 2026-05-29
"""

import json
import logging
import os
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# ─── حدود الباقات ─────────────────────────────────────────────────────────────
TIER_LIMITS = {
    "free":    50,    # أول 50 عملة
    "silver":  90,    # أول 90 عملة
    "gold":    150,   # أول 150 عملة
    "diamond": 500,   # أول 500 عملة
    "admin":   9999,  # جميع العملات بلا حدود
}

# ─── القائمة الثابتة (تُحدَّث شهرياً) ─────────────────────────────────────────
# الترتيب: (رقم الترتيب، الرمز، CoinGecko ID)
COINS_RANKED = [
    (  1, "BTC",   "bitcoin"),
    (  2, "ETH",   "ethereum"),
    (  3, "BNB",   "binancecoin"),
    (  4, "SOL",   "solana"),
    (  5, "XRP",   "ripple"),
    (  6, "DOGE",  "dogecoin"),
    (  7, "TON",   "the-open-network"),
    (  8, "ADA",   "cardano"),
    (  9, "TRX",   "tron"),
    ( 10, "AVAX",  "avalanche-2"),
    ( 11, "SHIB",  "shiba-inu"),
    ( 12, "LINK",  "chainlink"),
    ( 13, "DOT",   "polkadot"),
    ( 14, "BCH",   "bitcoin-cash"),
    ( 15, "NEAR",  "near"),
    ( 16, "LTC",   "litecoin"),
    ( 17, "APT",   "aptos"),
    ( 18, "UNI",   "uniswap"),
    ( 19, "ICP",   "internet-computer"),
    ( 20, "PEPE",  "pepe"),
    ( 21, "POL",   "polygon-ecosystem-token"),
    ( 22, "ETC",   "ethereum-classic"),
    ( 23, "STX",   "blockstack"),
    ( 24, "VET",   "vechain"),
    ( 25, "FIL",   "filecoin"),
    ( 26, "ATOM",  "cosmos"),
    ( 27, "IMX",   "immutable-x"),
    ( 28, "TAO",   "bittensor"),
    ( 29, "WIF",   "dogwifcoin"),
    ( 30, "ARB",   "arbitrum"),
    ( 31, "OP",    "optimism"),
    ( 32, "INJ",   "injective-protocol"),
    ( 33, "HBAR",  "hedera-hashgraph"),
    ( 34, "RENDER","render-token"),
    ( 35, "MNT",   "mantle"),
    ( 36, "THETA", "theta-token"),
    ( 37, "BONK",  "bonk"),
    ( 38, "SEI",   "sei-network"),
    ( 39, "ALGO",  "algorand"),
    ( 40, "HYPE",  "hyperliquid"),
    ( 41, "LEO",   "leo-token"),
    ( 42, "ENA",   "ethena"),
    ( 43, "WLD",   "worldcoin-wld"),
    ( 44, "FLOKI", "floki"),
    ( 45, "BRETT", "based-brett"),
    ( 46, "JASMY", "jasmycoin"),
    ( 47, "BEAM",  "beam-2"),
    ( 48, "PYTH",  "pyth-network"),
    ( 49, "JUP",   "jupiter-ag"),
    ( 50, "JTO",   "jito-governance-token"),
    # ٥١-٩٠ (فضي)
    ( 51, "GRT",   "the-graph"),
    ( 52, "FLOW",  "flow"),
    ( 53, "AXS",   "axie-infinity"),
    ( 54, "SAND",  "the-sandbox"),
    ( 55, "MANA",  "decentraland"),
    ( 56, "GALA",  "gala"),
    ( 57, "CHZ",   "chiliz"),
    ( 58, "ENJ",   "enjincoin"),
    ( 59, "LDO",   "lido-dao"),
    ( 60, "CRV",   "curve-dao-token"),
    ( 61, "AAVE",  "aave"),
    ( 62, "MKR",   "maker"),
    ( 63, "SNX",   "havven"),
    ( 64, "COMP",  "compound-governance-token"),
    ( 65, "YFI",   "yearn-finance"),
    ( 66, "SUSHI", "sushi"),
    ( 67, "1INCH", "1inch"),
    ( 68, "CAKE",  "pancakeswap-token"),
    ( 69, "GMX",   "gmx"),
    ( 70, "ENS",   "ethereum-name-service"),
    ( 71, "BAT",   "basic-attention-token"),
    ( 72, "ZIL",   "zilliqa"),
    ( 73, "IOTA",  "iota"),
    ( 74, "XTZ",   "tezos"),
    ( 75, "NEO",   "neo"),
    ( 76, "KAVA",  "kava"),
    ( 77, "CELO",  "celo"),
    ( 78, "ROSE",  "oasis-network"),
    ( 79, "FTM",   "fantom"),
    ( 80, "EGLD",  "elrond-erd-2"),
    ( 81, "MINA",  "mina-protocol"),
    ( 82, "ONE",   "harmony"),
    ( 83, "ZEC",   "zcash"),
    ( 84, "DASH",  "dash"),
    ( 85, "WAVES", "waves"),
    ( 86, "RUNE",  "thorchain"),
    ( 87, "OSMO",  "osmosis"),
    ( 88, "SCRT",  "secret"),
    ( 89, "EVMOS", "evmos"),
    ( 90, "STRK",  "starknet"),
    # ٩١-١٥٠ (ذهبي)
    ( 91, "ONDO",  "ondo-finance"),
    ( 92, "W",     "wormhole"),
    ( 93, "EIGEN", "eigenlayer"),
    ( 94, "ETHFI", "ether-fi"),
    ( 95, "LISTA", "lista-dao"),
    ( 96, "ZK",    "zksync"),
    ( 97, "OMNI",  "omni-network"),
    ( 98, "TAIKO", "taiko"),
    ( 99, "BLAST", "blast"),
    (100, "MODE",  "mode"),
    (101, "MANTA", "manta-network"),
    (102, "ALT",   "altlayer"),
    (103, "PORTAL","portal-gaming"),
    (104, "PIXEL", "pixels"),
    (105, "SAGA",  "saga-2"),
    (106, "NOT",   "notcoin"),
    (107, "DOGS",  "dogs-2"),
    (108, "ARKM",  "arkham"),
    (109, "PYUSD", "paypal-usd"),
    (110, "GNO",   "gnosis"),
    (111, "ORDI",  "ordi"),
    (112, "SATS",  "1000sats-ordinals"),
    (113, "CRO",   "crypto-com-chain"),
    (114, "FRAX",  "frax"),
    (115, "LUSD",  "liquity-usd"),
    (116, "DAI",   "dai"),
    (117, "USDE",  "ethena-usde"),
    (118, "CRVUSD","crvusd"),
    (119, "STG",   "stargate-finance"),
    (120, "METIS", "metis-token"),
    (121, "CELR",  "celer-network"),
    (122, "ACH",   "alchemy-pay"),
    (123, "ID",    "space-id"),
    (124, "HIGH",  "highstreet"),
    (125, "SLP",   "smooth-love-potion"),
    (126, "ALPHA", "alpha-finance"),
    (127, "CTSI",  "cartesi"),
    (128, "DENT",  "dent"),
    (129, "BIFI",  "beefy-finance"),
    (130, "JUNO",  "juno-network"),
    (131, "KUJI",  "kujira"),
    (132, "APE",   "apecoin"),
    (133, "LOOKS", "looksrare"),
    (134, "BLUR",  "blur"),
    (135, "DFI",   "defichain"),
    (136, "SCROLL","scroll"),
    (137, "BOBA",  "boba-network"),
    (138, "RVN",   "ravencoin"),
    (139, "DGB",   "digibyte"),
    (140, "ZEN",   "horizen"),
    (141, "DCR",   "decred"),
    (142, "KDA",   "kadena"),
    (143, "BAND",  "band-protocol"),
    (144, "API3",  "api3"),
    (145, "ZRX",   "0x"),
    (146, "RPL",   "rocket-pool"),
    (147, "CVX",   "convex-finance"),
    (148, "BAL",   "balancer"),
    (149, "DYDX",  "dydx"),
    (150, "PERP",  "perpetual-protocol"),
    # ١٥١-٣٠٠ (ماسي)
    (151, "BGB",   "bitget-token"),
    (152, "OKB",   "okb"),
    (153, "KCS",   "kucoin-shares"),
    (154, "GT",    "gatechain-token"),
    (155, "NEXO",  "nexo"),
    (156, "OCEAN", "ocean-protocol"),
    (157, "FET",   "fetch-ai"),
    (158, "AGIX",  "singularitynet"),
    (159, "NMR",   "numeraire"),
    (160, "GTC",   "gitcoin"),
    (161, "ANKR",  "ankr"),
    (162, "STORJ", "storj"),
    (163, "HOT",   "holo"),
    (164, "ARPA",  "arpa-chain"),
    (165, "COTI",  "coti"),
    (166, "SKL",   "skale"),
    (167, "NKN",   "nkn"),
    (168, "MASK",  "mask-network"),
    (169, "BNT",   "bancor"),
    (170, "MLN",   "enzyme"),
    (171, "BOND",  "barnbridge"),
    (172, "IDLE",  "idle"),
    (173, "POOL",  "pooltogether"),
    (174, "OHM",   "olympus"),
    (175, "ALCX",  "alchemix"),
    (176, "TOKE",  "tokemak"),
    (177, "GNS",   "gains-network"),
    (178, "LYRA",  "lyra-finance"),
    (179, "KWENTA","kwenta"),
    (180, "LEVEL", "level"),
    (181, "MUX",   "mux-protocol"),
    (182, "RAYDIUM","raydium"),
    (183, "ORCA",  "orca"),
    (184, "SRM",   "serum"),
    (185, "FIDA",  "bonfida"),
    (186, "RAY",   "raydium"),
    (187, "TULIP", "solfarm"),
    (188, "SLND",  "solend"),
    (189, "CREAM", "cream-2"),
    (190, "HARD",  "kava-lend"),
    (191, "USDP",  "pax-dollar"),
    (192, "TUSD",  "true-usd"),
    (193, "MIM",   "magic-internet-money"),
    (194, "ALUSD", "alchemix-usd"),
    (195, "POLS",  "polkastarter"),
    (196, "TLM",   "alien-worlds"),
    (197, "ALICE", "my-neighbor-alice"),
    (198, "DEGO",  "dego-finance"),
    (199, "PUNDIX","pundi-x-2"),
    (200, "REEF",  "reef-finance"),
    (201, "VTHO",  "vethor-token"),
    (202, "ARDR",  "ardor"),
    (203, "STEEM", "steem"),
    (204, "HIVE",  "hive"),
    (205, "XVG",   "verge"),
    (206, "VRA",   "verasity"),
    (207, "UFT",   "unifly"),
    (208, "IOST",  "iostoken"),
    (209, "XDC",   "xdce-crowd-sale"),
    (210, "XEM",   "nem"),
    (211, "ICX",   "icon"),
    (212, "QTUM",  "qtum"),
    (213, "ELF",   "aelf"),
    (214, "WAN",   "wanchain"),
    (215, "SYS",   "syscoin"),
    (216, "KMD",   "komodo"),
    (217, "LUNA",  "terra-luna-2"),
    (218, "LUNC",  "terra-luna"),
    (219, "SPELL", "spell-token"),
    (220, "TRIBE", "tribe-2"),
    (221, "NFTX",  "nftx"),
    (222, "RARE",  "superrare"),
    (223, "MUSE",  "muse-2"),
    (224, "ILV",   "illuvium"),
    (225, "MC",    "merit-circle"),
    (226, "WILD",  "wilder-world"),
    (227, "REVV",  "revv"),
    (228, "GODS",  "gods-unchained"),
    (229, "YGG",   "yield-guild-games"),
    (230, "PYR",   "vulcan-forged"),
    (231, "GHST",  "aavegotchi"),
    (232, "MBOX",  "mobox"),
    (233, "WAXP",  "wax"),
    (234, "NAKA",  "nakamoto-games"),
    (235, "HERO",  "metahero"),
    (236, "UFO",   "ufo-gaming"),
    (237, "LOKA",  "league-of-kingdoms"),
    (238, "VOXEL", "voxies"),
    (239, "GAS",   "gas"),
    (240, "ONT",   "ontology"),
    (241, "SWTH",  "switcheo"),
    (242, "SSV",   "ssv-network"),
    (243, "POND",  "marlin"),
    (244, "SC",    "siacoin"),
    (245, "HOT",   "holo"),
    (246, "DOCK",  "dock"),
    (247, "WIN",   "wink"),
    (248, "BTT",   "bittorrent"),
    (249, "JST",   "just"),
    (250, "SUN",   "sun-token"),
    (251, "NULS",  "nuls"),
    (252, "FLM",   "flamingo-finance"),
    (253, "BURGER","burger-swap"),
    (254, "BEL",   "bella-protocol"),
    (255, "WING",  "wing-finance"),
    (256, "VITE",  "vite"),
    (257, "KLAY",  "klay-token"),
    (258, "BORA",  "bora"),
    (259, "MVL",   "mass-vehicle-ledger"),
    (260, "AERGO", "aergo"),
    (261, "CELO",  "celo"),
    (262, "COTI",  "coti"),
    (263, "RLC",   "iexec-rlc"),
    (264, "OXT",   "orchid-protocol"),
    (265, "LPT",   "livepeer"),
    (266, "AUDIO", "audius"),
    (267, "SAND",  "the-sandbox"),
    (268, "ATLAS", "star-atlas"),
    (269, "POLIS", "star-atlas-dao"),
    (270, "FIDA",  "bonfida"),
    (271, "RNDR",  "render-token"),
    (272, "STEP",  "step-finance"),
    (273, "MEDIA", "media-network"),
    (274, "PORT",  "port-finance"),
    (275, "MAPS",  "maps"),
    (276, "OXY",   "oxygen"),
    (277, "SLIM",  "solanium"),
    (278, "SBR",   "saber"),
    (279, "SUNNY", "sunny-aggregator"),
    (280, "COPE",  "cope"),
    (281, "COPE",  "cope"),
    (282, "RAIN",  "rain-coin-2"),
    (283, "CKB",   "nervos-network"),
    (284, "VGX",   "voyager-token"),
    (285, "FIRO",  "zcoin"),
    (286, "XVS",   "venus"),
    (287, "TKO",   "toko-token"),
    (288, "HOOK",  "hooked-protocol"),
    (289, "ACM",   "ac-milan-fan-token"),
    (290, "CHR",   "chromia"),
    (291, "FORTH", "ampleforth-governance-token"),
    (292, "NU",    "nucypher"),
    (293, "KEEP",  "keep-network"),
    (294, "RGT",   "rari-governance-token"),
    (295, "BADGER","badger-dao"),
    (296, "TORN",  "tornado-cash"),
    (297, "INDEX", "index-coop"),
    (298, "POOL",  "pooltogether"),
    (299, "GTC",   "gitcoin"),
    (300, "FARM",  "harvest-finance"),
]

# الرموز المرتبة
RANKED_SYMBOLS = [sym for _, sym, _ in COINS_RANKED]
RANKED_CG_MAP  = {sym: cg_id for _, sym, cg_id in COINS_RANKED}

# ملف حفظ التحديث الشهري
_UPDATE_FILE = os.path.join(os.path.dirname(__file__), "..", "coins_cache.json")
_UPDATE_INTERVAL = 30 * 24 * 3600  # 30 يوم


def get_allowed_symbols(tier: str) -> list:
    """يُعيد قائمة العملات المسموحة لباقة معينة."""
    _load_updated_list()
    limit = TIER_LIMITS.get(tier, 50)
    return RANKED_SYMBOLS[:min(limit, len(RANKED_SYMBOLS))]


def is_symbol_allowed(symbol: str, tier: str) -> bool:
    """يتحقق إذا كانت العملة مسموحة للمستخدم."""
    if tier in ("admin",):
        return True
    _load_updated_list()
    limit = TIER_LIMITS.get(tier, 50)
    allowed = set(RANKED_SYMBOLS[:min(limit, len(RANKED_SYMBOLS))])
    return symbol.upper() in allowed


def get_cg_id(symbol: str) -> str:
    """يُعيد CoinGecko ID للعملة."""
    _load_updated_list()
    return RANKED_CG_MAP.get(symbol.upper(), symbol.lower())


def get_tier_message(symbol: str, tier: str) -> str:
    """رسالة رفض واضحة مع ذكر الباقة المطلوبة."""
    try:
        from core.state_manager import TIERS
    except Exception:
        TIERS = {}
    tier_names = {
        "free":    "🆓 مجاني",
        "silver":  "🥈 فضي",
        "gold":    "🥇 ذهبي",
        "diamond": "💎 ماسي",
        "admin":   "👑 مدير",
    }
    current_name = tier_names.get(tier, tier)
    sym_upper    = symbol.upper()
    _load_updated_list()

    for t, limit in sorted(TIER_LIMITS.items(), key=lambda x: x[1]):
        if sym_upper in RANKED_SYMBOLS[:min(limit, len(RANKED_SYMBOLS))]:
            required = tier_names.get(t, t)
            return (
                f"⛔ *{sym_upper}* غير متاحة لباقتك ({current_name})\n\n"
                f"🔓 يتطلب: *{required}* أو أعلى\n"
                f"⬆️ للترقية: /upgrade"
            )
    return (
        f"⛔ *{sym_upper}* غير موجودة في القائمة الحالية\n"
        f"📋 القائمة تُحدَّث شهرياً من CoinGecko/CMC"
    )


def _load_updated_list():
    """يُحمِّل القائمة المحدَّثة من ملف Cache إذا كانت موجودة."""
    global RANKED_SYMBOLS, RANKED_CG_MAP
    try:
        if not os.path.exists(_UPDATE_FILE):
            return
        with open(_UPDATE_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "symbols" not in data:
            return
        ts = data.get("updated_at", 0)
        if time.time() - ts > _UPDATE_INTERVAL:
            return  # منتهي الصلاحية
        syms   = data["symbols"]
        cg_map = data.get("cg_map", {})
        if len(syms) >= 50:
            RANKED_SYMBOLS = syms
            RANKED_CG_MAP  = cg_map
    except Exception as e:
        logger.debug(f"coins_list cache load: {e}")


async def update_coins_list_from_api(session) -> bool:
    """
    يُحدِّث القائمة شهرياً من CoinGecko أو CoinMarketCap.
    يُستدعى تلقائياً من scheduler.py كل 30 يوم.
    """
    global RANKED_SYMBOLS, RANKED_CG_MAP

    # محاولة ١: CoinGecko top 500
    try:
        import aiohttp
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order":       "market_cap_desc",
            "per_page":    "250",
            "page":        "1",
            "sparkline":   "false",
        }
        headers = {"accept": "application/json"}

        new_symbols = []
        new_cg_map  = {}

        for page in [1, 2]:
            params["page"] = str(page)
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    coins = await r.json()
                    for coin in coins:
                        sym = (coin.get("symbol") or "").upper()
                        cid = coin.get("id") or sym.lower()
                        if sym and sym not in new_symbols and not sym.startswith("USD"):
                            new_symbols.append(sym)
                            new_cg_map[sym] = cid

        if len(new_symbols) >= 100:
            RANKED_SYMBOLS = new_symbols
            RANKED_CG_MAP  = new_cg_map
            # حفظ في Cache
            with open(_UPDATE_FILE, "w") as f:
                json.dump({
                    "symbols":    new_symbols,
                    "cg_map":     new_cg_map,
                    "updated_at": time.time(),
                    "source":     "coingecko",
                }, f)
            logger.info(f"✅ coins_list: تحديث من CoinGecko — {len(new_symbols)} عملة")
            return True

    except Exception as e:
        logger.warning(f"CoinGecko update failed: {e}")

    # محاولة ٢: CoinMarketCap (إذا فشل CoinGecko)
    try:
        cmc_key = os.environ.get("CMC_API_KEY", "")
        if cmc_key:
            url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
            params = {"start": "1", "limit": "500", "convert": "USD"}
            headers = {"X-CMC_PRO_API_KEY": cmc_key, "Accept": "application/json"}
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    data   = await r.json()
                    coins  = data.get("data", [])
                    new_s  = []
                    new_cm = {}
                    for coin in coins:
                        sym = (coin.get("symbol") or "").upper()
                        if sym and sym not in new_s:
                            new_s.append(sym)
                            new_cm[sym] = sym.lower()
                    if len(new_s) >= 100:
                        RANKED_SYMBOLS = new_s
                        RANKED_CG_MAP  = new_cm
                        with open(_UPDATE_FILE, "w") as f:
                            json.dump({
                                "symbols":    new_s,
                                "cg_map":     new_cm,
                                "updated_at": time.time(),
                                "source":     "coinmarketcap",
                            }, f)
                        logger.info(f"✅ coins_list: تحديث من CMC — {len(new_s)} عملة")
                        return True
    except Exception as e:
        logger.warning(f"CMC update failed: {e}")

    logger.warning("⚠️ فشل تحديث قائمة العملات — القائمة الثابتة مستمرة")
    return False

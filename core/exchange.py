"""
🏦 رائد — Exchange Layer
يدعم: Binance Spot + Bybit Spot
- HMAC-SHA256 authentication
- Market + Limit orders
- Cancel + Status
- Balance query
- urllib (built-in) لتجاوز قيود aiohttp
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
import ssl
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context()


@dataclass
class OrderResult:
    success:      bool
    order_id:     str   = ""
    symbol:       str   = ""
    side:         str   = ""      # "Buy" | "Sell"
    qty:          float = 0.0
    price:        float = 0.0
    status:       str   = ""      # "NEW" | "FILLED" | "CANCELLED"
    filled_qty:   float = 0.0
    avg_price:    float = 0.0
    fee:          float = 0.0
    error:        str   = ""
    exchange:     str   = ""


@dataclass
class Balance:
    asset:     str
    free:      float
    locked:    float
    total:     float


@dataclass
class ProtectResult:
    """نتيجة وضع أوامر SL/TP على المنصة نفسها."""
    success:  bool
    mode:     str  = "none"   # oco | separate | none
    sl_id:    str  = ""
    tp_id:    str  = ""
    group_id: str  = ""       # orderListId (Binance) / algoId (OKX)
    error:    str  = ""


def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _plain(d: Decimal) -> str:
    """Decimal → نص عشري عادي بلا صيغة علمية ولا أصفار زائدة."""
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def _floor_to_step(value, step) -> Decimal:
    v, st = _to_dec(value), _to_dec(step)
    if st <= 0:
        return v
    return (v / st).to_integral_value(rounding=ROUND_DOWN) * st


def _round_to_tick(value, tick) -> Decimal:
    v, t = _to_dec(value), _to_dec(tick)
    if t <= 0:
        return v
    return (v / t).to_integral_value(rounding="ROUND_HALF_UP") * t


def _decimals_step(n) -> Decimal:
    """Bitget/MEXC تعطي عدد المنازل → الخطوة."""
    try:
        return Decimal(1).scaleb(-int(n))
    except Exception:
        return Decimal(0)


# ═══════════════════════════════════════════════════════════════
# Base Exchange
# ═══════════════════════════════════════════════════════════════

# حدود الحجم الأدنى لكل منصة (USD)
EXCHANGE_MIN_ORDER_USD = {
    "binance": {"default": 5.0,  "BTC": 10.0, "ETH": 5.0},
    "okx":     {"default": 1.0,  "BTC": 5.0,  "ETH": 2.0},
    "bybit":   {"default": 1.0,  "BTC": 5.0,  "ETH": 1.0},
    "bitget":  {"default": 2.0,  "BTC": 5.0,  "ETH": 2.0},
    "mexc":    {"default": 1.0,  "BTC": 5.0,  "ETH": 1.0,  "FET": 1.0, "ZRO": 1.0},
}

def get_exchange_min_order(exchange_name: str, symbol: str) -> float:
    ex_mins = EXCHANGE_MIN_ORDER_USD.get(exchange_name.lower(), {})
    return ex_mins.get(symbol.upper(), ex_mins.get("default", 1.0))

class BaseExchange:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        # تنظيف credentials من أي مسافات أو أحرف خفية
        self.api_key    = api_key.strip()
        self.api_secret = api_secret.strip()
        self.testnet    = testnet
        self._rules_cache: Dict[str, tuple] = {}   # symbol → (ts, rules)

    # ── قواعد الرمز (خطوة الكمية/السعر/الحد الأدنى) ─────────────
    SUPPORTS_PROTECTION = False   # هل المنصة تدعم SL/TP على المنصة؟

    async def _fetch_rules(self, symbol: str) -> dict:
        """تُعاد كتابتها في كل منصة: {step, min_qty, tick, min_notional} (Decimal)."""
        return {}

    async def get_symbol_rules(self, symbol: str) -> dict:
        sym = symbol.upper()
        hit = self._rules_cache.get(sym)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        try:
            rules = await self._fetch_rules(sym)
        except Exception as e:
            logger.warning(f"{getattr(self,'EXCHANGE_NAME','?')} rules {sym}: {e}")
            rules = {}
        if rules and rules.get("step"):
            self._rules_cache[sym] = (time.time(), rules)
        return rules or {}

    async def normalize_qty(self, symbol: str, qty: float) -> tuple:
        """يقرّب الكمية للأسفل حسب خطوة المنصة. يُعيد (نص الكمية، خطأ)."""
        rules = await self.get_symbol_rules(symbol)
        q = _to_dec(qty)
        if rules.get("step"):
            q = _floor_to_step(q, rules["step"])
        else:
            q = q.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if q <= 0:
            return "", "الكمية بعد التقريب صفر"
        mn = rules.get("min_qty")
        if mn and q < _to_dec(mn):
            return "", f"الكمية {_plain(q)} أقل من الحد الأدنى {_plain(_to_dec(mn))}"
        return _plain(q), ""

    async def normalize_price(self, symbol: str, price: float) -> str:
        rules = await self.get_symbol_rules(symbol)
        p = _to_dec(price)
        if rules.get("tick"):
            p = _round_to_tick(p, rules["tick"])
        else:
            p = p.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        return _plain(p)

    # ── حماية SL/TP على المنصة (افتراضياً غير مدعومة) ────────────
    async def place_protection(self, symbol: str, qty: float,
                                sl: float, tp: float) -> "ProtectResult":
        return ProtectResult(False, error="غير مدعوم في هذه المنصة")

    async def cancel_protection(self, symbol: str, prot: dict) -> bool:
        return True

    async def protection_status(self, symbol: str, prot: dict) -> dict:
        """{"state": live|tp|sl|gone|unknown, "price": float}"""
        return {"state": "unknown", "price": 0.0}

    def _sign(self, data: str) -> str:
        return hmac.new(
            self.api_secret.encode(), data.encode(), hashlib.sha256
        ).hexdigest()

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        raise NotImplementedError

    async def _async_request(self, url: str, method: str = "GET",
                               params: dict = None, signed: bool = False) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._request(url, method, params, signed))

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0) -> OrderResult:
        raise NotImplementedError

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        raise NotImplementedError

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        raise NotImplementedError

    async def get_balance(self, asset: str = "USDT") -> Balance:
        raise NotImplementedError

    async def get_price(self, symbol: str) -> float:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
# Binance Spot
# ═══════════════════════════════════════════════════════════════
class BinanceExchange(BaseExchange):
    EXCHANGE_NAME = "binance"

    async def get_min_order_size(self, symbol: str) -> float:
        return get_exchange_min_order("binance", symbol)


    BASE     = "https://api.binance.com"
    BASE_TEST= "https://testnet.binance.vision"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__(api_key, api_secret, testnet)
        self._base = self.BASE_TEST if testnet else self.BASE
        logger.info(f"Binance Exchange: {'Testnet' if testnet else 'Live'}")

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        params = params or {}
        if signed:
            params["timestamp"] = str(int(time.time() * 1000))
            query   = urllib.parse.urlencode(params)
            params["signature"] = self._sign(query)

        full_url = url
        if method in ("GET", "DELETE") and params:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"

        headers = {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type":  "application/x-www-form-urlencoded",
            "User-Agent":    "RaedTradingAgent/2.0",
        }

        data = None
        if method == "POST":
            data = urllib.parse.urlencode(params).encode()
            full_url = url

        req = urllib.request.Request(full_url, data=data,
                                      headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"Binance HTTP {e.code}: {body[:200]}")
            return {"error": body, "code": e.code}
        except Exception as e:
            logger.error(f"Binance request error: {e}")
            return {"error": str(e)}

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0) -> OrderResult:
        """
        side: "BUY" | "SELL"
        order_type: "MARKET" | "LIMIT"
        """
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return OrderResult(success=False, error=q_err, exchange="binance")
        params = {
            "symbol":    f"{symbol.upper()}USDT",
            "side":      side.upper(),
            "type":      order_type.upper(),
            "quantity":  q_str,
        }
        if order_type.upper() == "LIMIT" and price > 0:
            params["price"]       = await self.normalize_price(symbol, price)
            params["timeInForce"] = "GTC"

        url = f"{self._base}/api/v3/order"
        data = await self._async_request(url, "POST", params, signed=True)

        if "error" in data or ("code" in data and data.get("code", 0) < 0):
            return OrderResult(
                success=False, error=str(data), exchange="binance")

        executed = float(data.get("executedQty", 0) or 0)
        quote    = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg      = (quote / executed) if executed > 0 and quote > 0 else float(data.get("price", 0) or 0)
        return OrderResult(
            success=True,
            order_id=str(data.get("orderId", "")),
            symbol=symbol.upper(),
            side=data.get("side", ""),
            qty=float(data.get("origQty", qty) or qty),
            price=float(data.get("price", 0) or 0),
            status=data.get("status", ""),
            filled_qty=executed,
            avg_price=avg,
            exchange="binance",
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        url    = f"{self._base}/api/v3/order"
        params = {"symbol": f"{symbol.upper()}USDT", "orderId": order_id}
        data   = await self._async_request(url, "DELETE", params, signed=True)
        return "error" not in data

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        url    = f"{self._base}/api/v3/order"
        params = {"symbol": f"{symbol.upper()}USDT", "orderId": order_id}
        data   = await self._async_request(url, "GET", params, signed=True)
        if "error" in data:
            return OrderResult(success=False, error=str(data))
        executed = float(data.get("executedQty", 0) or 0)
        quote    = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg      = (quote / executed) if executed > 0 and quote > 0 else float(data.get("price", 0) or 0)
        return OrderResult(
            success=True,
            order_id=str(data.get("orderId", "")),
            symbol=symbol.upper(),
            side=data.get("side", ""),
            qty=float(data.get("origQty", 0) or 0),
            price=float(data.get("price", 0) or 0),
            status=data.get("status", ""),
            filled_qty=executed,
            avg_price=avg,
            exchange="binance",
        )

    SUPPORTS_PROTECTION = True

    async def _fetch_rules(self, symbol: str) -> dict:
        data = await self._async_request(
            f"{self._base}/api/v3/exchangeInfo", "GET",
            {"symbol": f"{symbol}USDT"})
        syms = data.get("symbols") or []
        if not syms:
            return {}
        out = {}
        for f in syms[0].get("filters", []):
            t = f.get("filterType")
            if t == "LOT_SIZE":
                out["step"] = _to_dec(f.get("stepSize"))
                out["min_qty"] = _to_dec(f.get("minQty"))
            elif t == "PRICE_FILTER":
                out["tick"] = _to_dec(f.get("tickSize"))
            elif t in ("NOTIONAL", "MIN_NOTIONAL"):
                out["min_notional"] = _to_dec(f.get("minNotional"))
        return out

    async def place_protection(self, symbol: str, qty: float,
                                sl: float, tp: float) -> ProtectResult:
        """OCO بيع: LIMIT_MAKER عند TP + STOP_LOSS_LIMIT عند SL."""
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return ProtectResult(False, error=q_err)
        sl_s = await self.normalize_price(symbol, sl)
        slp  = await self.normalize_price(symbol, sl * 0.997)
        tp_s = await self.normalize_price(symbol, tp)
        params = {
            "symbol": f"{symbol.upper()}USDT", "side": "SELL",
            "quantity": q_str,
            "aboveType": "LIMIT_MAKER", "abovePrice": tp_s,
            "belowType": "STOP_LOSS_LIMIT", "belowStopPrice": sl_s,
            "belowPrice": slp, "belowTimeInForce": "GTC",
        }
        data = await self._async_request(
            f"{self._base}/api/v3/orderList/oco", "POST", params, signed=True)
        if "error" in data or "orderListId" not in data:
            return ProtectResult(False, error=str(data)[:200])
        sl_id = tp_id = ""
        for o in data.get("orderReports") or data.get("orders") or []:
            typ = str(o.get("type", ""))
            if typ == "LIMIT_MAKER":
                tp_id = str(o.get("orderId", ""))
            elif typ.startswith("STOP_LOSS"):
                sl_id = str(o.get("orderId", ""))
        return ProtectResult(True, mode="oco", sl_id=sl_id, tp_id=tp_id,
                             group_id=str(data.get("orderListId")))

    async def cancel_protection(self, symbol: str, prot: dict) -> bool:
        gid = prot.get("group_id")
        if not gid:
            return True
        data = await self._async_request(
            f"{self._base}/api/v3/orderList", "DELETE",
            {"symbol": f"{symbol.upper()}USDT", "orderListId": gid},
            signed=True)
        if "error" not in data:
            return True
        # أُلغي أو نُفِّذ سابقاً؟
        st = await self.protection_status(symbol, prot)
        return st.get("state") in ("gone", "tp", "sl")

    async def protection_status(self, symbol: str, prot: dict) -> dict:
        gid = prot.get("group_id")
        if not gid:
            return {"state": "unknown", "price": 0.0}
        data = await self._async_request(
            f"{self._base}/api/v3/orderList", "GET",
            {"orderListId": gid}, signed=True)
        if "error" in data:
            return {"state": "unknown", "price": 0.0}
        status = str(data.get("listOrderStatus", ""))
        if status == "EXECUTING":
            return {"state": "live", "price": 0.0}
        for key, state in (("tp_id", "tp"), ("sl_id", "sl")):
            oid = prot.get(key)
            if not oid:
                continue
            o = await self.get_order(symbol, oid)
            if o.success and o.status == "FILLED":
                return {"state": state, "price": o.avg_price}
        return {"state": "gone", "price": 0.0}

    async def get_balance(self, asset: str = "USDT",
                           account_type: str = "spot") -> Balance:
        if account_type == "futures":
            try:
                fbase = "https://testnet.binancefuture.com" if self.testnet \
                        else "https://fapi.binance.com"
                data  = await self._async_request(
                    f"{fbase}/fapi/v2/balance", "GET", {}, signed=True)
                if isinstance(data, list):
                    for b in data:
                        if b.get("asset") == asset.upper():
                            free  = float(b.get("availableBalance", 0) or 0)
                            total = float(b.get("balance", 0) or 0)
                            return Balance(asset, free, max(0, total-free), total)
            except Exception as e:
                logger.warning(f"Binance futures balance: {e}")
            return Balance(asset, 0, 0, 0)

        url  = f"{self._base}/api/v3/account"
        data = await self._async_request(url, "GET", {}, signed=True)
        if "error" in data:
            logger.warning(f"Binance balance error: {data.get('error','')[:200]}")
            return Balance(asset, 0, 0, 0)
        for b in data.get("balances", []):
            if b["asset"] == asset.upper():
                free   = float(b["free"])
                locked = float(b["locked"])
                return Balance(asset, free, locked, free + locked)
        return Balance(asset, 0, 0, 0)

    async def verify_credentials(self) -> tuple:
        """يتحقق من صحة Binance credentials."""
        try:
            data = await self._async_request(
                f"{self._base}/api/v3/account", "GET", {}, signed=True)
            if "error" not in data:
                return True, ""
            body = str(data.get("error", ""))
            if "-2014" in body or "API-key format invalid" in body:
                return False, "API Key غير صحيح"
            if "-2015" in body or "Invalid API-key" in body:
                return False, "API Key غير صالح أو IP غير مصرح به أو صلاحيات ناقصة"
            if "-1021" in body:
                return False, "Timestamp خارج النطاق — تحقق من توقيت الخادم"
            return False, f"خطأ: {body[:100]}"
        except Exception as e:
            return False, str(e)

    async def get_price(self, symbol: str) -> float:
        url  = f"{self._base}/api/v3/ticker/price"
        data = await self._async_request(
            url, "GET", {"symbol": f"{symbol.upper()}USDT"})
        return float(data.get("price", 0))

    async def get_volume_24h(self, symbol: str) -> float:
        url  = f"{self._base}/api/v3/ticker/24hr"
        data = await self._async_request(
            url, "GET", {"symbol": f"{symbol.upper()}USDT"})
        return float(data.get("quoteVolume", 0))


# ═══════════════════════════════════════════════════════════════
# Bybit Spot V5
# ═══════════════════════════════════════════════════════════════
class BybitExchange(BaseExchange):
    EXCHANGE_NAME = "bybit"

    async def get_min_order_size(self, symbol: str) -> float:
        return get_exchange_min_order("bybit", symbol)


    BASE      = "https://api.bybit.com"
    BASE_TEST = "https://api-testnet.bybit.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__(api_key, api_secret, testnet)
        self._base = self.BASE_TEST if testnet else self.BASE
        logger.info(f"Bybit Exchange V5: {'Testnet' if testnet else 'Live'}")

    def _sign_bybit(self, timestamp: str, params_str: str) -> str:
        """Bybit V5 signature: timestamp + api_key + recv_window + params"""
        recv_window = "5000"
        pre_sign = timestamp + self.api_key + recv_window + params_str
        return hmac.new(
            self.api_secret.encode(), pre_sign.encode(), hashlib.sha256
        ).hexdigest()

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        params     = params or {}
        timestamp  = str(int(time.time() * 1000))
        recv_window= "5000"

        headers = {
            "Content-Type": "application/json",
            "User-Agent":   "RaedTradingAgent/2.0",
        }

        if signed:
            headers["X-BAPI-API-KEY"]     = self.api_key
            headers["X-BAPI-TIMESTAMP"]   = timestamp
            headers["X-BAPI-RECV-WINDOW"] = recv_window

        if method == "GET":
            params_str = urllib.parse.urlencode(params)
            if signed:
                headers["X-BAPI-SIGN"] = self._sign_bybit(
                    timestamp, params_str)
            full_url = f"{url}?{params_str}" if params_str else url
            req = urllib.request.Request(
                full_url, headers=headers, method="GET")
        else:  # POST
            body = json.dumps(params).encode()
            if signed:
                headers["X-BAPI-SIGN"] = self._sign_bybit(
                    timestamp, json.dumps(params))
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                data = json.loads(r.read().decode())
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"Bybit HTTP {e.code}: {body[:200]}")
            return {"retCode": e.code, "retMsg": body}
        except Exception as e:
            logger.error(f"Bybit request error: {e}")
            return {"retCode": -1, "retMsg": str(e)}

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0) -> OrderResult:
        """
        side: "Buy" | "Sell"
        order_type: "Market" | "Limit"
        """
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return OrderResult(success=False, error=q_err, exchange="bybit")
        side_n = "Buy" if side.lower() in ("buy", "long") else "Sell"
        params = {
            "category":  "spot",
            "symbol":    f"{symbol.upper()}USDT",
            "side":      side_n,
            "orderType": order_type.capitalize(),
            "qty":       q_str,
        }
        if order_type.lower() == "market":
            # شراء Market في Spot يُفسَّر افتراضياً بقيمة USDT — نحدد العملة الأساس صراحةً
            params["marketUnit"] = "baseCoin"
        if order_type.lower() == "limit" and price > 0:
            params["price"]       = await self.normalize_price(symbol, price)
            params["timeInForce"] = "GTC"

        url  = f"{self._base}/v5/order/create"
        data = await self._async_request(url, "POST", params, signed=True)

        if data.get("retCode", -1) != 0:
            return OrderResult(
                success=False,
                error=data.get("retMsg", "Unknown error"),
                exchange="bybit",
            )

        result = data.get("result", {})
        return OrderResult(
            success=True,
            order_id=result.get("orderId", ""),
            symbol=symbol.upper(),
            side=side_n,
            qty=float(q_str),
            price=price,
            status="NEW",
            exchange="bybit",
        )

    async def cancel_order(self, symbol: str, order_id: str,
                            order_filter: str = "") -> bool:
        url    = f"{self._base}/v5/order/cancel"
        params = {"category": "spot",
                   "symbol": f"{symbol.upper()}USDT",
                   "orderId": order_id}
        if order_filter:
            params["orderFilter"] = order_filter
        data   = await self._async_request(url, "POST", params, signed=True)
        return data.get("retCode", -1) == 0

    async def get_order(self, symbol: str, order_id: str,
                         order_filter: str = "") -> OrderResult:
        url    = f"{self._base}/v5/order/realtime"
        params = {"category": "spot",
                   "symbol": f"{symbol.upper()}USDT",
                   "orderId": order_id}
        if order_filter:
            params["orderFilter"] = order_filter
        data   = await self._async_request(url, "GET", params, signed=True)
        if data.get("retCode", -1) != 0:
            return OrderResult(success=False,
                                error=data.get("retMsg"), exchange="bybit")
        orders = data.get("result", {}).get("list", [])
        if not orders:
            return OrderResult(success=False, error="Order not found",
                                exchange="bybit")
        o = orders[0]
        return OrderResult(
            success=True,
            order_id=o.get("orderId", ""),
            symbol=symbol.upper(),
            side=o.get("side", ""),
            qty=float(o.get("qty", 0)),
            price=float(o.get("price", 0) or 0),
            status=o.get("orderStatus", ""),
            filled_qty=float(o.get("cumExecQty", 0)),
            avg_price=float(o.get("avgPrice", 0) or 0),
            fee=float(o.get("cumExecFee", 0) or 0),
            exchange="bybit",
        )

    SUPPORTS_PROTECTION = True

    async def _fetch_rules(self, symbol: str) -> dict:
        data = await self._async_request(
            f"{self._base}/v5/market/instruments-info", "GET",
            {"category": "spot", "symbol": f"{symbol}USDT"})
        lst = (data.get("result") or {}).get("list") or []
        if data.get("retCode", -1) != 0 or not lst:
            return {}
        it = lst[0]
        lot = it.get("lotSizeFilter") or {}
        prc = it.get("priceFilter") or {}
        return {"step": _to_dec(lot.get("basePrecision")),
                "min_qty": _to_dec(lot.get("minOrderQty")),
                "tick": _to_dec(prc.get("tickSize")),
                "min_notional": _to_dec(lot.get("minOrderAmt"))}

    async def _stop_order(self, symbol: str, qty_str: str, trigger: str) -> tuple:
        params = {"category": "spot", "symbol": f"{symbol.upper()}USDT",
                  "side": "Sell", "orderType": "Market", "qty": qty_str,
                  "marketUnit": "baseCoin", "triggerPrice": trigger,
                  "orderFilter": "StopOrder"}
        data = await self._async_request(
            f"{self._base}/v5/order/create", "POST", params, signed=True)
        if data.get("retCode", -1) != 0:
            return "", str(data.get("retMsg", ""))[:150]
        return (data.get("result") or {}).get("orderId", ""), ""

    async def place_protection(self, symbol: str, qty: float,
                                sl: float, tp: float) -> ProtectResult:
        """أمران شرطيان (StopOrder) لا يحجزان الرصيد حتى التفعيل."""
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return ProtectResult(False, error=q_err)
        sl_id, e1 = await self._stop_order(
            symbol, q_str, await self.normalize_price(symbol, sl))
        if not sl_id:
            return ProtectResult(False, error=f"SL: {e1}")
        tp_id, e2 = await self._stop_order(
            symbol, q_str, await self.normalize_price(symbol, tp))
        if not tp_id:
            await self.cancel_order(symbol, sl_id, "StopOrder")
            return ProtectResult(False, error=f"TP: {e2}")
        return ProtectResult(True, mode="separate", sl_id=sl_id, tp_id=tp_id)

    async def cancel_protection(self, symbol: str, prot: dict) -> bool:
        ok = True
        for key in ("sl_id", "tp_id"):
            oid = prot.get(key)
            if not oid:
                continue
            if not await self.cancel_order(symbol, oid, "StopOrder"):
                o = await self.get_order(symbol, oid, "StopOrder")
                if not (o.success and o.status in ("Cancelled", "Deactivated",
                                                   "Filled", "Rejected")):
                    ok = False
        return ok

    async def protection_status(self, symbol: str, prot: dict) -> dict:
        states = {}
        for key, name in (("tp_id", "tp"), ("sl_id", "sl")):
            oid = prot.get(key)
            if not oid:
                continue
            o = await self.get_order(symbol, oid, "StopOrder")
            if not o.success:
                return {"state": "unknown", "price": 0.0}
            if o.status == "Filled":
                return {"state": name, "price": o.avg_price}
            states[name] = o.status
        if states and all(v in ("Untriggered", "New", "Triggered") for v in states.values()):
            return {"state": "live", "price": 0.0}
        return {"state": "gone", "price": 0.0}

    async def get_balance(self, asset: str = "USDT",
                           account_type: str = "spot") -> Balance:
        """
        إصلاح #18: حسابات Bybit القديمة (Classic) ليست UNIFIED —
        نُجرِّب عدة أنواع حسابات بالترتيب بدل الاكتفاء بـ UNIFIED فقط.
        """
        url = f"{self._base}/v5/account/wallet-balance"
        if account_type == "futures":
            types_to_try = ["CONTRACT", "UNIFIED"]
        else:
            types_to_try = ["UNIFIED", "FUND", "SPOT", "CONTRACT"]

        last_err = ""
        for atype in types_to_try:
            try:
                params = {"accountType": atype}
                data   = await self._async_request(url, "GET", params, signed=True)
                if data.get("retCode", -1) != 0:
                    last_err = data.get("retMsg", "")
                    continue
                for acc in data.get("result", {}).get("list", []):
                    for coin in acc.get("coin", []):
                        if coin["coin"] == asset.upper():
                            free  = float(coin.get("availableToWithdraw", 0)
                                          or coin.get("walletBalance", 0) or 0)
                            total = float(coin.get("walletBalance", 0) or 0)
                            if total > 0 or free > 0:
                                logger.info(f"Bybit {atype} {asset}: {total:.4f}")
                                return Balance(asset, free, max(0, total-free), total)
            except Exception as e:
                last_err = str(e)
                continue
        if last_err:
            logger.warning(f"Bybit balance: لم يُعثر على رصيد ({last_err})")
        return Balance(asset, 0, 0, 0)

    async def verify_credentials(self) -> tuple:
        """يتحقق من صحة Bybit credentials."""
        try:
            data = await self._async_request(
                f"{self._base}/v5/account/wallet-balance",
                "GET", {"accountType": "UNIFIED"}, signed=True)
            code = data.get("retCode", -1)
            if code == 0:
                return True, ""
            msg_txt = data.get("retMsg", "خطأ غير معروف")
            err_map = {
                10003: "API Key غير صحيح",
                10004: "توقيع غير صحيح — تحقق من API Secret",
                10005: "صلاحيات API غير كافية",
                10010: "IP غير مصرح به",
                10018: "IP غير مصرح به في إعدادات API",
            }
            reason = err_map.get(code, f"خطأ {code}: {msg_txt}")
            return False, reason
        except Exception as e:
            return False, str(e)

    async def get_price(self, symbol: str) -> float:
        url    = f"{self._base}/v5/market/tickers"
        params = {"category": "spot", "symbol": f"{symbol.upper()}USDT"}
        data   = await self._async_request(url, "GET", params)
        if data.get("retCode", -1) != 0:
            return 0.0
        lst = data.get("result", {}).get("list", [])
        return float(lst[0].get("lastPrice", 0)) if lst else 0.0


# ═══════════════════════════════════════════════════════════════
# OKX V5
# ═══════════════════════════════════════════════════════════════
class OKXExchange(BaseExchange):
    EXCHANGE_NAME = "okx"

    async def get_min_order_size(self, symbol: str) -> float:
        return get_exchange_min_order("okx", symbol)

    """OKX V5 API — Spot + Futures + Margin"""

    BASE      = "https://www.okx.com"
    BASE_TEST = "https://www.okx.com"   # OKX testnet نفس الـ base

    def __init__(self, api_key: str, api_secret: str,
                  passphrase: str = "", testnet: bool = False):
        super().__init__(api_key.strip(), api_secret.strip(), testnet)
        self.passphrase = passphrase.strip()
        self._base      = self.BASE
        logger.info(f"OKX Exchange V5: {'Testnet' if testnet else 'Live'}")

    def _sign_okx(self, timestamp: str, method: str,
                   path: str, body: str = "") -> str:
        pre_sign = timestamp + method.upper() + path + (body or "")
        return __import__("base64").b64encode(
            hmac.new(self.api_secret.encode(),
                      pre_sign.encode(), hashlib.sha256).digest()
        ).decode()

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        import base64
        params   = params or {}
        path     = url.replace(self._base, "")
        body_str = ""

        # إصلاح #18: بناء query string لـ GET دائماً (وليس فقط عند signed=True)
        # — كان هذا يُسبب فشل get_price/get_volume_24h غير الموقَّعة مع params
        if method == "GET" and params:
            qs   = urllib.parse.urlencode(params)
            path = path + "?" + qs

        headers = {
            "Content-Type": "application/json",
            "User-Agent":   "RaedTradingAgent/2.0",
        }

        if signed:
            ts = __import__("datetime").datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%S.") + "000Z"
            if method != "GET":
                body_str = json.dumps(params) if params else ""

            headers.update({
                "OK-ACCESS-KEY":        self.api_key,
                "OK-ACCESS-SIGN":       self._sign_okx(ts, method, path, body_str),
                "OK-ACCESS-TIMESTAMP":  ts,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
            })
            if self.testnet:
                headers["x-simulated-trading"] = "1"

        full_url = self._base + path
        data     = body_str.encode() if body_str else None
        req      = urllib.request.Request(
            full_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"OKX HTTP {e.code}: {body[:200]}")
            return {"code": str(e.code), "msg": body}
        except Exception as e:
            logger.error(f"OKX request error: {e}")
            return {"code": "-1", "msg": str(e)}

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0,
                           trade_mode: str = "cash") -> OrderResult:
        """trade_mode: cash=Spot | cross=Margin | isolated=Isolated"""
        inst_id = f"{symbol.upper()}-USDT"
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return OrderResult(success=False, error=q_err, exchange="okx")
        is_buy = side.lower() in ("buy", "long")
        params  = {
            "instId":   inst_id,
            "tdMode":   trade_mode,
            "side":     "buy" if is_buy else "sell",
            "ordType":  "market" if order_type.lower() == "market" else "limit",
            "sz":       q_str,
        }
        if order_type.lower() == "market" and trade_mode == "cash":
            # شراء Market في Spot يُفسَّر افتراضياً بقيمة USDT — نحدد العملة الأساس صراحةً
            params["tgtCcy"] = "base_ccy"
        if order_type.lower() == "limit" and price > 0:
            params["px"] = await self.normalize_price(symbol, price)

        data = await self._async_request(
            f"{self._base}/api/v5/trade/order", "POST", params, signed=True)
        d = (data.get("data") or [{}])[0]
        if data.get("code") != "0" or str(d.get("sCode", "0")) != "0":
            return OrderResult(
                success=False,
                error=d.get("sMsg") or data.get("msg", "") or str(data)[:150],
                exchange="okx")
        return OrderResult(
            success=True, order_id=d.get("ordId",""),
            symbol=symbol.upper(), side=side,
            qty=float(q_str), price=price, status="NEW", exchange="okx")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = await self._async_request(
            f"{self._base}/api/v5/trade/cancel-order", "POST",
            {"instId": f"{symbol.upper()}-USDT", "ordId": order_id},
            signed=True)
        return data.get("code") == "0"

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        data = await self._async_request(
            f"{self._base}/api/v5/trade/order", "GET",
            {"instId": f"{symbol.upper()}-USDT", "ordId": order_id},
            signed=True)
        if data.get("code") != "0":
            return OrderResult(success=False, error=data.get("msg"), exchange="okx")
        d = data.get("data", [{}])[0]
        return OrderResult(
            success=True, order_id=d.get("ordId",""),
            symbol=symbol.upper(), side=d.get("side",""),
            qty=float(d.get("sz",0)), price=float(d.get("px",0) or 0),
            status=d.get("state",""), filled_qty=float(d.get("fillSz",0) or 0),
            avg_price=float(d.get("avgPx",0) or 0), exchange="okx")

    SUPPORTS_PROTECTION = True

    async def _fetch_rules(self, symbol: str) -> dict:
        data = await self._async_request(
            f"{self._base}/api/v5/public/instruments", "GET",
            {"instType": "SPOT", "instId": f"{symbol}-USDT"})
        lst = data.get("data") or []
        if data.get("code") != "0" or not lst:
            return {}
        it = lst[0]
        return {"step": _to_dec(it.get("lotSz")),
                "min_qty": _to_dec(it.get("minSz")),
                "tick": _to_dec(it.get("tickSz"))}

    async def place_protection(self, symbol: str, qty: float,
                                sl: float, tp: float) -> ProtectResult:
        """OCO algo: TP/SL تنفيذ Market عند التفعيل."""
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return ProtectResult(False, error=q_err)
        params = {
            "instId": f"{symbol.upper()}-USDT", "tdMode": "cash",
            "side": "sell", "ordType": "oco", "sz": q_str,
            "tpTriggerPx": await self.normalize_price(symbol, tp), "tpOrdPx": "-1",
            "slTriggerPx": await self.normalize_price(symbol, sl), "slOrdPx": "-1",
        }
        data = await self._async_request(
            f"{self._base}/api/v5/trade/order-algo", "POST", params, signed=True)
        d = (data.get("data") or [{}])[0]
        if data.get("code") != "0" or str(d.get("sCode", "0")) != "0" \
                or not d.get("algoId"):
            return ProtectResult(
                False, error=d.get("sMsg") or data.get("msg", "") or str(data)[:150])
        return ProtectResult(True, mode="oco", group_id=str(d["algoId"]))

    async def cancel_protection(self, symbol: str, prot: dict) -> bool:
        gid = prot.get("group_id")
        if not gid:
            return True
        data = await self._async_request(
            f"{self._base}/api/v5/trade/cancel-algos", "POST",
            [{"algoId": gid, "instId": f"{symbol.upper()}-USDT"}], signed=True)
        d = (data.get("data") or [{}])[0]
        if data.get("code") == "0" and str(d.get("sCode", "0")) == "0":
            return True
        st = await self.protection_status(symbol, prot)
        return st.get("state") in ("gone", "tp", "sl")

    async def protection_status(self, symbol: str, prot: dict) -> dict:
        gid = prot.get("group_id")
        if not gid:
            return {"state": "unknown", "price": 0.0}
        data = await self._async_request(
            f"{self._base}/api/v5/trade/order-algo", "GET",
            {"algoId": gid}, signed=True)
        lst = data.get("data") or []
        if data.get("code") != "0" or not lst:
            return {"state": "unknown", "price": 0.0}
        d = lst[0]
        state = d.get("state", "")
        if state in ("live", "pause"):
            return {"state": "live", "price": 0.0}
        if state in ("effective", "partially_effective"):
            side = str(d.get("actualSide", "")).lower()
            px = float(d.get("actualPx", 0) or 0)
            if px <= 0 and d.get("ordId"):
                o = await self.get_order(symbol, d["ordId"])
                px = o.avg_price if o.success else 0.0
            return {"state": "tp" if side == "tp" else "sl", "price": px}
        return {"state": "gone", "price": 0.0}

    async def get_balance(self, asset: str = "USDT",
                           account_type: str = "spot") -> Balance:
        """
        يجلب الرصيد من OKX.
        يتحقق من Trading Account أولاً ثم Funding Account.
        """
        asset_up = asset.upper()

        # 1. Trading Account (Unified)
        try:
            data = await self._async_request(
                f"{self._base}/api/v5/account/balance", "GET",
                {"ccy": asset_up}, signed=True)
            if data.get("code") == "0":
                for d in data.get("data", []):
                    for det in d.get("details", []):
                        if det.get("ccy") == asset_up:
                            avail = float(det.get("availBal", 0) or 0)
                            total = float(det.get("eq", 0) or 0)
                            if total > 0:
                                logger.info(f"OKX Trading {asset}: {total:.4f}")
                                return Balance(asset, avail,
                                               max(0, total - avail), total)
            else:
                logger.warning(f"OKX Trading balance: code={data.get('code')} "
                               f"msg={data.get('msg','')}")
        except Exception as e:
            logger.warning(f"OKX Trading balance error: {e}")

        # 2. Funding Account (كثير من المستخدمين رصيدهم هنا)
        try:
            data2 = await self._async_request(
                f"{self._base}/api/v5/asset/balances", "GET",
                {"ccy": asset_up}, signed=True)
            if data2.get("code") == "0":
                for item in data2.get("data", []):
                    if item.get("ccy") == asset_up:
                        avail = float(item.get("availBal", 0) or 0)
                        bal   = float(item.get("bal", 0) or 0)
                        if bal > 0:
                            logger.info(f"OKX Funding {asset}: {bal:.4f}")
                            return Balance(asset, avail,
                                           max(0, bal - avail), bal)
            else:
                logger.warning(f"OKX Funding balance: code={data2.get('code')} "
                               f"msg={data2.get('msg','')}")
        except Exception as e:
            logger.warning(f"OKX Funding balance error: {e}")

        logger.warning(f"OKX: {asset} رصيد صفر في Trading + Funding")
        return Balance(asset, 0, 0, 0)

    async def verify_credentials(self) -> tuple:
        """يتحقق من صحة OKX credentials."""
        try:
            data = await self._async_request(
                f"{self._base}/api/v5/account/balance", "GET",
                {}, signed=True)
            code = str(data.get("code", ""))
            if code == "0":
                return True, ""
            msg_txt = data.get("msg", "خطأ غير معروف")
            err_map = {
                "50111": "API Key غير صحيح",
                "50113": "Passphrase خاطئ",
                "50119": "IP غير مصرح به",
                "50102": "Timestamp خارج النطاق",
            }
            reason = err_map.get(code, f"خطأ {code}: {msg_txt}")
            return False, reason
        except Exception as e:
            return False, str(e)

    async def get_price(self, symbol: str) -> float:
        data = await self._async_request(
            f"{self._base}/api/v5/market/ticker", "GET",
            {"instId": f"{symbol.upper()}-USDT"})
        if data.get("code") != "0":
            return 0.0
        lst = data.get("data", [])
        return float(lst[0].get("last", 0)) if lst else 0.0

    async def get_volume_24h(self, symbol: str) -> float:
        """حجم التداول 24h بـ USDT — لاختيار أفضل منصة."""
        data = await self._async_request(
            f"{self._base}/api/v5/market/ticker", "GET",
            {"instId": f"{symbol.upper()}-USDT"})
        if data.get("code") != "0":
            return 0.0
        lst = data.get("data", [])
        return float(lst[0].get("volCcy24h", 0)) if lst else 0.0


# ═══════════════════════════════════════════════════════════════
# Bitget V2
# ═══════════════════════════════════════════════════════════════
class BitgetExchange(BaseExchange):
    EXCHANGE_NAME = "bitget"

    async def get_min_order_size(self, symbol: str) -> float:
        return get_exchange_min_order("bitget", symbol)

    """Bitget V2 API — Spot + Futures"""

    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str,
                  passphrase: str = "", testnet: bool = False):
        # تنظيف جميع credentials من مسافات زائدة
        super().__init__(api_key.strip(), api_secret.strip(), testnet)
        self.passphrase = passphrase.strip()
        logger.info(
            f"Bitget Exchange V2: {'Testnet' if testnet else 'Live'} "
            f"| key={self.api_key[:8]}... | pass_len={len(self.passphrase)}"
        )

    def _sign_bitget(self, timestamp: str, method: str,
                      path: str, body: str = "") -> str:
        pre_sign = timestamp + method.upper() + path + (body or "")
        return __import__("base64").b64encode(
            hmac.new(self.api_secret.encode(),
                      pre_sign.encode(), hashlib.sha256).digest()
        ).decode()

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        params   = params or {}
        path     = url.replace(self.BASE, "")
        body_str = ""

        headers = {
            "Content-Type": "application/json",
            "User-Agent":   "RaedTradingAgent/2.0",
        }

        if method == "GET" and params:
            qs   = urllib.parse.urlencode(params)
            path = path + "?" + qs

        if signed:
            ts = str(int(time.time() * 1000))
            if method != "GET":
                body_str = json.dumps(params) if params else ""
            headers.update({
                "ACCESS-KEY":        self.api_key,
                "ACCESS-SIGN":       self._sign_bitget(ts, method, path, body_str),
                "ACCESS-TIMESTAMP":  ts,
                "ACCESS-PASSPHRASE": self.passphrase,
                "locale":            "en-US",
            })

        full_url = self.BASE + path
        data     = body_str.encode() if body_str else None
        req      = urllib.request.Request(
            full_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"Bitget HTTP {e.code}: {body[:200]}")
            return {"code": str(e.code), "msg": body}
        except Exception as e:
            logger.error(f"Bitget error: {e}")
            return {"code": "-1", "msg": str(e)}

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0) -> OrderResult:
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return OrderResult(success=False, error=q_err, exchange="bitget")
        is_buy   = side.lower() in ("buy", "long")
        is_mkt   = order_type.lower() == "market"
        size_str = q_str
        if is_buy and is_mkt:
            # Bitget: شراء Market يأخذ size بعملة التسعير (USDT) لا العملة الأساس
            px = await self.get_price(symbol)
            if px <= 0:
                return OrderResult(success=False, error="تعذّر جلب السعر لحساب قيمة الشراء",
                                   exchange="bitget")
            quote = (_to_dec(q_str) * _to_dec(px)).quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN)
            size_str = _plain(quote)
        params = {
            "symbol":    f"{symbol.upper()}USDT",
            "side":      "buy" if is_buy else "sell",
            "orderType": "market" if is_mkt else "limit",
            "force":     "gtc",
            "size":      size_str,
        }
        if order_type.lower() == "limit" and price > 0:
            params["price"] = await self.normalize_price(symbol, price)
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/place-order",
            "POST", params, signed=True)
        if str(data.get("code","")) != "00000":
            return OrderResult(success=False, error=data.get("msg",""), exchange="bitget")
        d = data.get("data", {})
        return OrderResult(
            success=True, order_id=d.get("orderId",""),
            symbol=symbol.upper(), side=side, qty=float(q_str), price=price,
            status="NEW", exchange="bitget")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/cancel-order", "POST",
            {"symbol": f"{symbol.upper()}USDT", "orderId": order_id},
            signed=True)
        return str(data.get("code","")) == "00000"

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/orderInfo", "GET",
            {"symbol": f"{symbol.upper()}USDT", "orderId": order_id},
            signed=True)
        if str(data.get("code","")) != "00000":
            return OrderResult(success=False, error=data.get("msg"), exchange="bitget")
        d = data.get("data", {})
        if isinstance(d, list):
            d = d[0] if d else {}
        filled = float(d.get("baseVolume", 0) or d.get("fillSize", 0) or 0)
        avg = float(d.get("priceAvg", 0) or 0)
        if avg <= 0 and filled > 0:
            quote = float(d.get("quoteVolume", 0) or 0)
            avg = quote / filled if quote > 0 else 0.0
        return OrderResult(
            success=True, order_id=d.get("orderId",""),
            symbol=symbol.upper(), side=d.get("side",""),
            qty=float(d.get("size",0) or 0), price=float(d.get("price",0) or 0),
            status=d.get("status",""), filled_qty=filled,
            avg_price=avg, exchange="bitget")

    SUPPORTS_PROTECTION = True

    async def _fetch_rules(self, symbol: str) -> dict:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/public/symbols", "GET",
            {"symbol": f"{symbol}USDT"})
        lst = data.get("data") or []
        if str(data.get("code", "")) != "00000" or not lst:
            return {}
        it = lst[0]
        return {"step": _decimals_step(it.get("quantityPrecision", 8)),
                "min_qty": _to_dec(it.get("minTradeAmount")),
                "tick": _decimals_step(it.get("pricePrecision", 8)),
                "min_notional": _to_dec(it.get("minTradeUSDT"))}

    async def _plan_order(self, symbol: str, q_str: str, trigger: str) -> tuple:
        params = {"symbol": f"{symbol.upper()}USDT", "side": "sell",
                  "triggerPrice": trigger, "orderType": "market",
                  "triggerType": "fill_price", "size": q_str,
                  "planType": "amount"}
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/place-plan-order", "POST",
            params, signed=True)
        if str(data.get("code", "")) != "00000":
            return "", str(data.get("msg", ""))[:150]
        return str((data.get("data") or {}).get("orderId", "")), ""

    async def place_protection(self, symbol: str, qty: float,
                                sl: float, tp: float) -> ProtectResult:
        """أمرا Plan منفصلان (SL وTP) — يُلغى أحدهما عند تنفيذ الآخر."""
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return ProtectResult(False, error=q_err)
        sl_id, e1 = await self._plan_order(
            symbol, q_str, await self.normalize_price(symbol, sl))
        if not sl_id:
            return ProtectResult(False, error=f"SL: {e1}")
        tp_id, e2 = await self._plan_order(
            symbol, q_str, await self.normalize_price(symbol, tp))
        if not tp_id:
            await self._cancel_plan(symbol, sl_id)
            return ProtectResult(False, error=f"TP: {e2}")
        return ProtectResult(True, mode="separate", sl_id=sl_id, tp_id=tp_id)

    async def _cancel_plan(self, symbol: str, oid: str) -> bool:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/cancel-plan-order", "POST",
            {"orderId": oid, "symbol": f"{symbol.upper()}USDT"}, signed=True)
        return str(data.get("code", "")) == "00000"

    async def _live_plan_ids(self, symbol: str):
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/trade/current-plan-order", "GET",
            {"symbol": f"{symbol.upper()}USDT", "limit": "100"}, signed=True)
        if str(data.get("code", "")) != "00000":
            return None
        d = data.get("data") or {}
        rows = d.get("orderList") if isinstance(d, dict) else d
        return {str(r.get("orderId")) for r in (rows or [])}

    async def cancel_protection(self, symbol: str, prot: dict) -> bool:
        live = await self._live_plan_ids(symbol)
        ok = True
        for key in ("sl_id", "tp_id"):
            oid = prot.get(key)
            if not oid:
                continue
            if live is not None and oid not in live:
                continue                       # لم يعد قائماً (نُفِّذ/أُلغي)
            if not await self._cancel_plan(symbol, oid):
                ok = False
        return ok

    async def protection_status(self, symbol: str, prot: dict) -> dict:
        live = await self._live_plan_ids(symbol)
        if live is None:
            return {"state": "unknown", "price": 0.0}
        ids = [prot.get("sl_id"), prot.get("tp_id")]
        if all(i in live for i in ids if i):
            return {"state": "live", "price": 0.0}
        return {"state": "gone", "price": 0.0}

    async def get_balance(self, asset: str = "USDT",
                           account_type: str = "spot") -> Balance:
        """
        يجلب الرصيد من Bitget.
        account_type: "spot" | "futures" | "all"
        """
        # Bitget V2 — endpoints حسب نوع الحساب
        spot_endpoints = [
            f"{self.BASE}/api/v2/spot/account/assets",
            f"{self.BASE}/api/v2/account/all-account-balance",
        ]
        futures_endpoints = [
            f"{self.BASE}/api/v2/mix/account/accounts?productType=USDT-FUTURES",
        ]

        endpoints = (
            futures_endpoints if account_type == "futures"
            else spot_endpoints + futures_endpoints if account_type == "all"
            else spot_endpoints
        )

        for endpoint in endpoints:
            try:
                data = await self._async_request(endpoint, "GET", {}, signed=True)
                code = str(data.get("code", ""))
                logger.debug(f"Bitget balance ({account_type}): code={code} "
                             f"raw={str(data)[:200]}")

                if code != "00000":
                    msg_txt = data.get("msg", "")
                    logger.warning(f"Bitget: code={code} msg={msg_txt}")
                    # أخطاء المصادقة → نُثير exception لمنع قبول الربط
                    auth_errors = {"40012", "40011", "40006", "40007", "40009"}
                    if code in auth_errors:
                        raise ValueError(
                            f"Bitget مصادقة فاشلة (code={code}): {msg_txt}\n"
                            f"تحقق من: API Key + Secret + Passphrase"
                        )
                    continue

                items = data.get("data", [])

                # شكل قائمة [{coin, available, frozen}, ...]
                if isinstance(items, list):
                    for b in items:
                        coin = (b.get("coin") or b.get("coinName") or
                                b.get("marginCoin") or "")
                        if coin.upper() == asset.upper():
                            free   = float(b.get("available") or
                                           b.get("availableAmount") or
                                           b.get("available_balance") or 0)
                            locked = float(b.get("frozen") or
                                           b.get("frozenAmount") or
                                           b.get("frozen_balance") or 0)
                            if free + locked > 0:
                                logger.info(f"Bitget {asset} ({account_type}): "
                                            f"free={free:.4f} locked={locked:.4f}")
                                return Balance(asset, free, locked, free + locked)

                # شكل dict
                elif isinstance(items, dict):
                    for key, val in items.items():
                        if key.upper() == asset.upper():
                            free = float(val.get("available", 0) or 0)
                            if free > 0:
                                return Balance(asset, free, 0, free)

            except Exception as e:
                logger.warning(f"Bitget balance {endpoint}: {e}")

        logger.warning(f"Bitget: {asset} رصيد صفر في جميع endpoints "
                       f"(spot+futures) — تحقق من صلاحيات API Key")
        return Balance(asset, 0, 0, 0)

    async def verify_credentials(self) -> tuple:
        """
        يتحقق من صحة API credentials.
        يُعيد (True, "") أو (False, "سبب الخطأ")
        """
        try:
            data = await self._async_request(
                f"{self.BASE}/api/v2/spot/account/assets",
                "GET", {}, signed=True)
            code = str(data.get("code", ""))
            if code == "00000":
                return True, ""
            msg_txt = data.get("msg", "خطأ غير معروف")
            err_map = {
                "40012": "Passphrase خاطئ — تحقق من كلمة المرور",
                "40011": "API Key غير صحيح",
                "40006": "IP غير مصرح به في إعدادات API",
                "40007": "صلاحيات API غير كافية — فعّل Read",
                "40009": "API Key منتهي الصلاحية",
            }
            reason = err_map.get(code, f"خطأ {code}: {msg_txt}")
            return False, reason
        except Exception as e:
            return False, str(e)

    async def get_price(self, symbol: str) -> float:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/market/tickers", "GET",
            {"symbol": f"{symbol.upper()}USDT"})
        if str(data.get("code","")) != "00000":
            return 0.0
        lst = data.get("data", [])
        return float(lst[0].get("lastPr", 0)) if lst else 0.0

    async def get_volume_24h(self, symbol: str) -> float:
        data = await self._async_request(
            f"{self.BASE}/api/v2/spot/market/tickers", "GET",
            {"symbol": f"{symbol.upper()}USDT"})
        if str(data.get("code","")) != "00000":
            return 0.0
        lst = data.get("data", [])
        return float(lst[0].get("usdtVolume", 0)) if lst else 0.0


# ═══════════════════════════════════════════════════════════════
# MEXC V3
# ═══════════════════════════════════════════════════════════════
class MEXCExchange(BaseExchange):
    EXCHANGE_NAME = "mexc"

    async def get_min_order_size(self, symbol: str) -> float:
        return get_exchange_min_order("mexc", symbol)

    """MEXC V3 API — Spot"""

    BASE = "https://api.mexc.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        super().__init__(api_key, api_secret, testnet)
        logger.info("MEXC Exchange V3: Live")

    def _request(self, url: str, method: str = "GET",
                  params: dict = None, signed: bool = False) -> dict:
        params = params or {}
        if signed:
            # M#93: MEXC V3 signature — timestamp أولاً ثم sign على كل المعاملات
            params["timestamp"] = str(int(time.time() * 1000))
            # MEXC تشترط: sign على query string مرتّب أبجدياً بدون "signature"
            query_to_sign = urllib.parse.urlencode(sorted(params.items()))
            sig           = self._sign(query_to_sign)
            # signature تُضاف في نهاية الـ query string
            # لا تُضاف في params لتجنب double-encoding

        headers = {
            "X-MEXC-APIKEY": self.api_key,
            "Content-Type":  "application/json",
            "User-Agent":    "RaedTradingAgent/2.0",
        }

        if method in ("GET", "DELETE"):
            # DELETE (إلغاء أمر) يتطلب المعاملات في الـ query لا في الـ body
            qs       = urllib.parse.urlencode(sorted(params.items()))
            full_url = f"{url}?{qs}" + (f"&signature={sig}" if signed else "")
            req      = urllib.request.Request(
                full_url, headers=headers, method=method)
        else:
            # POST: params في body والـ signature في URL query string
            body_str = urllib.parse.urlencode(sorted(params.items()))
            if signed:
                full_url = f"{url}?signature={sig}"
            else:
                full_url = url
            body     = body_str.encode()
            req      = urllib.request.Request(
                full_url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"MEXC HTTP {e.code}: {body[:200]}")
            return {"error": body, "code": e.code}
        except Exception as e:
            logger.error(f"MEXC error: {e}")
            return {"error": str(e)}

    async def _fetch_rules(self, symbol: str) -> dict:
        data = await self._async_request(
            f"{self.BASE}/api/v3/exchangeInfo", "GET",
            {"symbol": f"{symbol}USDT"})
        syms = data.get("symbols") or []
        if not syms:
            return {}
        it = syms[0]
        step = _to_dec(it.get("baseSizePrecision"))
        if step <= 0:
            step = _decimals_step(it.get("baseAssetPrecision", 8))
        return {"step": step,
                "tick": _decimals_step(it.get("quoteAssetPrecision", 8)),
                "min_notional": _to_dec(it.get("quoteAmountPrecision"))}

    async def place_order(self, symbol: str, side: str, qty: float,
                           order_type: str = "MARKET",
                           price: float = 0) -> OrderResult:
        import asyncio
        q_str, q_err = await self.normalize_qty(symbol, qty)
        if q_err:
            return OrderResult(success=False, error=q_err, exchange="mexc")
        params = {
            "symbol":   f"{symbol.upper()}USDT",
            "side":     "BUY" if side.lower() in ("buy","long") else "SELL",
            "type":     order_type.upper(),
            "quantity": q_str,
        }
        if order_type.upper() == "LIMIT" and price > 0:
            params["price"]       = await self.normalize_price(symbol, price)
            params["timeInForce"] = "GTC"
        # M#93: استخدام _request مباشرة في thread منفصل
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, lambda: self._request(
                    f"{self.BASE}/api/v3/order", "POST", params, signed=True))
        except Exception as e:
            return OrderResult(success=False, error=str(e), exchange="mexc")
        if "error" in data or (isinstance(data.get("code"), int) and data.get("code") != 200):
            err_msg = data.get("msg", str(data))
            logger.error(f"MEXC place_order failed: {err_msg}")
            return OrderResult(success=False, error=err_msg, exchange="mexc")
        # رد الإنشاء لا يحوي التنفيذ الفعلي — يؤكده OrderManager عبر get_order
        return OrderResult(
            success=True, order_id=str(data.get("orderId","")),
            symbol=symbol.upper(), side=side, qty=float(q_str), price=price,
            filled_qty=float(data.get("executedQty", 0) or 0),
            avg_price=0.0,
            status=data.get("status","NEW"), exchange="mexc")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = await self._async_request(
            f"{self.BASE}/api/v3/order", "DELETE",
            {"symbol": f"{symbol.upper()}USDT", "orderId": order_id},
            signed=True)
        return "error" not in data

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        data = await self._async_request(
            f"{self.BASE}/api/v3/order", "GET",
            {"symbol": f"{symbol.upper()}USDT", "orderId": order_id},
            signed=True)
        if "error" in data:
            return OrderResult(success=False, error=str(data), exchange="mexc")
        executed = float(data.get("executedQty", 0) or 0)
        quote    = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg      = (quote / executed) if executed > 0 and quote > 0 else float(data.get("price", 0) or 0)
        return OrderResult(
            success=True, order_id=str(data.get("orderId","")),
            symbol=symbol.upper(), side=data.get("side",""),
            qty=float(data.get("origQty",0) or 0), price=float(data.get("price",0) or 0),
            status=data.get("status",""), filled_qty=executed,
            avg_price=avg, exchange="mexc")

    async def get_balance(self, asset: str = "USDT",
                           account_type: str = "spot") -> Balance:
        if account_type == "futures":
            return await self._get_futures_balance(asset)

        data = await self._async_request(
            f"{self.BASE}/api/v3/account", "GET", {}, signed=True)
        # إصلاح #18 الحرج: استجابات الخطأ مثل {"code":700002,"msg":"Signature invalid"}
        # لا تحتوي مفتاح "error" — كانت تُعامَل كـ "لا أرصدة" فتُعيد $0 صامتة
        if "error" in data:
            logger.warning(f"MEXC balance HTTP error: {str(data.get('error'))[:200]}")
            return Balance(asset, 0, 0, 0)
        if "code" in data and "balances" not in data:
            logger.warning(f"MEXC balance API error: code={data.get('code')} "
                           f"msg={data.get('msg','')}")
            return Balance(asset, 0, 0, 0)
        for b in data.get("balances", []):
            if b["asset"] == asset.upper():
                free   = float(b["free"])
                locked = float(b["locked"])
                return Balance(asset, free, locked, free+locked)
        return Balance(asset, 0, 0, 0)

    async def _get_futures_balance(self, asset: str = "USDT") -> Balance:
        """رصيد MEXC Futures (Contract) — base + signing مختلف عن Spot."""
        try:
            import asyncio as _aio
            ts = str(int(time.time() * 1000))
            sign_str = f"{self.api_key}{ts}"
            sign = hmac.new(self.api_secret.encode(), sign_str.encode(),
                            hashlib.sha256).hexdigest()
            headers = {
                "ApiKey":      self.api_key,
                "Request-Time": ts,
                "Signature":   sign,
                "Content-Type": "application/json",
                "User-Agent":  "RaedTradingAgent/2.0",
            }
            url = "https://contract.mexc.com/api/v1/private/account/assets"
            req = urllib.request.Request(url, headers=headers, method="GET")
            loop = _aio.get_event_loop()
            def _do():
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
                    return json.loads(r.read().decode())
            data = await loop.run_in_executor(None, _do)
            if data.get("success") and isinstance(data.get("data"), list):
                for item in data["data"]:
                    if item.get("currency","").upper() == asset.upper():
                        free  = float(item.get("availableBalance", 0) or 0)
                        total = float(item.get("equity", 0) or 0)
                        return Balance(asset, free, max(0,total-free), total)
        except Exception as e:
            logger.warning(f"MEXC futures balance: {e}")
        return Balance(asset, 0, 0, 0)

    async def verify_credentials(self) -> tuple:
        """يتحقق من صحة MEXC credentials."""
        try:
            data = await self._async_request(
                f"{self.BASE}/api/v3/account", "GET", {}, signed=True)
            if "balances" in data:
                return True, ""
            if "error" in data:
                return False, f"خطأ اتصال: {str(data.get('error'))[:80]}"
            code = data.get("code")
            msg_txt = data.get("msg", "خطأ غير معروف")
            err_map = {
                700002: "توقيع غير صحيح — تحقق من API Secret",
                10072:  "API Key غير صحيح",
                10073:  "صلاحيات API غير كافية — فعّل Spot Trading + Read",
                700003: "Timestamp خارج النطاق — تحقق من توقيت الخادم",
            }
            reason = err_map.get(code, f"خطأ {code}: {msg_txt}")
            return False, reason
        except Exception as e:
            return False, str(e)

    async def get_price(self, symbol: str) -> float:
        data = await self._async_request(
            f"{self.BASE}/api/v3/ticker/price", "GET",
            {"symbol": f"{symbol.upper()}USDT"})
        return float(data.get("price", 0))

    async def get_volume_24h(self, symbol: str) -> float:
        data = await self._async_request(
            f"{self.BASE}/api/v3/ticker/24hr", "GET",
            {"symbol": f"{symbol.upper()}USDT"})
        return float(data.get("quoteVolume", 0))


# ═══════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════
def create_exchange(exchange: str, api_key: str,
                     api_secret: str, testnet: bool = False,
                     passphrase: str = "") -> BaseExchange:
    """
    يُنشئ exchange حسب الاسم.
    exchange: "binance" | "bybit"
    """
    ex = exchange.lower()
    if ex == "binance":
        return BinanceExchange(api_key, api_secret, testnet)
    elif ex == "bybit":
        return BybitExchange(api_key, api_secret, testnet)
    elif ex == "okx":
        return OKXExchange(api_key, api_secret, passphrase, testnet)
    elif ex == "bitget":
        return BitgetExchange(api_key, api_secret, passphrase, testnet)
    elif ex == "mexc":
        return MEXCExchange(api_key, api_secret, testnet)
    else:
        raise ValueError(f"Exchange غير مدعوم: {exchange}")


# قائمة المنصات المدعومة
SUPPORTED_EXCHANGES = {
    "okx":     {"name": "OKX",     "premium": False, "futures": True},
    "binance": {"name": "Binance", "premium": True,  "futures": True},
    "bybit":   {"name": "Bybit",   "premium": True,  "futures": True},
    "bitget":  {"name": "Bitget",  "premium": True,  "futures": True},
    "mexc":    {"name": "MEXC",    "premium": True,  "futures": False},
}

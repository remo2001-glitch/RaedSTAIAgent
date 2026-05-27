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


# ═══════════════════════════════════════════════════════════════
# Base Exchange
# ═══════════════════════════════════════════════════════════════
class BaseExchange:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.testnet    = testnet

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
        if method == "GET" and params:
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
        params = {
            "symbol":    f"{symbol.upper()}USDT",
            "side":      side.upper(),
            "type":      order_type.upper(),
            "quantity":  f"{qty:.6f}",
        }
        if order_type.upper() == "LIMIT" and price > 0:
            params["price"]       = f"{price:.2f}"
            params["timeInForce"] = "GTC"

        url = f"{self._base}/api/v3/order"
        data = await self._async_request(url, "POST", params, signed=True)

        if "error" in data or "code" in data and data.get("code", 0) < 0:
            return OrderResult(
                success=False, error=str(data), exchange="binance")

        return OrderResult(
            success=True,
            order_id=str(data.get("orderId", "")),
            symbol=symbol.upper(),
            side=data.get("side", ""),
            qty=float(data.get("origQty", qty)),
            price=float(data.get("price", 0) or data.get("cummulativeQuoteQty", 0) / max(float(data.get("executedQty", qty) or qty), 1e-8)),
            status=data.get("status", ""),
            filled_qty=float(data.get("executedQty", 0)),
            avg_price=float(data.get("price", price)),
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
        return OrderResult(
            success=True,
            order_id=str(data.get("orderId", "")),
            symbol=symbol.upper(),
            side=data.get("side", ""),
            qty=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)),
            status=data.get("status", ""),
            filled_qty=float(data.get("executedQty", 0)),
            avg_price=float(data.get("price", 0)),
            exchange="binance",
        )

    async def get_balance(self, asset: str = "USDT") -> Balance:
        url  = f"{self._base}/api/v3/account"
        data = await self._async_request(url, "GET", {}, signed=True)
        if "error" in data:
            return Balance(asset, 0, 0, 0)
        for b in data.get("balances", []):
            if b["asset"] == asset.upper():
                free   = float(b["free"])
                locked = float(b["locked"])
                return Balance(asset, free, locked, free + locked)
        return Balance(asset, 0, 0, 0)

    async def get_price(self, symbol: str) -> float:
        url  = f"{self._base}/api/v3/ticker/price"
        data = await self._async_request(
            url, "GET", {"symbol": f"{symbol.upper()}USDT"})
        return float(data.get("price", 0))


# ═══════════════════════════════════════════════════════════════
# Bybit Spot V5
# ═══════════════════════════════════════════════════════════════
class BybitExchange(BaseExchange):

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
        params = {
            "category":  "spot",
            "symbol":    f"{symbol.upper()}USDT",
            "side":      side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty":       f"{qty:.6f}",
        }
        if order_type.lower() == "limit" and price > 0:
            params["price"]       = f"{price:.4f}"
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
            side=side.capitalize(),
            qty=qty,
            price=price,
            status="NEW",
            exchange="bybit",
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        url    = f"{self._base}/v5/order/cancel"
        params = {"category": "spot",
                   "symbol": f"{symbol.upper()}USDT",
                   "orderId": order_id}
        data   = await self._async_request(url, "POST", params, signed=True)
        return data.get("retCode", -1) == 0

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        url    = f"{self._base}/v5/order/realtime"
        params = {"category": "spot",
                   "symbol": f"{symbol.upper()}USDT",
                   "orderId": order_id}
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

    async def get_balance(self, asset: str = "USDT") -> Balance:
        url    = f"{self._base}/v5/account/wallet-balance"
        params = {"accountType": "UNIFIED"}
        data   = await self._async_request(url, "GET", params, signed=True)
        if data.get("retCode", -1) != 0:
            return Balance(asset, 0, 0, 0)
        for acc in data.get("result", {}).get("list", []):
            for coin in acc.get("coin", []):
                if coin["coin"] == asset.upper():
                    free = float(coin.get("availableToWithdraw", 0) or 0)
                    total= float(coin.get("walletBalance", 0) or 0)
                    return Balance(asset, free, total - free, total)
        return Balance(asset, 0, 0, 0)

    async def get_price(self, symbol: str) -> float:
        url    = f"{self._base}/v5/market/tickers"
        params = {"category": "spot", "symbol": f"{symbol.upper()}USDT"}
        data   = await self._async_request(url, "GET", params)
        if data.get("retCode", -1) != 0:
            return 0.0
        lst = data.get("result", {}).get("list", [])
        return float(lst[0].get("lastPrice", 0)) if lst else 0.0


# ═══════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════
def create_exchange(exchange: str, api_key: str,
                     api_secret: str, testnet: bool = False) -> BaseExchange:
    """
    يُنشئ exchange حسب الاسم.
    exchange: "binance" | "bybit"
    """
    ex = exchange.lower()
    if ex == "binance":
        return BinanceExchange(api_key, api_secret, testnet)
    elif ex == "bybit":
        return BybitExchange(api_key, api_secret, testnet)
    else:
        raise ValueError(f"Exchange غير مدعوم: {exchange}")

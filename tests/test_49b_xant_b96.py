"""الدفعة 96 (رحال 2026-11-20 00:38 «نعم يمكنك ذلك»): OO1–OO4 — سهم ANTHROPIC الفوري XANT (قاعدة X) وعقده ANTHROPIC،
واكتشاف أي سهم مرمّز فوري جديد مع عقده — عرض/بيانات فقط، لا يمس قرار القياس."""
import asyncio
import types
from _util import chk, src
import core.data_layer as DL
import handlers.analysis as A

# ── OO1: القاموس ─────────────────────────────────────────────────
R = DL.resolve_stock_symbol
chk("OO1a ANTHROPIC: Spot = XANT و Futures = ANTHROPIC", R("ANTHROPIC", "spot")["okx_symbol"] == "XANT"
    and R("ANTHROPIC", "futures")["okx_symbol"] == "ANTHROPIC" and R("ANTHROPIC", "spot")["is_stock"])
chk("OO1b XANT لا يُجرَّد إلى ANT", R("XANT", "futures")["okx_symbol"] == "ANTHROPIC" and R("XANT", "spot")["base"] == "ANTHROPIC")
chk("OO1c قاعدة X باقية للأسهم الأخرى", R("NVDA", "spot")["okx_symbol"] == "XNVDA" and R("XSPCX", "futures")["okx_symbol"] == "SPCX")
s = src("core/data_layer.py")
chk("OO1d شموع Futures اليومية للعقد كما كانت (perp_daily)", '(bool(_sw_info.get("is_stock")) and not _sw_info.get("perp_daily"))' in s)
chk("OO1e سهم فوري حديث ⇒ شموع العقد موسومة", '_r["spot_young"] = len(x_spot_candles)' in s)
young = [{"close": 96.0, "source": "okx_perp", "spot_young": 1, "perp_inst": "ANTHROPIC-USDT-SWAP"}]
chk("OO1f الوسم: Spot حديث الإدراج والشموع من العقد", A._mkt_tag_b41("spot", young, "XANT")
    == " · 💱 Spot (حديث الإدراج: شمعة يومية واحدة — الشموع من ANTHROPIC-USDT-SWAP)", A._mkt_tag_b41("spot", young, "XANT"))
chk("OO1g بلا سهم فوري يبقى وسم الدفعة 95", A._mkt_tag_b41("spot", [{"source": "okx_perp"}]).startswith(" · 📈 Futures/Perp (لا سوق فوري"))

# ── OO2: النصوص تُحسب ─────────────────────────────────────────────
SENT = []


class _M:
    async def reply_text(self, t, *a, **k): SENT.append(t)


class _DL:
    async def resolve_display_alias_b95(self, s): return "ANTHROPIC" if s == "ANT" else s
    async def discover_x_stock_b96(self, s): return ""


ctx = types.SimpleNamespace(args=["ANT"], bot_data={"raed_engine": types.SimpleNamespace(data_layer=_DL())})
note = asyncio.run(A.alias_args_b95(types.SimpleNamespace(effective_message=_M()), ctx))
chk("OO2a سطر التوضيح يذكر السهم الفوري", note == "ℹ️ ANT في تطبيق OKX = عقد ANTHROPIC-USDT-SWAP (Futures)، والسهم الفوري XANT-USDT (Spot) — التحليل التالي لـ ANTHROPIC"
    and ctx.args == ["ANTHROPIC"], note)
a = src("handlers/analysis.py")
chk("OO2b /regime «العقد الدائم فقط» بعد تحقق فعلي", "_spot96 = bool((await engine.data_layer.check_spot_available(symbol)).get(\"available\"))" in a)

# ── OO3: الاكتشاف ─────────────────────────────────────────────────
class _D(DL.DataLayer):
    def __init__(self, cat="3", spot=95.9, perps=None):
        self.cat, self.spot, self.perps, self.session = cat, spot, perps or {}, None
    async def _price_okx(self, s, q="USDT"):
        return {"price": self.spot} if s.startswith("X") and self.spot else None
    async def _price_okx_perp(self, s):
        return {"price": self.perps[s]} if s in self.perps else None
    async def _swap_bases_list_b96(self):
        return ["BTC", "ANTHROPIC", "ANTX"] + list(self.perps)


async def _fake_fetch(session, url, **k):
    return {"code": "0", "data": [{"instCategory": FAKE_CAT[0]}]}
FAKE_CAT = ["3"]
_orig = DL._fetch
DL._fetch = _fake_fetch
try:
    DL._cache.clear()
    saved = {k: DL._TOKENIZED_STOCK_MAP.pop(k) for k in ("XANT", "ANTHROPIC")}
    r1 = asyncio.run(_D(perps={"ANTHROPIC": 96.4, "ANTX": 3.0}).discover_x_stock_b96("XANT"))
    chk("OO3a XANT ⇒ ANTHROPIC بمطابقة السعر", r1 == "ANTHROPIC" and DL._TOKENIZED_STOCK_MAP["XANT"]["okx_futures"] == "ANTHROPIC"
        and DL._TOKENIZED_STOCK_MAP["ANTHROPIC"]["okx_spot"] == "XANT", DL._TOKENIZED_STOCK_MAP.get("XANT"))
    DL._TOKENIZED_STOCK_MAP.update(saved)
    DL._cache.clear()
    FAKE_CAT[0] = "1"
    chk("OO3b عملة رقمية تبدأ بـX (instCategory 1) لا تُعامل سهماً", asyncio.run(_D(perps={"YO": 1.0}).discover_x_stock_b96("XYO")) == ""
        and "XYO" not in DL._TOKENIZED_STOCK_MAP)
    DL._cache.clear()
    FAKE_CAT[0] = "3"
    chk("OO3c XRP وأسهم القاموس لا تُفحص", asyncio.run(_D().discover_x_stock_b96("XRP")) == ""
        and asyncio.run(_D().discover_x_stock_b96("XNVDA")) == "NVDA")
finally:
    DL._fetch = _orig
    DL._cache.clear()
chk("OO3d مُطبَّق مركزياً في غلاف الأوامر", "await dl.discover_x_stock_b96(_xa)" in a)

# ── OO4: /chart ─────────────────────────────────────────────────
chk("OO4 سعر العقد للقطة XANT من القاموس", '_mf96 = _TOKENIZED_STOCK_MAP.get(sym, {}).get("okx_futures")' in s)

# ── فحص السوق الفوري (b96): كان _store(ttl=) يرمي استثناءً دائماً ⇒ «متاح» بلا فحص ─────────────
chk("SP1 لا _store بوسيط ttl غير مدعوم", "_store(_cache_key, result, ttl=" not in s)


class _S(DL.DataLayer):
    def __init__(self): self.session = None


async def _sf(session, url, **k):
    ok = "XANT-USDT" in url or "BTC-USDT" in url
    return {"code": "0", "data": [{"last": "95.9"}]} if ok else {"code": "51001", "data": []}
DL._fetch = _sf
try:
    DL._cache.clear()
    r = asyncio.run(_S().check_spot_available("ANTHROPIC"))
    chk("SP2 ANTHROPIC Spot ⇒ XANT فعلياً", r["available"] and r["spot_symbol"] == "XANT" and r["spot_price"] == 95.9, r)
    DL._cache.clear()
    r2 = asyncio.run(_S().check_spot_available("OPENAI"))
    chk("SP3 لا زوج فوري ⇒ غير متاح (لا افتراض)", r2["available"] is False, r2)
finally:
    DL._fetch = _orig
    DL._cache.clear()
chk("SP4 لا تنبيه ساعات NYSE لشركة خاصة", A._get_market_hours_warning("XANT") == "" and A._get_market_hours_warning("ANTHROPIC") == ""
    and not A._is_us_listed_symbol("ANTHROPIC") is None)

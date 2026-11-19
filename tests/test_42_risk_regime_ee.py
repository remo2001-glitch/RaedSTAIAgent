"""البند EE (رحال 2026-11-19): /risk يصرّح بمصدر المحفظة ويذكر مراكز القياس للحساب الحقيقي؛ /regime يتبع آخر سوق — عرض فقط."""
import asyncio
import types
from _util import chk, src
import handlers.trading as T
import handlers.analysis as A

# ── EE1: سطر المحفظة ─────────────────────────────────────────────
L = T.risk_portfolio_line_ee1
chk("EE1a رصيد فعلي يُعرض كما هو", L("ok", 2345.6, 10000, True) == "💰 المحفظة: $2,346")
f = L("fail", 10000, 10000, True)
chk("EE1b تعذّر الجلب لا يُعرض كأنه رصيدك", "غير متاحة الآن (تعذّر جلب رصيد المنصة)" in f and "القيمة المرجعية $10,000" in f
    and not f.startswith("💰 المحفظة: $10,000"), f)
z = L("zero", 10000, 10000, True)
chk("EE1c رصيد صفر/خطأ صامت يُصرَّح به", "رصيد المنصة $0 أو تعذّر جلبه" in z and "القيمة المرجعية" in z, z)
chk("EE1d المحفظة الافتراضية الفارغة", L("zero", 10000, 10000, False).startswith("💰 المحفظة: $0 — الحدود"))


class _Bal:
    def __init__(self, t): self.total = t


class _Ex:
    def __init__(self, v, boom=False): self.v, self.boom = v, boom
    async def get_balance(self, a):
        if self.boom:
            raise RuntimeError("net")
        return self.v


class _Eng:
    def __init__(self, ex): self.ex = ex
    def get_user_exchange(self, uid): return {"exchange": self.ex}


_orig = T._open_positions_map
T._open_positions_map = lambda e, u, l: {"BTC": 50.0}
try:
    run = lambda ex: asyncio.run(T.risk_equity_state_ee1(_Eng(ex), 1, True))
    chk("EE1e حالة ok (USDT + المراكز)", run(_Ex(_Bal(100.0))) == (150.0, "ok"))
    chk("EE1f استثناء ⇒ fail", run(_Ex(None, boom=True)) == (None, "fail"))
    chk("EE1g None ⇒ fail", run(_Ex(None)) == (None, "fail"))
finally:
    T._open_positions_map = _orig

t = src("handlers/trading.py")
chk("EE1h الحدود تُحسب كما كانت (المحفظة الفعلية وإلا المرجعية)", "pv     = _eq_ee1 if (_eq_ee1 or 0) > 0 else _ref_ee1" in t)
chk("EE2 سطر القياس الآلي للحساب الحقيقي", "🤖 القياس الآلي (افتراضي، خارج الحساب الحقيقي)" in t and "if (_auto87 and _live)" in t)
chk("EE2b لا يُخلط بعدد صفقات الحساب الحقيقي", "if not _live:   # محفظة افتراضية: مراكز Futures جزء منها" in t)

# ── EE3: /regime يتبع آخر سوق ─────────────────────────────────────
R = A.regime_market_ee3
chk("EE3a الافتراضي Futures", R({}, "BTC") == ("futures", "default"))
ctx = types.SimpleNamespace(user_data={})
A.remember_mkt_ee3(ctx, "btc", "spot")
chk("EE3b يتذكر آخر اختيار للأصل", R(ctx.user_data, "BTC") == ("spot", "sym"))
chk("EE3c ولأصل آخر: آخر سوق عموماً", R(ctx.user_data, "ETH") == ("spot", "last"))
chk("EE3d الوسيط الصريح يتقدّم", R(ctx.user_data, "BTC", "futures") == ("futures", "arg"))
A.remember_mkt_ee3(ctx, "ETH", "futures")
chk("EE3e اختيار ETH لا يغيّر BTC", R(ctx.user_data, "BTC") == ("spot", "sym") and R(ctx.user_data, "SOL") == ("futures", "last"))
hl = types.SimpleNamespace(user_data={"_headless_b68": True})
A.remember_mkt_ee3(hl, "BTC", "spot")
chk("EE3f المسح الآلي لا يكتب تفضيلاً", "_last_mkt_ee3" not in hl.user_data)
n = A.regime_mkt_note_ee3("sym", "spot", "BTC")
chk("EE3g سطر التوضيح وطريقة التبديل", "حسب آخر اختيار لك لهذا الأصل في /signal" in n and "/regime BTC futures" in n, n)
chk("EE3h لا سطر عند الوسيط الصريح أو احتياط الفوري", A.regime_mkt_note_ee3("arg", "spot", "BTC") == "" and A.regime_mkt_note_ee3("fallback", "spot", "BTC") == "")
s = src("handlers/analysis.py")
chk("EE3i /signal و/analyze يحفظان الاختيار", s.count("remember_mkt_ee3(context, (context.args") == 2)

"""الدفعة 103 (رحال 2026-11-21 07:17 «١. سويا ٢. فقط الخاص ٣. نفذه مع جميع الاصلاحات المعلقة»):
AC1 (أ) تنبيه «فرصة للحساب الحقيقي» + (ب) «فرصة تقترب» — للمالك في الخاص فقط، تنبيه بلا تنفيذ؛ AB1 وAB2 (/vfutures و/wallet).
لا تغيير في منطق القرار المجمّد."""
import asyncio
import time
import types
from _util import chk, src
import core.real_alert_b103 as RA
import core.auto_trader_b68 as AT
import core.access_guard as AG


class _FakeSM:
    def __init__(self):
        self.kv = {}
        self.wallets = {}
    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = str(v)
    def get_autotrade_users(self): return [OWNER]
    def get_tier(self, uid): return "diamond"
    def get_virtual_wallet(self, uid): return self.wallets.get(uid)
    def save_virtual_wallet(self, uid, d): self.wallets[uid] = d


OWNER = 777
FSM = _FakeSM()
RA._sm = lambda: FSM
AT._sm = lambda: FSM
AG.owner_id = lambda: OWNER
AG.is_allowed = lambda u: True

# ── (ب) شرط واحد ناقص ─────────────────────────────────────────────
L1 = RA.near_one_long_b103
chk("AC1b1 R/R عند السعر الحالي 0.95 ⇒ فرصة تقترب", "1:0.95" in L1({}, "R/R عند السعر الحالي 1:0.95 < 1", 0.953))
eth = {"verdict": "AUTO-FAIL", "verdict_reason": "G3 — R/R = 1:0.96", "direction": "long", "confidence": 0.70, "n_conf": 2}
chk("AC1b2 حالة ETH 05:09 (AUTO-FAIL R/R 0.96 وثقة 70%) ⇒ شرط واحد", L1(eth, "x", 0) == "R/R المرجّح 1:0.96 (المطلوب ≥ 1:1)", L1(eth, "x", 0))
chk("AC1b3 الجهة غير مؤكدة (ثقة 60%) ⇒ ليست شرطاً واحداً", L1({**eth, "confidence": 0.60}, "x", 0) == "")
chk("AC1b4 R/R بعيد (0.80) ⇒ لا", L1({**eth, "verdict_reason": "G3 — R/R = 1:0.80"}, "x", 0) == "" and L1({}, "R/R عند السعر الحالي 1:0.8 < 1", 0.8) == "")
chk("AC1b5 AUTO-FAIL لبوابة أخرى ⇒ لا", L1({**eth, "verdict_reason": "G4 أصل اصطناعي"}, "x", 0) == "")
chk("AC1b6 انتظار عام ⇒ لا", L1({"verdict": "WAIT", "confidence": 0.7}, "انتظار", 0) == "")
S1 = RA.near_one_short_b103
chk("AC1b7 Short ينقصه تأكيد واحد ⇒ نعم", S1({"eligible": False, "fails_b86": ["تأكيدات Short 1/4 (الحد الأدنى 2)"]}, "", 0)
    == "تأكيدات Short 1/4 (الحد الأدنى 2)")
chk("AC1b8 مانع (ذروة بيع) أو شرطان ⇒ لا", S1({"fails_b86": ["RSI 19 < 30 — ذروة بيع، لا Short"]}, "", 0) == ""
    and S1({"fails_b86": ["a", "b"]}, "", 0) == "")
chk("AC1b9 R/R Short بعيد (0.81) ⇒ لا", S1({"fails_b86": ["R/R المرجّح 1:0.81 < 1:1"]}, "", 0) == "")

# ── (أ) النص ───────────────────────────────────────────────────
o = {"sym": "ETH", "side": "long", "px": 1612.55, "sl": 1457.60, "tp1": 1700.0, "tp2": 1844.98, "rr": 1.12, "conf": 0.71,
     "n_conf": 2, "why": "دخول — شراء *مشروط*", "size_frac": 0.035}
t0 = RA.opp_text_b103(o, {"name": "OKX", "free": 0.0, "total": 0.0, "ok": True}, OWNER, "05:00 KSA")
chk("AC1a1 العنوان والمستويات", t0.startswith("🔔 *فرصة للحساب الحقيقي* — ETH 🟢 Long (Futures)") and "• الدخول: $1,612.55" in t0
    and "($1,457.60" not in t0 and "🛑 الوقف: $1,457.60 (-9.6%)" in t0 and "TP2 (40%): $1,844.98 (+14.4%)" in t0, t0)
chk("AC1a2 R/R والثقة والتأكيدات", "• R/R المرجّح: 1:1.12 | الثقة 71% | التأكيدات 2/4" in t0, t0)
chk("AC1a3 فحص الرصيد الحقيقي $0 ونسبة الحجم", "رصيدك الحقيقي على OKX: $0.00 — لا يمكن التنفيذ" in t0 and "3.5% من رأس المال" in t0, t0)
chk("AC1a4 الصلاحية ±1% وحتى المسح القادم", "صالحة ما دام السعر بين $1,596.42 و$1,628.68 (±1%) وحتى المسح القادم (05:00 KSA)" in t0, t0)
chk("AC1a5 تحذير «غير مثبتة» وتنبيه فقط وMarkdown آمن", "غير مثبتة بعد؛ القرار لك" in t0 and "🔒 تنبيه فقط — لا تنفيذ آلي" in t0
    and "مشروط*" not in t0, t0)
t1 = RA.opp_text_b103(o, {"name": "OKX", "free": 2000.0, "total": 2500.0, "ok": True}, OWNER)
chk("AC1a6 رصيد موجود ⇒ المبلغ بنفس النسبة", "بنفس نسبة القياس 3.5% ⇒ ≈ $70.00" in t1, t1)
chk("AC1a7 غير مربوط ⇒ /live", "غير مربوط — /live" in RA.opp_text_b103(o, None, OWNER))

# ── الإرسال: المالك فقط + منع التكرار ──────────────────────────────
SENT = []


async def _send(text, user_id=None, parse_mode=None):
    SENT.append((user_id, text))


eng = types.SimpleNamespace(get_user_exchange=lambda u: None)
FSM.kv.clear()
asyncio.run(RA.send_opp_b103(eng, _send, o))
asyncio.run(RA.send_opp_b103(eng, _send, o))
chk("AC1c1 (أ) للمالك فقط ومرة واحدة خلال 12 ساعة", len(SENT) == 1 and SENT[0][0] == OWNER)
SENT.clear()
n = {"sym": "ETH", "side": "long", "score": 0.70, "px": 1627.64, "missing": "R/R المرجّح 1:0.96 (المطلوب ≥ 1:1)"}
asyncio.run(RA.send_near_b103(eng, _send, [n]))
asyncio.run(RA.send_near_b103(eng, _send, [n]))
chk("AC1c2 (ب) مرة لكل أصل وجهة كل 24 ساعة", len(SENT) == 1 and SENT[0][0] == OWNER and "⏳ *فرصة تقترب* — ETH 🟢 Long" in SENT[0][1]
    and "ينقصها شرط واحد: R/R المرجّح 1:0.96" in SENT[0][1], SENT)
FSM.kv[f"realalert_b103:near:ETH:long"] = f"{time.time() - 25 * 3600:.0f}"
SENT.clear()
asyncio.run(RA.send_near_b103(eng, _send, [n]))
chk("AC1c3 بعد 24 ساعة يعود", len(SENT) == 1)

# ── دورة قياس كاملة (محاكاة): التنبيه قبل إشعار فتح القياس، والقرار كما هو ─────────────
import handlers.analysis as A
REC = {"BTC": {"verdict": "ENTER", "direction": "long", "confidence": 0.72, "size_pct": 20, "sl": 57000.0, "tp1": 63500.0,
               "tp2": 66000.0, "atr_pct": 2.0, "n_conf": 2, "decision_label": "دخول شراء", "regime_bucket": "هابط"},
       "ETH": {"verdict": "AUTO-FAIL", "verdict_reason": "G3 — R/R = 1:0.96", "direction": "long", "confidence": 0.70, "n_conf": 2,
               "sl": 1450.0, "tp1": 1700.0, "tp2": 1760.0},
       "SOL": {"verdict": "WAIT", "wait": True, "confidence": 0.20,
               "short": {"eligible": False, "fails_b86": ["تأكيدات Short 1/4 (الحد الأدنى 2)"]}}}
PX = {"BTC": 60000.0, "ETH": 1627.64, "SOL": 130.0}


async def _hs(engine, sym, mkt, uid, bd=None, timeout=150.0):
    return REC.get(sym, {})
A.headless_signal_b68 = _hs


async def _uni(engine):
    return ["BTC", "ETH", "SOL"]
AT._universe = _uni
AT.AUTO_EXTRA = []


class _DL:
    async def get_price(self, s, mkttype=None):
        return {"price": PX[s]}


eng2 = types.SimpleNamespace(data_layer=_DL(), event_risk=types.SimpleNamespace(get_exposure_multiplier=lambda: (1.0, "")),
                             get_user_exchange=lambda u: None,
                             scheduler=types.SimpleNamespace(next_scan_ar=lambda: "⏰ المسح القادم: 09:00 KSA — بعد 4 ساعات"))
FSM.kv.clear()
FSM.wallets.clear()
SENT.clear()
sm = asyncio.run(AT.run_auto_cycle_b68(eng2, _send))
heads = [t.split("\n")[0] for _, t in SENT]
chk("AC1d1 الدورة: فتح BTC كما كان (القرار لم يتغيّر)", sm["long"] == 1 and sm.get("opened_syms") == ["BTC"], sm)
chk("AC1d2 تنبيه الحساب الحقيقي قبل إشعار فتح القياس", len(heads) >= 2 and heads[0].startswith("🔔 *فرصة للحساب الحقيقي* — BTC")
    and heads[1].startswith("🤖 *صفقة آلية افتراضية* — BTC"), heads)
chk("AC1d3 فرصتان تقتربان (ETH R/R، SOL تأكيد Short) للمالك", any(h.startswith("⏳ *فرصة تقترب* — ETH 🟢 Long") for h in heads)
    and any(h.startswith("⏳ *فرصة تقترب* — SOL 🔴 Short") for h in heads) and all(u == OWNER for u, _ in SENT), heads)
chk("AC1d4 صلاحية «حتى المسح القادم (09:00 KSA)»", "وحتى المسح القادم (09:00 KSA)" in SENT[0][1], SENT[0][1])
SENT.clear()
FSM.wallets.clear()
asyncio.run(AT.run_auto_cycle_b68(eng2, _send))
chk("AC1d5 الدورة التالية: لا تكرار للتنبيهين (12 و24 ساعة)", not any(t.startswith(("🔔", "⏳")) for _, t in SENT), [t[:30] for _, t in SENT])

# ── AB1 / AB2 ──────────────────────────────────────────────────
import handlers.trading as T
p = {"qty": 500 / 1612.55, "fee_open": 0.25}
usd = T._loss_sl_usd_b103(p, 1612.55, 1457.60, 0.0005)
chk("AB1a الخسارة عند الوقف شاملة الرسوم = بطاقة الفتح ($48.52 / 29.1%)", f"{usd:.2f}" == "48.52" and f"{usd / 166.67 * 100:.1f}" == "29.1", usd)
st = src("handlers/trading.py")
chk("AB1b /vfutures بالدولار والنسبة الشاملة، وبطاقة الفتح بمنزلة", "شاملة الرسوم؛ " in st and "({_lp:.1f}% من الهامش)" in st)
chk("AB2a «مُستثمر (فوري)» و«إجمالي (نقد + فوري بالتكلفة + هامش Futures)»", "• مُستثمر (فوري):" in st
    and "• إجمالي (نقد + فوري بالتكلفة + هامش Futures):" in st)
chk("AB2b سطر «إجمالي لو أُغلقت المراكز الآن»", "• إجمالي لو أُغلقت المراكز الآن:" in st and "_wallet_mtm_b103" in st)
from core.virtual_wallet import VirtualWallet as _VW
vw = _VW({"balance": 10055.20, "invested": 0.0, "positions": {}, "history": [], "futures": {"ETH": {
    "symbol": "ETH", "side": "long", "entry": 1612.55, "qty": 500 / 1612.55, "margin": 166.67, "notional": 500.0,
    "fee_open": 0.25, "leverage": 3}}})


class _DLw:
    async def get_price(self, s, mkttype=None):
        return {"price": 1611.75}
ctx = types.SimpleNamespace(bot_data={"raed_engine": types.SimpleNamespace(data_layer=_DLw())})
mtm = asyncio.run(T._wallet_mtm_b103(ctx, vw))
chk("AB2c حالة 06:22: الإجمالي لو أُغلق = $10,222.12 − $1.25 (صافي /vfutures)", f"{vw.total_value:.2f}" == "10221.87"
    and f"{mtm:.2f}" == "10220.87", (vw.total_value, mtm))

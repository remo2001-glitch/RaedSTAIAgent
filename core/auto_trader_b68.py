"""
core/auto_trader_b68.py — التداول الآلي الافتراضي بمنطق /signal المجمّد (الدفعة 68)

قرارات رحال 2026-11-06:
  • التنفيذ الآلي = حكم /signal الكامل نفسه (يُشغَّل /signal داخلياً لكل عملة ويُقرأ قراره المحفوظ) —
    لا منطق دخول/وقف مستقل في المسح (كان: ثقة ≥65% فقط ووقف −2%/هدف +4% ثابتان).
  • المستويات: دخول/وقف/هدفان 60/40 من /signal، وخروج زمني 5 أيام.
  • النطاق: أكبر 20 عملة (بلا العملات المستقرة)، 6 مرات يومياً مع المسح الرباعي.
  • السوق (batch68e — قرار رحال): كل الأصول في Futures افتراضي برافعة 1x للجهتين — شراء وShort (حكم /signal بسوق Futures).
  • الحجم: 5% من المحفظة × (حجم /signal ÷ أقصى حجم للباقة) × سقف التعرض (70%/50% حول الأحداث الكبيرة).
  • القياس: سجل منفصل autoledger_b68 — المراكز القديمة تبقى كما هي ولا تدخل القياس.
  • الرسوم والانزلاق: Spot 0.1% لكل جهة، Futures 0.05% لكل جهة، وانزلاق 0.1% عند الدخول والخروج.
"""
from core.ar_count import ar_n   # QQ2 (b98): صيغة العدّ العربية
from core.real_alert_b103 import (near_one_long_b103, near_one_short_b103,   # AC1 (b103): تنبيه الحساب الحقيقي
                                  send_near_b103, send_opp_b103)
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("RAED.auto_b68")

AUTO_LOGIC_VER = "b68"
AUTO_UNIVERSE = 20
AUTO_BASE_PCT = 0.05
AUTO_MAX_OPEN = 10
AUTO_SPOT_FEE = 0.001
AUTO_SLIP = 0.001
AUTO_HOLD_HOURS = 120.0
AUTO_MIN_USD = 20.0
AUTO_CONCURRENCY = 4      # batch68j: تحليلات /signal المتزامنة في المسح الآلي
STABLES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "GUSD", "LUSD", "FRAX",
           "MIM", "ALUSD", "SUSD", "PYUSD", "USDE", "USD1", "RLUSD", "EURC", "USDS", "USDTB", "BUIDL", "USYC",
           # batch68c: أصول مغلّفة/مرهونة تكرّر BTC/ETH (ولا سوق فوري لها على OKX) + رموز منصات بلا سيولة OKX كافية
           "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "METH", "LBTC", "SOLVBTC", "BSC-USD",
           "JITOSOL", "MSOL", "LEO", "FIGR_HELOC", "WBT",
           "USDG"}   # AG2 (b105): Global Dollar عملة مستقرة — كانت تظهر في «لا عقد دائم» (مسح 21 نوفمبر 09:09)
# معايير العبور إلى الحقيقي (المتفق عليها 2026-11-06)
CRIT_MIN_TRADES = 50
CRIT_MAX_DD_PCT = 10.0
CRIT_MIN_REGIMES = 2


def _sm():
    from core.state_manager import state_manager
    return state_manager




# ── سجل القياس ──────────────────────────────────────────────────────────
def ledger_get(uid: int) -> list:
    try:
        return json.loads(_sm().kv_get(f"autoledger_b68:{int(uid)}", "[]") or "[]")
    except Exception:
        return []


def ledger_add(uid: int, rec: dict) -> None:
    try:
        L = ledger_get(uid)
        L.append(rec)
        _sm().kv_set(f"autoledger_b68:{int(uid)}", json.dumps(L[-5000:], ensure_ascii=False))
    except Exception as e:
        logger.warning(f"autoledger add: {e}")




# ── البوابات (نفس /trade: قرار /signal + R/R عند سعر التنفيذ) ─────────────
def long_gate_b68(rec: dict, price: float):
    """(ok, سبب، rr_now). نفس شروط /trade: لا AUTO-FAIL، لا انتظار، حجم > 0، شراء، وR/R المرجّح ≥ 1 عند السعر الحالي."""
    if not rec:
        return False, "لا قرار /signal", 0.0
    if str(rec.get("verdict", "")).upper() == "AUTO-FAIL":
        return False, autofail_reason_b79(rec), 0.0
    if str(rec.get("verdict", "")).upper() == "WATCH ONLY":   # AF4 (أ) b105 (رحال 2026-11-21 09:12)
        return False, watch_reason_b105(rec), 0.0
    if rec.get("wait") or float(rec.get("size_pct") or 0) <= 0:
        return False, "انتظار", 0.0
    if str(rec.get("direction", "")).lower() not in ("long", "bullish", "buy"):
        return False, "لا اتجاه شراء", 0.0
    sl, tp1, tp2 = float(rec.get("sl") or 0), float(rec.get("tp1") or 0), float(rec.get("tp2") or 0)
    if price <= 0 or sl <= 0 or tp1 <= 0 or price <= sl or price >= tp1:
        return False, "السعر خارج بنية الوقف/الهدف", 0.0
    sl_pct = (price - sl) / price * 100
    atr = float(rec.get("atr_pct") or 0)
    if atr > 0 and sl_pct < 1.5 * atr:           # نفس /trade: الوقف ≥1.5×ATR من سعر التنفيذ
        sl_pct = 1.5 * atr
    tp1_pct = (tp1 / price - 1) * 100
    tp2_pct = (tp2 / price - 1) * 100 if tp2 > price else tp1_pct
    rr = (0.6 * tp1_pct + 0.4 * tp2_pct) / sl_pct if sl_pct > 0 else 0.0
    if rr < 1.0:
        return False, f"R/R عند السعر الحالي 1:{rr:.2f} < 1", rr
    return True, "", rr


def size_reason_ar_b108(label: str) -> str:
    """AH1 (b108): وسم قرار الحجم بالعربية — «(NORMAL) — حجم 10–20%» ⇒ «حجم عادي (10–20% حسب /signal)»."""
    import re as _re108
    s = str(label or "").strip()
    m = _re108.match(r"^[\[(]\s*(HIGH|NORMAL|LOW|WAIT)\s*[\])]\s*—?\s*(.*)$", s, _re108.I)
    if not m:
        return s
    tag, rest = m.group(1).upper(), m.group(2).strip()
    name = {"HIGH": "حجم مرتفع", "NORMAL": "حجم عادي", "LOW": "حجم منخفض", "WAIT": "انتظار"}[tag]
    r = _re108.match(r"^حجم\s+([\d–\-]+%)\s*(.*)$", rest)
    if r:
        return f"{name} ({r.group(1)} حسب /signal)" + (f" {r.group(2)}" if r.group(2) else "")
    return f"{name}" + (f" — {rest}" if rest else "")


GATE_REV = "b105"   # AF4 (أ): البوابة تحترم حكم جودة البنية «انتظار — مراقبة فقط» — يُوسَم في لقطة الدخول


def watch_reason_b105(rec: dict) -> str:
    """AF4 (أ) b105: سبب «انتظار — مراقبة فقط» من تقييم البنية (هبوط حاد، ثقة دون الحد، بنية ضعيفة…). عرض فقط."""
    w = str((rec or {}).get("watch_reason") or "").replace("*", "").strip()
    return "جودة البنية «انتظار — مراقبة فقط»" + (f": {w}" if w else "")


def short_gate_b68(sh: dict, price: float):
    if not sh or not sh.get("eligible"):
        return False, (sh or {}).get("reason") or "Short غير مؤهل", 0.0
    sl, tp1, tp2 = float(sh.get("sl") or 0), float(sh.get("tp1") or 0), float(sh.get("tp2") or 0)
    if price <= 0 or sl <= 0 or tp1 <= 0 or price >= sl or price <= tp1:
        return False, "السعر خارج بنية الوقف/الهدف", 0.0
    sl_pct = (sl - price) / price * 100
    tp1_pct = (price - tp1) / price * 100
    tp2_pct = (price - tp2) / price * 100 if 0 < tp2 < price else tp1_pct
    rr = (0.6 * tp1_pct + 0.4 * tp2_pct) / sl_pct if sl_pct > 0 else 0.0
    if rr < 1.0:
        return False, f"R/R عند السعر الحالي 1:{rr:.2f} < 1", rr
    return True, "", rr


def size_usd_b68(total_value: float, factor: float, ev_mult: float) -> float:
    return max(0.0, float(total_value) * AUTO_BASE_PCT * max(0.0, min(1.0, float(factor))) * max(0.0, min(1.0, float(ev_mult))))


def _long_factor(rec: dict, tier: str) -> float:
    try:
        from handlers.analysis import _TIER_CONF
        mx = float(_TIER_CONF.get(tier, _TIER_CONF["silver"])[2]) or 35.0
    except Exception:
        mx = 35.0
    return min(1.0, float(rec.get("size_pct") or 0) / mx)


# batch68d (قرار رحال 2026-11-06): أصول إضافية ثابتة — النفط برنت والفضة والذهب (عقود دائمة فقط) والأسهم المرمّزة
AUTO_EXTRA = ["BZ", "XAG", "XAU", "SPCX", "MSTR", "CRCL", "SNDK", "TSLA", "INTC", "GOOGL", "QQQ", "MU", "NVDA"]


AUTO_STOCKS = ["SPCX", "MSTR", "CRCL", "SNDK", "TSLA", "INTC", "GOOGL", "QQQ", "MU", "NVDA"]






# batch68b: قائمة احتياطية حين يتعذّر CoinGecko (كان طلب limit=40 مباشرة بعد طلب المسح يُرفض 429 فلا يُفحص شيء)
FALLBACK_TOP = ["BTC", "ETH", "XRP", "BNB", "SOL", "TRX", "DOGE", "ADA", "HYPE", "LINK",
                "BCH", "XLM", "SUI", "AVAX", "LTC", "HBAR", "TON", "DOT", "UNI", "AAVE"]


_PERP_PROBE_B68 = {}      # batch68i: رمز → (وقت الفحص، هل له عقد دائم USDT على OKX)
_PERP_PROBE_TTL = 6 * 3600
LAST_NO_PERP_B68 = []     # آخر ما استُبعد من النطاق لعدم وجود عقد دائم (لسطر التقرير)


async def _has_perp_b68(engine, sym: str):
    """True/False من OKX SWAP؛ None عند تعذّر الفحص نفسه."""
    import time as _t
    c = _PERP_PROBE_B68.get(sym)
    if c and _t.time() - c[0] < _PERP_PROBE_TTL:
        return c[1]
    try:
        pp = await engine.data_layer._price_okx_perp(sym)
    except Exception:
        return None
    ok = bool(pp and float((pp or {}).get("price", 0) or 0) > 0)
    if ok:   # الرفض لا يُخزَّن: عطل شبكة عابر لا يستبعد عملة لساعات
        _PERP_PROBE_B68[sym] = (_t.time(), ok)
    return ok


async def _universe(engine) -> list:
    """أكبر 20 عملة (بلا عملات مستقرة/مغلّفة) **لها عقد دائم على OKX** — batch68i: كل التنفيذ Futures 1x،
    فالعملة غير المدرجة في Futures (مثل XMR وRAIN في مسح 5 ص) تُستبعد ويحل محلها التالي في الترتيب."""
    global LAST_NO_PERP_B68
    coins = []
    for lim in (150, 30, 40):   # نفس مفاتيح كاش المسح الرباعي أولاً (بلا طلب جديد)
        try:
            coins = await engine.data_layer.get_top_coins(limit=lim) or []
        except Exception:
            coins = []
        if len(coins) >= AUTO_UNIVERSE:
            break
    cand = []
    for c in coins:
        s = str((c or {}).get("symbol") or "").upper()
        if s and s not in STABLES and s not in cand:
            cand.append(s)
    for s_ in FALLBACK_TOP:
        if s_ not in cand:
            cand.append(s_)
    out, no_perp, unknown = [], [], 0
    probe = hasattr(getattr(engine, "data_layer", None), "_price_okx_perp")
    for s in cand:
        if len(out) >= AUTO_UNIVERSE:
            break
        if probe:
            hp = await _has_perp_b68(engine, s)
            if hp is False:
                no_perp.append(s)
                continue
            if hp is None:
                unknown += 1
        out.append(s)
    # حماية: إن رُفض أكثر من النصف فالأرجح عطل في الشبكة لا غياب العقود — لا نستبعد شيئاً
    if len(no_perp) > AUTO_UNIVERSE // 2:
        out = cand[:AUTO_UNIVERSE]
        no_perp = []
    LAST_NO_PERP_B68 = no_perp[:]
    return out


async def run_auto_cycle_b68(engine, send_fn=None, bot_data=None) -> dict:
    """دورة دخول واحدة لكل مستخدمي التداول الآلي. تُرجع ملخصاً للسجل."""
    from core.virtual_wallet import VirtualWallet
    from handlers.analysis import headless_signal_b68
    summary = {"scanned": 0, "long": 0, "short": 0, "skipped": {}, "universe": 0, "users": 0,
               "waits": 0, "errors": 0, "ev_mult": 1.0, "scanned_syms": [], "err_syms": []}
    _rec_cache = {}   # batch68g: (الباقة، الأصل) → قرار /signal — لا يُعاد التحليل لكل حساب بنفس الباقة
    uids = _sm().get_autotrade_users() or []
    # batch69 (الوضع الخاص، قرار رحال): الدخول الآلي لحسابات المالك والمدراء والمسموح لهم فقط
    try:
        from core.access_guard import is_allowed as _ok69
        uids = [u for u in uids if _ok69(u)]
    except Exception:
        pass
    summary["users"] = len(uids)
    if not uids:
        return summary
    try:
        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
    except Exception:
        ev_mult, ev_reason = 1.0, ""
    summary["ev_mult"] = ev_mult
    if ev_mult <= 0:
        summary["skipped"]["event"] = ev_reason
        return summary
    syms = await _universe(engine)
    syms += [x for x in AUTO_EXTRA if x not in syms]   # batch68d
    summary["universe"] = len(syms)
    summary["no_perp"] = list(LAST_NO_PERP_B68)
    # batch68j (ملاحظة رحال: تقرير المسح يتأخر منذ مسح 9 م يوم 6 نوفمبر): تحليلات /signal كانت متتابعة
    # (حتى 33 أصلاً × عدد الباقات). الآن تُجلب مسبقاً بالتوازي بحد AUTO_CONCURRENCY — نفس الحكم ونفس المستويات،
    # والدخول بعدها بسعر لحظي جديد كما كان. لا تغيير في منطق القرار.
    _t0 = time.time()
    _need = {}
    for uid in uids:
        try:
            tier = _sm().get_tier(uid)
        except Exception:
            tier = "silver"
        try:
            wd0 = _sm().get_virtual_wallet(uid) or {}
        except Exception:
            wd0 = {}
        # batch70: مراكز القياس فقط تمنع إعادة الدخول — المراكز اليدوية على الأصل نفسه لا تمنعه (قرار رحال)
        _pos0 = {str((p or {}).get("symbol") or k).split("@", 1)[0].upper()
                 for k, p in (wd0.get("futures") or {}).items() if (p or {}).get("logic_ver")} | \
                {k for k, p in (wd0.get("positions") or {}).items() if (p or {}).get("logic_ver")}
        _nopen0 = sum(1 for p in (wd0.get("positions") or {}).values() if (p or {}).get("logic_ver")) + \
                  sum(1 for p in (wd0.get("futures") or {}).values() if (p or {}).get("logic_ver"))
        if _nopen0 >= AUTO_MAX_OPEN:
            continue
        for sym in syms:
            if sym not in _pos0 and (tier, sym) not in _need:
                _need[(tier, sym)] = uid
    _sem = asyncio.Semaphore(AUTO_CONCURRENCY)

    async def _pre(ck, uid_):
        async with _sem:
            try:
                _rec_cache[ck] = await headless_signal_b68(engine, ck[1], "futures", uid_, bot_data)
            except Exception as e:
                logger.warning(f"auto_b68 {ck[1]}: /signal futures فشل — {type(e).__name__}: {e}")
                _rec_cache[ck] = {}
    if _need:
        await asyncio.gather(*[_pre(ck, u) for ck, u in _need.items()], return_exceptions=True)
    summary["analysis_sec"] = round(time.time() - _t0, 1)
    logger.info(f"auto_b68: {len(_need)} تحليل /signal في {summary['analysis_sec']}s (توازي {AUTO_CONCURRENCY})")
    for uid in uids:
        try:
            tier = _sm().get_tier(uid)
        except Exception:
            tier = "silver"
        for sym in syms:
            wd = _sm().get_virtual_wallet(uid) or {"balance": 10000.0, "invested": 0.0, "profit": 0.0,
                                                    "positions": {}, "history": [], "futures": {}}
            vw = VirtualWallet(wd)
            n_open = sum(1 for p in vw.positions.values() if p.get("logic_ver")) + \
                     sum(1 for p in vw.futures.values() if p.get("logic_ver"))
            if n_open >= AUTO_MAX_OPEN:
                summary["skipped"]["max_open"] = summary["skipped"].get("max_open", 0) + 1
                break
            if _auto_open_b70(vw, sym):   # مركز قياس مفتوح على الأصل (لا المراكز اليدوية)
                if sym not in summary.setdefault("held_syms", []):
                    summary["held_syms"].append(sym)
                continue
            summary["scanned"] += 1
            if sym not in summary["scanned_syms"]:
                summary["scanned_syms"].append(sym)
            # batch68e (قرار رحال 2026-11-06): كل الأصول — العملات الرقمية والأسهم المرمّزة والذهب والفضة والنفط —
            # تُحلَّل وتُنفَّذ في Futures 1x للجهتين (شراء وبيع). /signal بسوق Futures مرة واحدة يعطي حكم الشراء وتقييم Short معاً.
            _fut_long = True
            rec = {}
            _ck = (tier, sym)
            if _ck in _rec_cache:
                rec = _rec_cache[_ck]
            else:
                try:
                    rec = await headless_signal_b68(engine, sym, "futures", uid, bot_data)
                except Exception as e:
                    logger.warning(f"auto_b68 {sym}: /signal futures فشل — {type(e).__name__}: {e}")
                    rec = {}
                _rec_cache[_ck] = rec
            if not rec:
                summary["errors"] += 1
                if sym not in summary["err_syms"]:
                    summary["err_syms"].append(sym)
                continue
            try:
                pd = await engine.data_layer.get_price(sym, mkttype="futures")   # batch68e: سعر العقد الدائم
                px = float((pd or {}).get("price") or 0)
            except Exception:
                px = 0.0
            if px <= 0:
                continue
            ok, why, rr = long_gate_b68(rec, px)
            _cf73 = float(rec.get("confidence") or 0)
            if not ok and _cf73 > 0.35:   # batch73 (H2): عرض فقط — أقرب الأصول للدخول وما ينقصها
                _near_add_b73(summary, sym, "شراء", _cf73,
                              (_wait_reason_b77(rec, _cf73) if why == "انتظار" else why))
            if not ok:   # AC1 (ب) b103: فرصة تقترب — شرط واحد ناقص (تنبيه للمالك فقط)
                _real_near_add_b103(summary, sym, "long", _cf73, px, near_one_long_b103(rec, why, rr))
            if ok:
                # AC1 (أ) b103: تنبيه «فرصة للحساب الحقيقي» للمالك — قبل فتح صفقة القياس الافتراضية
                _epx105 = px * (1 + AUTO_SLIP)   # AG1 (b105): نفس سعر تنفيذ القياس ووقفه (كان من السعر قبل الانزلاق)
                await _real_opp_b103(engine, send_fn, summary, {
                    "sym": sym, "side": "long", "px": _epx105, "rr": rr, "conf": _cf73,
                    "sl": (min(float(rec["sl"]), _epx105 * (1 - 1.5 * float(rec.get("atr_pct") or 0) / 100))
                           if float(rec.get("atr_pct") or 0) > 0 else float(rec["sl"])),
                    "tp1": float(rec["tp1"]), "tp2": float(rec.get("tp2") or rec["tp1"]),
                    "n_conf": rec.get("n_conf"), "why": rec.get("decision_label") or rec.get("size_rule") or "",
                    "size_frac": AUTO_BASE_PCT * max(0.0, min(1.0, _long_factor(rec, tier))) * max(0.0, min(1.0, ev_mult))})
                amt = size_usd_b68(vw.total_value, _long_factor(rec, tier), ev_mult)
                amt = min(amt, vw.balance * 0.95)
                if amt >= AUTO_MIN_USD:
                    pid = uuid.uuid4().hex[:10]
                    epx = px * (1 + AUTO_SLIP)
                    _atr = float(rec.get("atr_pct") or 0)
                    _sl_use = float(rec["sl"])
                    if _atr > 0:   # نفس /trade: الوقف ≥ 1.5×ATR من سعر التنفيذ
                        _sl_use = min(_sl_use, epx * (1 - 1.5 * _atr / 100))
                    extra = {"logic_ver": AUTO_LOGIC_VER, "pos_id": pid, "auto": True,
                             "stop_loss": _sl_use, "take_profit": float(rec.get("tp2") or rec["tp1"]),
                             "tp1": float(rec["tp1"]), "tp2": float(rec.get("tp2") or rec["tp1"]), "tp1_done": False,
                             "max_hold_hours": AUTO_HOLD_HOURS, "regime": rec.get("regime_bucket", ""),
                             "rr_entry": round(rr, 3), "conf_entry": float(rec.get("confidence") or 0),
                             **entry_snapshot_b80(rec, ev_mult)}   # batch80
                    if _fut_long:   # batch68e: كل الشراء الآلي عبر Futures 1x
                        _ex_f = {k: v for k, v in extra.items() if k not in ("stop_loss", "take_profit", "max_hold_hours")}
                        r = vw.open_futures(sym, "long", epx, amt, 1, stop_loss=_sl_use,
                                            take_profit=float(rec.get("tp2") or rec["tp1"]), max_hold_hours=AUTO_HOLD_HOURS,
                                            extra=_ex_f, key=VirtualWallet.auto_key(sym))
                    else:
                        r = vw.buy(sym, epx, amt, fee_rate=AUTO_SPOT_FEE, extra=extra)
                    if r.get("ok"):
                        _sid = _track_open_b68(engine, uid, sym, "long", epx, extra, rec.get("confidence"), 0)
                        _book = vw.futures if _fut_long else vw.positions
                        _k70 = r.get("key") or sym
                        if _sid and _k70 in _book:
                            _book[_k70]["signal_id"] = _sid
                            _book[_k70]["setup_type"] = "auto_long_b68"
                        _sm().save_virtual_wallet(uid, vw.to_dict())
                        summary["long"] += 1
                        summary.setdefault("opened_syms", []).append(sym)   # batch78 (P2)
                        _ledger_open(uid, sym, "long", epx, amt, {**extra, "mkt": "futures" if _fut_long else "spot"})
                        await _notify(send_fn, uid, _open_msg(sym, "long_fut" if _fut_long else "long", epx, amt, extra, rr, ev_mult))
                continue
            # Short: فقط حين يميل /signal للهبوط بوضوح (ثقة الشراء ≤ 35% = ميل هبوط ≥ 65%)
            if float(rec.get("confidence") or 1) > 0.35:
                summary["waits"] += 1 if rec else 0
                continue
            if _fut_long:
                recf = rec   # تحليل Futures موجود أصلاً (أصل بلا Spot)
            else:
                try:
                    recf = await headless_signal_b68(engine, sym, "futures", uid, bot_data)
                except Exception as e:
                    logger.info(f"auto_b68 {sym}: /signal futures فشل — {type(e).__name__}")
                    continue
            sh = (recf or {}).get("short") or {}
            ok2, why2, rr2 = short_gate_b68(sh, px)
            if not ok2:
                summary["waits"] += 1
                _near_add_b73(summary, sym, "Short", 1.0 - _cf73, why2,
                              fails=(sh.get("fails_b86") if not sh.get("eligible") else None))   # batch73 (H2) + b91 (HH1)
                _real_near_add_b103(summary, sym, "short", 1.0 - _cf73, px, near_one_short_b103(sh, why2, rr2))   # AC1 (ب)
                continue
            # AC1 (أ) b103: تنبيه «فرصة للحساب الحقيقي» للمالك — قبل فتح صفقة القياس الافتراضية
            await _real_opp_b103(engine, send_fn, summary, {
                "sym": sym, "side": "short", "px": px * (1 - AUTO_SLIP), "rr": rr2,   # AG1 (b105): سعر تنفيذ القياس
                "conf": float(sh.get("bear_conf") or 0),   # AI1 (b106): كان مُعلَّقاً خطأً في b105
                "sl": float(sh["sl"]), "tp1": float(sh["tp1"]), "tp2": float(sh.get("tp2") or sh["tp1"]),
                "n_conf": sh.get("n_flags"), "why": sh.get("reason") or "",
                "size_frac": AUTO_BASE_PCT * max(0.0, min(1.0, float(sh.get("size_factor") or 0))) * max(0.0, min(1.0, ev_mult))})
            mg = size_usd_b68(vw.total_value, float(sh.get("size_factor") or 0), ev_mult)
            mg = min(mg, vw.balance * 0.95)
            if mg < AUTO_MIN_USD:
                continue
            pid = uuid.uuid4().hex[:10]
            epx = px * (1 - AUTO_SLIP)
            extra = {"logic_ver": AUTO_LOGIC_VER, "pos_id": pid, "auto": True,
                     "tp1": float(sh["tp1"]), "tp2": float(sh.get("tp2") or sh["tp1"]), "tp1_done": False,
                     "regime": rec.get("regime_bucket", ""), "rr_entry": round(rr2, 3),
                     "conf_entry": float(sh.get("bear_conf") or 0),
                     "e_short_flags": sh.get("n_flags"), **entry_snapshot_b80(rec, ev_mult)}   # batch80
            r = vw.open_futures(sym, "short", epx, mg, 1, stop_loss=float(sh["sl"]),
                                take_profit=float(sh.get("tp2") or sh["tp1"]), max_hold_hours=AUTO_HOLD_HOURS, extra=extra,
                                key=VirtualWallet.auto_key(sym))
            if r.get("ok"):
                _sid = _track_open_b68(engine, uid, sym, "short", epx, {**extra, "stop_loss": float(sh["sl"])},
                                       sh.get("bear_conf"), sh.get("n_flags"))
                if _sid and (r.get("key") or sym) in vw.futures:
                    vw.futures[r.get("key") or sym]["signal_id"] = _sid
                _sm().save_virtual_wallet(uid, vw.to_dict())
                summary["short"] += 1
                summary.setdefault("opened_syms", []).append(sym)   # batch78 (P2)
                _ledger_open(uid, sym, "short", epx, mg, extra)
                await _notify(send_fn, uid, _open_msg(sym, "short", epx, mg, {**extra, "stop_loss": float(sh["sl"])}, rr2, ev_mult))
    # AC1 (ب) b103: «فرصة تقترب» للمالك — بعد الدورة، دون الأصول التي وصلها تنبيه (أ) الآن
    try:
        _nr = [n for n in summary.get("real_near") or [] if n["sym"] not in (summary.get("real_opp_syms") or [])]
        logger.info(f"AC1 b103 near: {len(_nr)} مرشح " + (", ".join(f"{n['sym']}:{n['side']}" for n in _nr) if _nr else "—"))
        if _nr:
            if getattr(engine, "_post_scan_near_b108", False):   # AJ2 (b108): يُرسل بعد تقرير المسح
                engine._near_pending_b108 = (send_fn, _nr)
            else:
                summary["real_near_sent"] = await send_near_b103(engine, send_fn, _nr)
    except Exception as e:
        logger.warning(f"AC1 b103 near: {e}")
    logger.info(f"auto_b68 cycle: {summary}")
    return summary


def _real_near_add_b103(summary: dict, sym: str, side: str, score: float, px: float, missing: str) -> None:
    """AC1 (ب) b103: يسجّل أصلاً ينقصه شرط واحد — مرة لكل أصل وجهة في الدورة. تنبيه فقط."""
    if not missing:
        return
    try:
        lst = summary.setdefault("real_near", [])
        if not any(x["sym"] == sym and x["side"] == side for x in lst):
            lst.append({"sym": sym, "side": side, "score": float(score), "px": float(px), "missing": missing})
    except Exception:
        pass


async def _real_opp_b103(engine, send_fn, summary: dict, o: dict) -> None:
    """AC1 (أ) b103: مرة لكل أصل في الدورة (لا لكل حساب قياس)."""
    try:
        done = summary.setdefault("real_opp_syms", [])
        if o["sym"] in done:
            return
        done.append(o["sym"])
        try:
            _nx = engine.scheduler.next_scan_ar().replace("⏰ المسح القادم:", "").split("—")[0].strip()
        except Exception:
            _nx = ""
        await send_opp_b103(engine, send_fn, o, _nx)
    except Exception as e:
        logger.warning(f"AC1 b103 opp: {e}")


def _track_open_b68(engine, uid, sym, side, px, extra, conf_pct, n_conf) -> str:
    """ربط الصفقة الآلية بسجل الإشارات (/performance) — كما كان المسار القديم (perf_funnel_fix)."""
    try:
        tr = getattr(engine, "signal_tracker", None)
        if not tr:
            return ""
        sid = tr.log_signal(symbol=sym, setup_type=f"auto_{side}_b68", direction=side,
                            confidence_pct=float(conf_pct or 0) * 100, confirmations_met=int(n_conf or 0),
                            entry_price=px, sl_price=float(extra.get("stop_loss") or 0),
                            tp1_price=float(extra.get("tp1") or 0), tp2_price=float(extra.get("tp2") or 0),
                            regime=str(extra.get("regime") or ""), user_id=uid)
        if sid:
            tr.mark_executed(sid)
        return sid or ""
    except Exception as e:
        logger.debug(f"auto_b68 tracker: {e}")
        return ""


def _track_close_b68(engine, sid, px, why):
    try:
        if sid and getattr(engine, "signal_tracker", None):
            engine.signal_tracker.close_signal(sid, px, {"sl": "closed_sl", "time": "expired"}.get(why, "closed_tp"))
    except Exception as e:
        logger.debug(f"auto_b68 tracker close: {e}")


def _auto_open_b70(vw, sym: str) -> bool:
    """مركز قياس مفتوح على الأصل: بالمفتاح المستقل، أو بمفتاح الرمز لمراكز قياس قديمة (قبل الدفعة 70)."""
    from core.virtual_wallet import VirtualWallet as _VW
    if _VW.auto_key(sym) in vw.futures:
        return True
    for book in (vw.futures, vw.positions):
        p = book.get(sym)
        if p and p.get("logic_ver"):
            return True
    return False


def rr_fmt_b79(x: float) -> str:
    """batch79 (Q1): دون 1 يُقرَّب للأسفل (0.996 ⇒ «0.99») — كان «1:1.00 < 1:1» يبدو تناقضاً."""
    import math
    x = float(x or 0)
    return f"{math.floor(x * 100) / 100:.2f}" if 0 < x < 1 else f"{x:.2f}"


def autofail_reason_b79(rec: dict) -> str:
    """batch78/79 (P1/Q1): سبب AUTO-FAIL الفعلي من قرار /signal (البوابات G1–G5) بدل افتراض أنه R/R دائماً. عرض فقط."""
    import re as _re79
    vr = str((rec or {}).get("verdict_reason") or "")
    g = _re79.search(r"\bG(\d)\b", vr)
    gate = g.group(1) if g else "3"
    if gate == "1":
        return "بنية الصفقة فاشلة (AUTO-FAIL): خطأ في بناء الإشارة — لا أساس للدخول"
    if gate == "2":
        return "بنية الصفقة فاشلة (AUTO-FAIL): خطة الصفقة ناقصة (الدخول أو الوقف أو الإلغاء)"
    if gate == "4":
        return "بنية الصفقة فاشلة (AUTO-FAIL): أصل اصطناعي بسيولة ضعيفة والسوق مغلق — خطر فجوة سعرية"
    if gate == "5":
        return "بنية الصفقة فاشلة (AUTO-FAIL): أصل حديث الإدراج — مؤشراته غير موثوقة"
    # batch82 (W2): الرقم من تذييل G3 أولاً (مقرَّب للأسفل من القيمة غير المقرّبة) — rec["rr"] مقرَّب لمنزلة واحدة (0.9)
    m = _re79.search(r"R/R = 1:([\d.]+)", vr)
    rr = float(m.group(1)) if m else float((rec or {}).get("rr") or 0)
    return ("بنية الصفقة فاشلة (AUTO-FAIL): " + (f"R/R المرجّح 1:{rr_fmt_b79(rr)} < 1:1" if 0 < rr < 1 else "R/R المرجّح < 1:1")
            + " — الأهداف قريبة مقارنة بالوقف")


def _wait_reason_b77(rec: dict, conf: float) -> str:
    """batch77 (O2): سبب «انتظار» الفعلي من قرار /signal المحفوظ (سبب الحجم 0% أو وسم القرار) بدل نص عام
    («الثقة 67% دون حد الدخول…» كان مضللاً فوق حد الدخول). عرض فقط."""
    import re as _re77
    for k in ("size_rule", "decision_label", "verdict_reason"):
        t = str((rec or {}).get(k) or "").strip()
        t = _re77.sub(r"^[\s0%.,—\-:|]+", "", t).replace("*", "").strip()
        if t and t not in ("انتظار", "⏳ انتظار"):
            return t
    return f"حكم /signal «انتظار» (الثقة {conf:.0%})"


def _near_add_b73(summary: dict, sym: str, side: str, score: float, why: str, fails=None) -> None:
    """batch73 (H2): يسجّل أقرب الأصول للدخول (أعلى ثقة شراء أو أعلى ميل هبوط) وسبب الرفض — للعرض فقط.
    b91 (HH1): fails = كل الشروط الناقصة (Short: fails_b86) — لا الأول وحده."""
    try:
        lst = summary.setdefault("near", [])
        if any(x["sym"] == sym for x in lst):
            return
        fl = [str(f)[:120] for f in (fails or []) if f] or ([str(why or "")[:120]] if why else [])
        lst.append({"sym": sym, "side": side, "score": float(score), "why": str(why or "")[:120], "fails": fl})   # batch79 (Q2): كان 70 فيقطع «قريبة مقارنة بالوقف»
    except Exception:
        pass


_BLOCKERS_B91 = ("السيناريو ملغى", "ذروة بيع، لا Short")


def _near_fails_b91(x: dict) -> list:
    return list(x.get("fails") or ([x["why"]] if x.get("why") else []))


def _near_desc_b91(x: dict) -> str:
    """b91 (HH1ج): الموانع «مانع:»، والباقي «ينقصه: …» أو «ينقصه N شروط: أول اثنين و…»."""
    fl = _near_fails_b91(x)
    blk = [f for f in fl if any(b in f for b in _BLOCKERS_B91)]
    rest = [f for f in fl if f not in blk]
    parts = []
    if blk:
        parts.append("مانع: " + "، ".join(blk))
    if rest:
        if len(rest) == 1:
            parts.append(("وينقصه: " if blk else "ينقصه: ") + rest[0])
        else:
            parts.append(("وينقصه" if blk else "ينقصه") + f" {len(rest)} شروط: " + "، ".join(rest[:2]) + (" و…" if len(rest) > 2 else ""))
    return " — ".join(parts) if parts else "—"


def near_text_b73(sm: dict, k: int = 3) -> str:
    try:
        # batch74 (L1): الأقرب فعلاً = الأقل نقصاً — b91 (HH1ب): الموانع آخراً، ثم الأقل شروطاً ناقصة، ثم التأكيدات، ثم الميل
        import re as _re74
        def _cn74(x):
            m = _re74.search(r"تأكيدات[^\d]*(\d+)/(\d+)", " ".join(_near_fails_b91(x)))
            return int(m.group(1)) if m else 0
        def _blk(x):
            return any(any(b in f for b in _BLOCKERS_B91) for f in _near_fails_b91(x))
        lst = sorted((sm or {}).get("near") or [],
                     key=lambda x: (_blk(x), len(_near_fails_b91(x)) or 1, -_cn74(x), -x["score"]))[:k]
        if not lst:
            return ""
        return "\n🔎 الأقرب للدخول: " + "؛ ".join(
            f"{x['sym']} {x['side']} ({'ثقة' if x['side'] == 'شراء' else 'ميل هبوط'} {x['score']:.0%}) — {_near_desc_b91(x)}"
            for x in lst)
    except Exception:
        return ""


def scan_line_b68(sm: dict) -> str:
    """سطر تقرير المسح: ما فعله التداول الآلي في هذه الدورة (يظهر دائماً، حتى بلا صفقات)."""
    sm = sm or {}
    if not sm.get("users"):
        return "🤖 التداول الآلي: غير مُفعَّل لأي حساب — /autotrade on"
    if sm.get("skipped", {}).get("event"):
        return f"🤖 التداول الآلي: لا دخول الآن — {sm['skipped']['event']} (سقف التعرض 0%)"
    if not sm.get("universe"):
        return "⚠️ التداول الآلي: تعذّر جلب قائمة العملات — لم يُفحص شيء في هذه الدورة"
    # batch68g: العدّ بالأصول لا بالحساب×الأصل (كان «فُحص 98 من 33» مع 3 حسابات مفعّلة)
    _ns = len(sm.get("scanned_syms") or []) or sm.get("scanned", 0)
    _nu = int(sm.get("users") or 1)
    t = (f"🤖 التداول الآلي (حكم /signal الكامل — مجمّد b68): فُحص {_ns} من {sm['universe']} أصلاً"
         + (f" لـ{_nu} حسابات" if _nu > 1 else "")
         + " (أكبر 20 عملة + النفط والذهب والفضة و10 أسهم مرمّزة — كلها Futures 1x للجهتين) — "
         f"شراء {sm.get('long', 0)} / Short {sm.get('short', 0)}")
    if not (sm.get("long") or sm.get("short")):
        t += " — لا أصل استوفى حكم «دخول» الآن"
    _es = sm.get("err_syms") or []
    if _es:
        t += f" (تعذّر تحليل {len(_es)}: {'، '.join(_es[:12])})"
    elif sm.get("errors"):
        t += f" (تعذّر تحليل {sm['errors']})"
    _hs = sm.get("held_syms") or []
    if _hs:   # batch70: سبب الأصل المتخطّى (كان «فُحص 32 من 33» بلا تفسير)
        t += f" (تُخطّي {len(_hs)}: {'، '.join(_hs[:8])} — مركز قياس مفتوح عليه)"
    _np = sm.get("no_perp") or []
    if _np:
        t += f" (استُبعد لعدم وجود عقد دائم على OKX وحلّ محله التالي: {'، '.join(_np[:8])})"
    if float(sm.get("ev_mult", 1) or 1) < 1:
        t += f" | سقف التعرض {float(sm['ev_mult']):.0%}"
    return t + " — /autostats" + (near_text_b73(sm) if not (sm.get("long") or sm.get("short")) else "")


def entry_snapshot_b80(rec: dict, ev_mult: float) -> dict:
    """batch80: لقطة الدخول (قرار /signal + السوق) لكل صفقة قياس — للتعلّم والقياس اللاحق. تخزين فقط."""
    r = rec or {}
    keys = ("verdict", "size_pct", "atr_pct", "setup_score", "n_conf", "rsi", "vol_ratio", "fear_greed",
            "cvd_available", "sl_pct", "tp1_pct", "tp2_pct", "support_gap_pct", "regime_desc", "mkt")
    out = {f"e_{k}": r.get(k) for k in keys if r.get(k) not in (None, "")}
    out["e_decision"] = str(r.get("decision_label") or r.get("size_rule") or "")[:120]
    out["e_ev_mult"] = float(ev_mult or 1)
    out["e_gate_rev"] = GATE_REV   # AF4 (b105): الصفقات قبله بلا هذا الوسم (BTC 21 نوفمبر 09:04 فُتحت قبل الإصلاح)
    return out


def _ledger_open(uid, sym, side, px, amt, extra):
    ledger_add(uid, {"ev": "open", "pos_id": extra.get("pos_id"), "symbol": sym, "side": side, "entry": px,
                     "amount": round(amt, 2), "sl": extra.get("stop_loss"), "tp1": extra.get("tp1"),
                     "tp2": extra.get("tp2"), "regime": extra.get("regime", ""), "rr": extra.get("rr_entry"),
                     "conf": extra.get("conf_entry"),
                     **{k: v for k, v in extra.items() if k.startswith("e_")},   # batch80: لقطة الدخول الكاملة
                     "ts": time.time(), "ver": AUTO_LOGIC_VER})


_AR_DAYS_QQ1 = ("الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
_AR_MONTHS_QQ1 = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")


def _md_safe_qq1(t) -> str:
    return str(t or "").replace("*", "").replace("_", " ").replace("`", "").replace("[", "(").replace("]", ")").strip()


def _open_msg(sym, side, px, amt, extra, rr, ev_mult, now_ts: float = None) -> str:
    """QQ1 (b98، رحال 2026-11-20): بطاقة فتح تفصيلية بنمط إشعار الإغلاق (الدفعة 97) — تُرسل فور الفتح
    (قرار b68: القياس آلي دون موافقة مسبقة). عرض فقط: البيانات نفسها المحفوظة في سجل الفتح ولقطة الدخول."""
    from handlers.analysis import _fmt_price
    short = side == "short"
    lev = 1
    mkt = "Spot" if side == "long" else "Futures 1x"
    head = "🔴 Short" if short else "🟢 Long"
    L = [f"🤖 *صفقة آلية افتراضية* — {sym} {head} {mkt}",
         f"• الدخول: {_fmt_price(px)} | الهامش: ${amt:,.2f}"]

    def _lvl(name, v, frac):
        try:
            v = float(v or 0)
            if v <= 0 or px <= 0:
                return None
            mv = (v / px - 1) * 100 * (-1 if short else 1)
            usd = amt * lev * frac * mv / 100   # TP1 على 60% من المركز وTP2 على الباقي 40%
            return f"• {name}: {_fmt_price(v)} ({mv:+.2f}% ⇒ {'+' if usd >= 0 else '-'}${abs(usd):,.2f} قبل الرسوم)"
        except Exception:
            return None
    for ln in (_lvl("🛑 الوقف (المركز كاملاً)", extra.get("stop_loss"), 1.0),
               _lvl("🎯 TP1 (يُغلق 60%)", extra.get("tp1"), 0.6),
               _lvl("🎯 TP2 (الباقي 40%)", extra.get("tp2"), 0.4)):
        if ln:
            L.append(ln)
    if extra.get("tp1"):
        L.append(f"  ↪️ بعد TP1 يصبح الوقف = سعر TP1 {_fmt_price(extra.get('tp1'))} للباقي")
    _rr = f"• R/R المرجّح عند التنفيذ: 1:{rr:.2f}"
    try:
        _cf = float(extra.get("conf_entry") or 0)
        _cf = _cf * 100 if _cf <= 1 else _cf
        if _cf > 0:
            _rr += f" | {'ميل الهبوط' if short else 'الثقة'} {_cf:.0f}%"
    except Exception:
        pass
    _nc = extra.get("e_short_flags") if short else extra.get("e_n_conf")
    if _nc is not None:
        _rr += f" | التأكيدات {_nc}/4"
    L.append(_rr)
    ctx = []
    if extra.get("e_regime_desc"):
        ctx.append(_md_safe_qq1(extra.get("e_regime_desc"))[:60])
    if extra.get("e_rsi") is not None:
        try:
            ctx.append(f"RSI {float(extra['e_rsi']):.0f}")
        except Exception:
            pass
    if extra.get("e_atr_pct") is not None:
        try:
            ctx.append(f"ATR {float(extra['e_atr_pct']):.1f}%")
        except Exception:
            pass
    if extra.get("e_fear_greed") is not None:
        ctx.append(f"F&G {extra.get('e_fear_greed')}")
    if ctx:
        L.append("• السوق عند الدخول: " + " · ".join(ctx))
    if extra.get("e_decision"):
        L.append("• سبب الدخول: " + size_reason_ar_b108(_md_safe_qq1(extra.get("e_decision")))[:120])   # AH1 (b108)
    if ev_mult < 1:
        L.append(f"• سقف التعرض المطبّق: {ev_mult:.0%} (حدث اقتصادي قريب)")
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        ex = _dt.fromtimestamp((now_ts or time.time()) + AUTO_HOLD_HOURS * 3600, _tz.utc) + _td(hours=3)
        L.append(f"• ⏰ الخروج الزمني إن لم يُصب هدف أو وقف: {_AR_DAYS_QQ1[ex.weekday()]} {ex.day} "
                 f"{_AR_MONTHS_QQ1[ex.month - 1]} {ex:%H:%M} (KSA) — بعد {int(AUTO_HOLD_HOURS // 24)} أيام")
    except Exception:
        pass
    L.append("🎮 افتراضي — /autostats لنتائج القياس | /vfutures للمركز")
    return "\n".join(L)


async def _notify(send_fn, uid, text):
    if not send_fn:
        return
    try:
        await send_fn(text, user_id=uid, parse_mode="Markdown")
    except Exception:
        pass


# ── الخروج (كل 5 دقائق) ─────────────────────────────────────────────────
async def _minute_bars_b68(engine, sym: str, since_ts: float) -> list:
    """batch68f: شموع الدقيقة من العقد الدائم منذ آخر فحص — [(ts, high, low)] تصاعدياً.
    تكشف لمس الهدف/الوقف بين فحصين (ذيول الشموع) بدل السعر اللحظي وحده."""
    try:
        from core.data_layer import _fetch
        n = int(max(2, min(300, (time.time() - float(since_ts)) / 60 + 2)))
        url = f"https://www.okx.com/api/v5/market/candles?instId={str(sym).upper()}-USDT-SWAP&bar=1m&limit={n}"
        data = await _fetch(engine.data_layer.session, url, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        out = []
        for r in rows or []:
            ts = float(r[0]) / 1000.0
            if ts + 60 > float(since_ts):
                out.append((ts, float(r[2]), float(r[3])))
        return sorted(out)
    except Exception:
        return []


def walk_exits_b68(side: str, bars: list, sl: float, tp1: float, tp2: float, tp1_done: bool) -> list:
    """batch68f: يمرّ على الشموع بالترتيب ويُرجع أحداث الخروج [(kind, level)].
    قرار رحال 2026-11-06: بعد تحقق الهدف 1 يصبح سعر الهدف 1 نفسه هو الوقف الجديد للباقي.
    تحفظ القياس: إن لمست شمعة الوقف والهدف معاً يُحتسب الوقف أولاً (لا يُعرف الترتيب داخل الدقيقة)."""
    ev = []
    long = side != "short"
    for _ts, hi, lo in bars:
        hit_sl = sl > 0 and ((lo <= sl) if long else (hi >= sl))
        hit_t1 = (not tp1_done) and tp1 > 0 and ((hi >= tp1) if long else (lo <= tp1))
        hit_t2 = tp2 > 0 and ((hi >= tp2) if long else (lo <= tp2))
        if hit_sl:
            ev.append(("sl" if not tp1_done else "sl_tp1", sl))
            return ev
        if hit_t1:
            ev.append(("tp1", tp1))
            tp1_done = True
            sl = tp1
            if hit_t2:
                ev.append(("tp2", tp2))
                return ev
            continue
        if tp1_done and hit_t2:
            ev.append(("tp2", tp2))
            return ev
    return ev


async def check_auto_exits_b68(engine, send_fn=None) -> int:
    """كل 5 دقائق: خروج مراكز القياس الآلي من شموع الدقيقة منذ آخر فحص (لا السعر اللحظي وحده).
    الهدف 1 يُغلق 60% بسعره (أمر محدد)، ثم يصبح سعر الهدف 1 وقفاً للباقي؛ الهدف 2 يُغلق الباقي بسعره؛
    الوقف يُنفَّذ بسعره ناقص انزلاق 0.1% (أمر سوق)؛ الخروج الزمني بعد 5 أيام بالسعر الحالي."""
    from core.virtual_wallet import VirtualWallet
    closed = 0
    now = time.time()
    for uid in (_sm().get_autotrade_users() or []):
        try:
            wd = _sm().get_virtual_wallet(uid) or {}
            if not wd:
                continue
            vw = VirtualWallet(wd)
            notes, changed = [], False
            books = [("spot", s) for s, p in vw.positions.items() if p.get("logic_ver")] + \
                    [("fut", s) for s, p in vw.futures.items() if p.get("logic_ver")]
            for book, sym in books:      # sym = مفتاح المحفظة (مثل XAG@AUTO)؛ _bs = الرمز الحقيقي للبيانات
                pos = (vw.positions if book == "spot" else vw.futures).get(sym)
                if not pos:
                    continue
                _bs = VirtualWallet.base_symbol(sym, pos) if book == "fut" else sym
                side = "long" if book == "spot" else pos.get("side", "long")
                short = side == "short"
                try:
                    _op = datetime.fromisoformat(str(pos.get("opened_at")))
                    _op_ts = (_op if _op.tzinfo else _op.replace(tzinfo=timezone.utc)).timestamp()
                except Exception:
                    _op_ts = now - 300
                since = float(pos.get("last_chk_ts") or _op_ts)
                bars = await _minute_bars_b68(engine, _bs, since)
                pd = await engine.data_layer.get_price(_bs, mkttype="futures")
                px = float((pd or {}).get("price") or 0)
                if not bars and px > 0:
                    bars = [(now, px, px)]   # احتياط: السعر الحالي إن تعذّرت شموع الدقيقة
                # batch80: أقصى صعود/هبوط بلغه السعر أثناء الصفقة (MFE/MAE) — للتعلّم؛ لا يغيّر الخروج
                try:
                    if bars:
                        _hi80 = max(b[1] for b in bars); _lo80 = min(b[2] for b in bars)
                        _fav, _adv = (_lo80, _hi80) if short else (_hi80, _lo80)
                        _pf, _pa = pos.get("mfe_px"), pos.get("mae_px")
                        pos["mfe_px"] = (min(_pf, _fav) if short else max(_pf, _fav)) if _pf else _fav
                        pos["mae_px"] = (max(_pa, _adv) if short else min(_pa, _adv)) if _pa else _adv
                except Exception:
                    pass
                sl, tp1, tp2 = float(pos.get("stop_loss") or 0), float(pos.get("tp1") or 0), float(pos.get("tp2") or 0)
                events = walk_exits_b68(side, bars, sl, tp1, tp2, bool(pos.get("tp1_done")))
                if not events:
                    expired = vw.is_hold_expired(sym) if book == "spot" else (vw.futures_exit_reason(sym, px) == "time" if px > 0 else False)
                    if expired and px > 0:
                        events = [("time", px)]
                for kind, lvl in events:
                    if kind in ("sl", "sl_tp1"):
                        xp = lvl * (1 + AUTO_SLIP) if short else lvl * (1 - AUTO_SLIP)
                    elif kind == "time":
                        xp = lvl * (1 + AUTO_SLIP) if short else lvl * (1 - AUTO_SLIP)
                    else:
                        xp = lvl   # الهدف = أمر محدد بسعره
                    if book == "spot":
                        qty = float(vw.positions[sym]["quantity"]) * 0.6 if kind == "tp1" else None
                        r = vw.sell(sym, xp, qty)
                        live = vw.positions
                    elif kind == "tp1":
                        r = vw.reduce_futures(sym, xp, 0.6, "tp1")
                        live = vw.futures
                    else:
                        r = vw.close_futures(sym, xp, {"tp2": "tp", "sl_tp1": "sl"}.get(kind, kind))
                        live = vw.futures
                    if not r.get("ok"):
                        break
                    changed = True
                    t = r.get("trade", {})
                    if kind == "tp1" and sym in live:
                        live[sym]["tp1_done"] = True
                        live[sym]["stop_loss"] = tp1   # قرار رحال: الوقف الجديد = سعر الهدف 1
                    final = sym not in live
                    _ledger_close(uid, t, kind, final=final, pos=pos)
                    if final:
                        _track_close_b68(engine, pos.get("signal_id"), xp, kind)
                    closed += 1
                    notes.append(_close_msg(_bs, side, kind, t, pos=pos, final=final))
                    if final:
                        break
                _book = vw.positions if book == "spot" else vw.futures
                if sym in _book:
                    _book[sym]["last_chk_ts"] = now
                    changed = True
            if changed:
                _sm().save_virtual_wallet(uid, vw.to_dict())
                for n in notes:
                    await _notify(send_fn, uid, n)
        except Exception as e:
            logger.debug(f"auto exits uid={uid}: {e}")
    return closed


def close_details_b80(pos: dict, now: float = None) -> dict:
    """batch80: تفاصيل الإغلاق للتعلّم — مدة الصفقة وأقصى صعود/هبوط بلغه السعر كنسبة من الدخول."""
    out = {}
    try:
        p = pos or {}
        ep = float(p.get("entry") or p.get("entry_price") or 0)
        short = p.get("side") == "short"
        if ep > 0:
            for k, nm in (("mfe_px", "mfe_pct"), ("mae_px", "mae_pct")):
                v = float(p.get(k) or 0)
                if v > 0:
                    out[nm] = round(((ep - v) if short else (v - ep)) / ep * 100, 3)
        _op = datetime.fromisoformat(str(p.get("opened_at")))
        _op = _op if _op.tzinfo else _op.replace(tzinfo=timezone.utc)
        out["held_h"] = round(((now or time.time()) - _op.timestamp()) / 3600.0, 2)
    except Exception:
        pass
    return out


def _ledger_close(uid, t: dict, why: str, final: bool, pos: dict = None):
    ledger_add(uid, {"ev": "close", "pos_id": t.get("pos_id"), "symbol": t.get("symbol"), "side": t.get("side", "long"),
                     "exit": t.get("price"), "pnl": float(t.get("pnl") or 0), "pnl_pct": t.get("pnl_pct"),
                     "qty": t.get("quantity"), "reason": why, "final": bool(final),
                     "regime": t.get("regime", ""), **close_details_b80(pos),   # batch80
                     "ts": time.time(), "ver": AUTO_LOGIC_VER})


def _fmt_px_pp1(v) -> str:
    v = float(v or 0)
    return f"${v:,.2f}" if v >= 100 else (f"${v:,.4f}" if v >= 1 else f"${v:.6g}")


def _close_msg(sym, side, why, t, pos: dict = None, final: bool = True) -> str:
    """PP1 (b97، رحال 2026-11-20): إشعار إغلاق القياس بتفاصيل السجل نفسها — الدخول ⇒ الخروج، الصافي $ و% من الهامش،
    الرسوم، المدة، أفضل/أسوأ حركة خلال الصفقة، وللإغلاق الجزئي الكمية والوقف الجديد. عرض فقط."""
    _w = {"sl": "🛑 وقف الخسارة", "tp1": "🎯 الهدف 1 (60%)",
          "tp2": "🎯 الهدف 2 (الباقي 40%)", "sl_tp1": "🔒 الوقف المنقول إلى سعر الهدف 1 (الباقي 40%)",
          "time": "⏰ الخروج الزمني"}.get(why, why)
    p = float(t.get("pnl") or 0)
    lines = [f"🤖 *إغلاق آلي افتراضي* — {sym} {'Long' if side == 'long' else 'Short'} "
             f"{int(float(t.get('leverage') or (pos or {}).get('leverage') or 1))}x"
             + (" (جزئي)" if not final else ""),
             f"• السبب: {_w}"]
    try:
        ep = float(t.get("entry") or (pos or {}).get("entry") or 0)
        xp = float(t.get("price") or 0)
        if ep > 0 and xp > 0:
            mv = (xp / ep - 1) * 100 * (-1 if side == "short" else 1)
            lines.append(f"• الدخول: {_fmt_px_pp1(ep)} → الخروج: {_fmt_px_pp1(xp)} (حركة السعر على المركز {mv:+.2f}%)")
    except Exception:
        pass
    _pct = t.get("pnl_pct")
    _fees = float(t.get("fees") or 0)
    lines.append(f"• الصافي: {'+' if p >= 0 else '-'}${abs(p):,.2f}"
                 + (f" ({float(_pct):+.1f}% من الهامش)" if _pct is not None else "")
                 + (f" — بعد رسوم ${_fees:,.2f} وانزلاق الإغلاق" if _fees > 0 else " — بعد الرسوم والانزلاق"))
    d = close_details_b80(pos) if pos else {}
    if d.get("held_h") is not None:
        h = float(d["held_h"])
        lines.append(f"• المدة: {h / 24:.1f} يوم ({h:.0f} ساعة)" if h >= 24 else f"• المدة: {h:.1f} ساعة")
    if d.get("mfe_pct") is not None or d.get("mae_pct") is not None:
        _f = f"{d['mfe_pct']:+.2f}%" if d.get("mfe_pct") is not None else "—"
        _a = f"{d['mae_pct']:+.2f}%" if d.get("mae_pct") is not None else "—"
        lines.append(f"• خلال الصفقة: أفضل حركة {_f} · أسوأ حركة {_a} (من سعر الدخول)")
    if why == "tp1" and not final:
        try:
            lines.append(f"• أُغلق 60% — الباقي 40% بوقف جديد = سعر الهدف 1 {_fmt_px_pp1((pos or {}).get('tp1'))}"
                         + (f" وهدف {_fmt_px_pp1((pos or {}).get('tp2'))}" if (pos or {}).get("tp2") else ""))
        except Exception:
            pass
    lines.append("/autostats لنتائج القياس")
    return "\n".join(lines)


# ── الإحصاء ومعايير العبور ──────────────────────────────────────────────
def auto_stats_b68(ledger: list, start_capital: float = 10000.0) -> dict:
    opens = {r["pos_id"]: r for r in ledger if r.get("ev") == "open" and r.get("pos_id")}
    pnl_by = {}
    done = set()
    for r in ledger:
        if r.get("ev") == "close" and r.get("pos_id"):
            pnl_by[r["pos_id"]] = pnl_by.get(r["pos_id"], 0.0) + float(r.get("pnl") or 0)
            if r.get("final"):
                done.add(r["pos_id"])
    closed = [pid for pid in opens if pid in done]
    res = [pnl_by.get(pid, 0.0) for pid in closed]
    n = len(res)
    wins = [x for x in res if x > 0]
    losses = [x for x in res if x <= 0]
    tot = sum(res)
    # أقصى تراجع على المنحنى التراكمي (بترتيب الإغلاق)
    order = [r for r in ledger if r.get("ev") == "close"]
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in order:
        eq += float(r.get("pnl") or 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    regimes = {opens[pid].get("regime") for pid in closed if opens[pid].get("regime") not in (None, "", "غير معروف")}
    by_side = {}
    for pid in closed:
        sd = opens[pid].get("side", "long")
        b = by_side.setdefault(sd, {"n": 0, "pnl": 0.0, "w": 0})
        b["n"] += 1
        b["pnl"] += pnl_by.get(pid, 0.0)
        b["w"] += 1 if pnl_by.get(pid, 0.0) > 0 else 0
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0.0)
    dd_pct = dd / start_capital * 100 if start_capital > 0 else 0.0
    crit = [
        (f"صفقات مغلقة ≥ {CRIT_MIN_TRADES}", n >= CRIT_MIN_TRADES, f"{n}"),
        ("متوسط الصافي لكل صفقة > 0 بعد الرسوم", n > 0 and tot / n > 0, _usd_b81(tot / n if n else 0)),   # batch81 (S1)
        # batch73 (H1): بلا صفقات مغلقة لا يُعدّ الشرط متحققاً (كان ✅ مع 0 صفقات)
        (f"أقصى تراجع ≤ {CRIT_MAX_DD_PCT:.0f}% من رأس المال", n > 0 and dd_pct <= CRIT_MAX_DD_PCT, (f"{dd_pct:.1f}%" if n else "—")),
        (f"صفقات في {CRIT_MIN_REGIMES}+ حالات سوق", len(regimes) >= CRIT_MIN_REGIMES, "، ".join(sorted(regimes)) or "—"),
    ]
    return {"n": n, "open": len([p for p in opens if p not in done]), "wins": len(wins), "total": tot,
            "avg": (tot / n if n else 0.0), "pf": pf, "dd": dd, "dd_pct": dd_pct, "by_side": by_side,
            "regimes": sorted(regimes), "criteria": crit, "ready": all(c[1] for c in crit)}


async def open_rows_b81(engine, uid: int) -> list:
    """batch81 (S2): حالة مراكز القياس المفتوحة (السعر الحالي، الصافي بعد رسوم وانزلاق الإغلاق، المسافة إلى TP1 والوقف)."""
    rows = []
    try:
        from core.virtual_wallet import VirtualWallet
        vw = VirtualWallet(_sm().get_virtual_wallet(uid) or {})
        for k, p in vw.futures.items():
            if not p.get("logic_ver"):
                continue
            bs = VirtualWallet.base_symbol(k, p)
            pd = await engine.data_layer.get_price(bs, mkttype="futures") if engine else None
            px = float((pd or {}).get("price") or 0) or float(p["entry"])
            short = p.get("side") == "short"
            pxc = px * (1.001 if short else 0.999)
            net = vw.futures_pnl(k, pxc) - float(p.get("fee_open", 0) or 0) - float(p["qty"]) * pxc * vw.FUT_FEE_RATE
            mg = float(p.get("margin") or 0)
            tp1, sl = float(p.get("tp1") or 0), float(p.get("stop_loss") or 0)
            try:
                _op = datetime.fromisoformat(str(p.get("opened_at")))
                _op = _op if _op.tzinfo else _op.replace(tzinfo=timezone.utc)
                hrs = (time.time() - _op.timestamp()) / 3600.0
            except Exception:
                hrs = 0
            _mh = float(p.get("max_hold_hours") or 0) or float(getattr(VirtualWallet, "LEGACY_MAX_HOLD_HOURS", AUTO_HOLD_HOURS))
            rows.append({"sym": bs, "side": p.get("side"), "hours": hrs, "net": net,
                         "hold_left_h": (_mh - hrs if hrs > 0 else None),   # FF1: نفس قاعدة futures_exit_reason
                         "net_pct": (net / mg * 100 if mg else 0), "tp1_done": bool(p.get("tp1_done")),
                         "to_tp1_pct": (abs(tp1 - px) / px * 100 if tp1 > 0 and px > 0 else None),
                         "to_sl_pct": (abs(px - sl) / px * 100 if sl > 0 and px > 0 else None)})
    except Exception as e:
        logger.debug(f"open_rows_b81: {e}")
    return rows


def _usd_b81(x: float) -> str:
    """batch81 (S1): صيغة مبلغ واحدة «−$25.35» / «+$4.10» (كان «-$25.35» و«-25.35$» في السطر نفسه)."""
    x = float(x or 0)
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _pct_b82(x: float) -> str:
    """batch82 (U1): نسبة بعلامة «−» نفسها في المبلغ، ومنزلتان حين تكون دون 0.1% («−0.03%» بدل «-0.0%»)."""
    x = float(x or 0)
    a = abs(x)
    if a < 0.005:
        return "0.00%"
    v = f"{a:.2f}" if a < 0.1 else f"{a:.1f}"
    return f"{'+' if x > 0 else '−'}{v}%"


def hold_left_text_ff1(left_h) -> str:
    """FF1 (رحال 2026-11-19): موعد الخروج الزمني (AUTO_HOLD_HOURS) لكل مركز قياس — عرض فقط؛ الفحص كل 5 دقائق."""
    if left_h is None:
        return ""
    try:
        h = float(left_h)
    except Exception:
        return ""
    if h <= 0:
        return " | ⏰ الخروج الزمني مستحق (عند الفحص التالي خلال 5 دقائق)"
    if h < 1:
        return " | ⏰ خروج زمني خلال أقل من ساعة"
    if h < 48:
        n = int(h)   # للأسفل: لا نعد بأكثر مما بقي
        return " | ⏰ خروج زمني بعد " + ("ساعة" if n == 1 else "ساعتين" if n == 2 else f"{n} ساعات" if n <= 10 else f"{n} ساعة")
    return f" | ⏰ خروج زمني بعد {h / 24:.1f} يوم"


def open_lines_b81(open_rows: list) -> list:
    """batch81 (S2، مبدأ رحال: بيانات كل صفقة واضحة): سطر لكل مركز قياس مفتوح.
    open_rows: [{sym, side, hours, net, net_pct, to_tp1_pct, to_sl_pct, tp1_done}]"""
    out = []
    for r in open_rows or []:
        try:
            _d = r.get("hours") or 0
            _dur = (f"{_d:.0f} ساعة" if _d >= 11 else "ساعة" if round(_d) <= 1 else "ساعتين" if round(_d) == 2 else f"{round(_d)} ساعات") \
                if _d < 48 else f"{_d / 24:.1f} يوم"
            line = (f"  ↳ {r['sym']} {'Long' if r.get('side') != 'short' else 'Short'} منذ {_dur} — الصافي غير المحقق "
                    f"{_usd_b81(r.get('net', 0))} ({_pct_b82(r.get('net_pct'))})")   # batch82 (U1)
            if r.get("tp1_done"):
                line += " | ✅ TP1 تحقق"
            elif r.get("to_tp1_pct") is not None:
                line += f" | إلى TP1 {float(r['to_tp1_pct']):.1f}%"
            if r.get("to_sl_pct") is not None:
                line += f" | إلى الوقف {float(r['to_sl_pct']):.1f}%"
            line += hold_left_text_ff1(r.get("hold_left_h"))
            out.append(line)
        except Exception:
            continue
    return out


_WHY_AR_B85 = {"sl": "الوقف", "sl_tp1": "الوقف بعد TP1", "tp1": "TP1 جزئي 60%", "tp2": "TP2", "time": "خروج زمني"}


def week_summary_b85(ledger: list, now_ts: float = None, days: int = 7) -> str:
    """batch85 (AA2): ما حدث في سجل القياس خلال آخر 7 أيام — فتح وإغلاق (مع السبب) والصافي المحقق."""
    import time as _t85
    now_ts = float(now_ts or _t85.time())
    start = now_ts - days * 86400
    wk = [r for r in (ledger or []) if start <= float(r.get("ts") or 0) <= now_ts]
    opened = [r for r in wk if r.get("ev") == "open"]
    closes = [r for r in wk if r.get("ev") == "close"]
    if not opened and not closes:
        return "📅 *هذا الأسبوع:* لا فتح ولا إغلاق"
    def _sd(r):
        return "Long" if str(r.get("side", "long")) == "long" else "Short"

    def _why(r):
        w = str(r.get("reason") or "")
        return _WHY_AR_B85.get(w, w)

    parts = ["فُتحت %d" % len(opened) + (" (" + "، ".join("%s %s" % (r.get("symbol"), _sd(r)) for r in opened) + ")" if opened else "")]
    fin = [r for r in closes if r.get("final")]
    part = [r for r in closes if not r.get("final")]
    if closes:
        parts.append("أُغلقت %d" % len(fin) + (" (" + "، ".join("%s — %s" % (r.get("symbol"), _why(r)) for r in fin) + ")" if fin else ""))
        if part:
            parts.append("إغلاق جزئي %d (" % len(part) + "، ".join(str(r.get("symbol")) for r in part) + ")")
        parts.append("الصافي المحقق " + _usd_b81(sum(float(r.get("pnl") or 0) for r in closes)))
    return "📅 *هذا الأسبوع:* " + "، ".join(parts)


def format_auto_stats_b68(st: dict, open_rows: list = None) -> str:
    L = ["📊 *قياس التداول الآلي الافتراضي* — منطق /signal المجمّد (b68)", "━━━━━━━━━━━━━━━━━━"]
    n = st["n"]
    L.append(f"• صفقات مغلقة: {n} | مفتوحة: {st['open']}")
    L += open_lines_b81(open_rows)   # batch81 (S2)
    if n:
        L.append(f"• نسبة الربح: {st['wins'] / n:.0%} ({st['wins']}/{n})")
        L.append(f"• الصافي الكلي: {_usd_b81(st['total'])} | المتوسط: {_usd_b81(st['avg'])} لكل صفقة")   # batch81 (S1): صيغة واحدة
        _pf = "∞" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        L.append(f"• عامل الربح: {_pf} | أقصى تراجع: ${st['dd']:,.2f} ({st['dd_pct']:.1f}%)")
        for sd, b in st["by_side"].items():
            L.append(f"  ↳ {'Long' if sd == 'long' else 'Short Futures 1x'}: {ar_n(b['n'], 'صفقة')}، "
                     f"ربح {b['w']}/{b['n']}، صافي {_usd_b81(b['pnl'])}")
    L += ["", "🎯 *شروط العبور إلى تجربة حقيقية صغيرة*"]
    for lbl, ok, val in st["criteria"]:
        L.append(f"{'✅' if ok else '⬜'} {lbl} — الحالي: {val}")
    L.append("✅ *الشروط مستوفاة — يمكن نقاش تجربة حقيقية صغيرة*" if st["ready"]
             else "⏳ القياس مستمر — لا عبور قبل استيفاء الشروط كلها")
    L.append("\nℹ️ يحسب الصفقات الآلية بعد التجميد فقط؛ الرسوم والانزلاق محسوبة. افتراضي — لا أموال حقيقية.")
    return "\n".join(L)

"""
core/auto_trader_b68.py — التداول الآلي الافتراضي بمنطق /signal المجمّد (الدفعة 68)

قرارات رحال 2026-11-06:
  • التنفيذ الآلي = حكم /signal الكامل نفسه (يُشغَّل /signal داخلياً لكل عملة ويُقرأ قراره المحفوظ) —
    لا منطق دخول/وقف مستقل في المسح (كان: ثقة ≥65% فقط ووقف −2%/هدف +4% ثابتان).
  • المستويات: دخول/وقف/هدفان 60/40 من /signal، وخروج زمني 5 أيام.
  • النطاق: أكبر 20 عملة (بلا العملات المستقرة)، 6 مرات يومياً مع المسح الرباعي.
  • السوق: الشراء في الفوري؛ Short في Futures افتراضي برافعة 1x (تقييم Short في /signal بسوق Futures).
  • الحجم: 5% من المحفظة × (حجم /signal ÷ أقصى حجم للباقة) × سقف التعرض (70%/50% حول الأحداث الكبيرة).
  • القياس: سجل منفصل autoledger_b68 — المراكز القديمة تبقى كما هي ولا تدخل القياس.
  • الرسوم والانزلاق: Spot 0.1% لكل جهة، Futures 0.05% لكل جهة، وانزلاق 0.1% عند الدخول والخروج.
"""
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
STABLES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "GUSD", "LUSD", "FRAX",
           "MIM", "ALUSD", "SUSD", "PYUSD", "USDE", "USD1", "RLUSD", "EURC", "USDS", "USDTB", "BUIDL", "USYC",
           # batch68c: أصول مغلّفة/مرهونة تكرّر BTC/ETH (ولا سوق فوري لها على OKX) + رموز منصات بلا سيولة OKX كافية
           "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "METH", "LBTC", "SOLVBTC", "BSC-USD",
           "JITOSOL", "MSOL", "LEO", "FIGR_HELOC", "WBT"}
# معايير العبور إلى الحقيقي (المتفق عليها 2026-11-06)
CRIT_MIN_TRADES = 50
CRIT_MAX_DD_PCT = 10.0
CRIT_MIN_REGIMES = 2


def _sm():
    from core.state_manager import state_manager
    return state_manager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _regime_bucket(desc: str) -> str:
    d = str(desc or "")
    return "هابط" if "هابط" in d else "صاعد" if "صاعد" in d else "جانبي" if ("جانبي" in d or "تذبذب" in d) else "غير معروف"


# ── البوابات (نفس /trade: قرار /signal + R/R عند سعر التنفيذ) ─────────────
def long_gate_b68(rec: dict, price: float):
    """(ok, سبب، rr_now). نفس شروط /trade: لا AUTO-FAIL، لا انتظار، حجم > 0، شراء، وR/R المرجّح ≥ 1 عند السعر الحالي."""
    if not rec:
        return False, "لا قرار /signal", 0.0
    if str(rec.get("verdict", "")).upper() == "AUTO-FAIL":
        return False, "AUTO-FAIL", 0.0
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


# batch68b: قائمة احتياطية حين يتعذّر CoinGecko (كان طلب limit=40 مباشرة بعد طلب المسح يُرفض 429 فلا يُفحص شيء)
FALLBACK_TOP = ["BTC", "ETH", "XRP", "BNB", "SOL", "TRX", "DOGE", "ADA", "HYPE", "LINK",
                "BCH", "XLM", "SUI", "AVAX", "LTC", "HBAR", "TON", "DOT", "UNI", "AAVE"]


async def _universe(engine) -> list:
    coins = []
    for lim in (150, 30, 40):   # نفس مفاتيح كاش المسح الرباعي أولاً (بلا طلب جديد)
        try:
            coins = await engine.data_layer.get_top_coins(limit=lim) or []
        except Exception:
            coins = []
        if len(coins) >= AUTO_UNIVERSE:
            break
    out = []
    for c in coins:
        s = str((c or {}).get("symbol") or "").upper()
        if s and s not in STABLES and s not in out:
            out.append(s)
        if len(out) >= AUTO_UNIVERSE:
            break
    if len(out) < AUTO_UNIVERSE:
        for s_ in FALLBACK_TOP:
            if s_ not in out:
                out.append(s_)
            if len(out) >= AUTO_UNIVERSE:
                break
    return out


async def run_auto_cycle_b68(engine, send_fn=None, bot_data=None) -> dict:
    """دورة دخول واحدة لكل مستخدمي التداول الآلي. تُرجع ملخصاً للسجل."""
    from core.virtual_wallet import VirtualWallet
    from handlers.analysis import headless_signal_b68
    summary = {"scanned": 0, "long": 0, "short": 0, "skipped": {}, "universe": 0, "users": 0,
               "waits": 0, "errors": 0, "ev_mult": 1.0}
    uids = _sm().get_autotrade_users() or []
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
    summary["universe"] = len(syms)
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
            if sym in vw.positions or sym in vw.futures:
                continue
            summary["scanned"] += 1
            try:
                rec = await headless_signal_b68(engine, sym, "spot", uid, bot_data)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"auto_b68 {sym}: /signal spot فشل — {type(e).__name__}: {e}")
                continue
            if not rec:
                summary["errors"] += 1
            try:
                pd = await engine.data_layer.get_price(sym)
                px = float((pd or {}).get("price") or 0)
            except Exception:
                px = 0.0
            if px <= 0:
                continue
            ok, why, rr = long_gate_b68(rec, px)
            if ok:
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
                             "rr_entry": round(rr, 3), "conf_entry": float(rec.get("confidence") or 0)}
                    r = vw.buy(sym, epx, amt, fee_rate=AUTO_SPOT_FEE, extra=extra)
                    if r.get("ok"):
                        _sid = _track_open_b68(engine, uid, sym, "long", epx, extra, rec.get("confidence"), 0)
                        if _sid:
                            vw.positions[sym]["signal_id"] = _sid
                            vw.positions[sym]["setup_type"] = "auto_long_b68"
                        _sm().save_virtual_wallet(uid, vw.to_dict())
                        summary["long"] += 1
                        _ledger_open(uid, sym, "long", epx, amt, extra)
                        await _notify(send_fn, uid, _open_msg(sym, "long", epx, amt, extra, rr, ev_mult))
                continue
            # Short: فقط حين يميل /signal للهبوط بوضوح (ثقة الشراء ≤ 35% = ميل هبوط ≥ 65%)
            if float(rec.get("confidence") or 1) > 0.35:
                summary["waits"] += 1 if rec else 0
                continue
            try:
                recf = await headless_signal_b68(engine, sym, "futures", uid, bot_data)
            except Exception as e:
                logger.info(f"auto_b68 {sym}: /signal futures فشل — {type(e).__name__}")
                continue
            sh = (recf or {}).get("short") or {}
            ok2, why2, rr2 = short_gate_b68(sh, px)
            if not ok2:
                summary["waits"] += 1
                continue
            mg = size_usd_b68(vw.total_value, float(sh.get("size_factor") or 0), ev_mult)
            mg = min(mg, vw.balance * 0.95)
            if mg < AUTO_MIN_USD:
                continue
            pid = uuid.uuid4().hex[:10]
            epx = px * (1 - AUTO_SLIP)
            extra = {"logic_ver": AUTO_LOGIC_VER, "pos_id": pid, "auto": True,
                     "tp1": float(sh["tp1"]), "tp2": float(sh.get("tp2") or sh["tp1"]), "tp1_done": False,
                     "regime": rec.get("regime_bucket", ""), "rr_entry": round(rr2, 3),
                     "conf_entry": float(sh.get("bear_conf") or 0)}
            r = vw.open_futures(sym, "short", epx, mg, 1, stop_loss=float(sh["sl"]),
                                take_profit=float(sh.get("tp2") or sh["tp1"]), max_hold_hours=AUTO_HOLD_HOURS, extra=extra)
            if r.get("ok"):
                _sid = _track_open_b68(engine, uid, sym, "short", epx, {**extra, "stop_loss": float(sh["sl"])},
                                       sh.get("bear_conf"), sh.get("n_flags"))
                if _sid:
                    vw.futures[sym]["signal_id"] = _sid
                _sm().save_virtual_wallet(uid, vw.to_dict())
                summary["short"] += 1
                _ledger_open(uid, sym, "short", epx, mg, extra)
                await _notify(send_fn, uid, _open_msg(sym, "short", epx, mg, {**extra, "stop_loss": float(sh["sl"])}, rr2, ev_mult))
    logger.info(f"auto_b68 cycle: {summary}")
    return summary


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


def scan_line_b68(sm: dict) -> str:
    """سطر تقرير المسح: ما فعله التداول الآلي في هذه الدورة (يظهر دائماً، حتى بلا صفقات)."""
    sm = sm or {}
    if not sm.get("users"):
        return "🤖 التداول الآلي: غير مُفعَّل لأي حساب — /autotrade on"
    if sm.get("skipped", {}).get("event"):
        return f"🤖 التداول الآلي: لا دخول الآن — {sm['skipped']['event']} (سقف التعرض 0%)"
    if not sm.get("universe"):
        return "⚠️ التداول الآلي: تعذّر جلب قائمة العملات — لم يُفحص شيء في هذه الدورة"
    t = (f"🤖 التداول الآلي (حكم /signal الكامل — مجمّد b68): فُحصت {sm.get('scanned', 0)} من أكبر {sm['universe']} عملة — "
         f"شراء {sm.get('long', 0)} / Short {sm.get('short', 0)}")
    if not (sm.get("long") or sm.get("short")):
        t += " — لا عملة استوفت حكم «دخول» الآن"
    if sm.get("errors"):
        t += f" (تعذّر تحليل {sm['errors']})"
    if float(sm.get("ev_mult", 1) or 1) < 1:
        t += f" | سقف التعرض {float(sm['ev_mult']):.0%}"
    return t + " — /autostats"


def _ledger_open(uid, sym, side, px, amt, extra):
    ledger_add(uid, {"ev": "open", "pos_id": extra.get("pos_id"), "symbol": sym, "side": side, "entry": px,
                     "amount": round(amt, 2), "sl": extra.get("stop_loss"), "tp1": extra.get("tp1"),
                     "tp2": extra.get("tp2"), "regime": extra.get("regime", ""), "rr": extra.get("rr_entry"),
                     "ts": time.time(), "ver": AUTO_LOGIC_VER})


def _open_msg(sym, side, px, amt, extra, rr, ev_mult) -> str:
    from handlers.analysis import _fmt_price
    return ("🤖 *صفقة آلية افتراضية* (منطق /signal المجمّد — b68)\n"
            f"• {sym} {'🟢 شراء Spot' if side == 'long' else '🔴 Short Futures 1x'} عند {_fmt_price(px)} — ${amt:,.0f}\n"
            f"• الوقف {_fmt_price(extra.get('stop_loss'))} | TP1 {_fmt_price(extra.get('tp1'))} (60%) | TP2 {_fmt_price(extra.get('tp2'))} (40%)\n"
            f"• R/R المرجّح عند التنفيذ 1:{rr:.2f}" + (f" | سقف التعرض {ev_mult:.0%}" if ev_mult < 1 else "") + "\n"
            "🎮 افتراضي — /autostats لنتائج القياس")


async def _notify(send_fn, uid, text):
    if not send_fn:
        return
    try:
        await send_fn(text, user_id=uid, parse_mode="Markdown")
    except Exception:
        pass


# ── الخروج (كل 5 دقائق) ─────────────────────────────────────────────────
async def check_auto_exits_b68(engine, send_fn=None) -> int:
    from core.virtual_wallet import VirtualWallet
    closed = 0
    for uid in (_sm().get_autotrade_users() or []):
        try:
            wd = _sm().get_virtual_wallet(uid) or {}
            if not wd:
                continue
            vw = VirtualWallet(wd)
            notes, changed = [], False
            for sym in [s for s, p in vw.positions.items() if p.get("logic_ver")]:
                pos = vw.positions[sym]
                pd = await engine.data_layer.get_price(sym)
                px = float((pd or {}).get("price") or 0)
                if px <= 0:
                    continue
                xp = px * (1 - AUTO_SLIP)
                sl, tp1, tp2 = float(pos.get("stop_loss") or 0), float(pos.get("tp1") or 0), float(pos.get("tp2") or 0)
                why = ("sl" if sl > 0 and px <= sl else "tp2" if tp2 > 0 and px >= tp2 else
                       "tp1" if (tp1 > 0 and px >= tp1 and not pos.get("tp1_done")) else
                       "time" if vw.is_hold_expired(sym) else "")
                if not why:
                    continue
                qty = float(pos["quantity"]) * 0.6 if why == "tp1" else None
                r = vw.sell(sym, xp, qty)
                if r.get("ok"):
                    changed = True
                    t = r.get("trade", {})
                    if why == "tp1" and sym in vw.positions:
                        vw.positions[sym]["tp1_done"] = True
                    _ledger_close(uid, t, why, final=(sym not in vw.positions))
                    if sym not in vw.positions:
                        _track_close_b68(engine, pos.get("signal_id"), xp, why)
                    closed += 1
                    notes.append(_close_msg(sym, "long", why, t))
            for sym in [s for s, p in vw.futures.items() if p.get("logic_ver")]:
                pos = vw.futures[sym]
                pd = await engine.data_layer.get_price(sym)
                px = float((pd or {}).get("price") or 0)
                if px <= 0:
                    continue
                short = pos["side"] == "short"
                xp = px * (1 + AUTO_SLIP) if short else px * (1 - AUTO_SLIP)
                sl, tp1, tp2 = float(pos.get("stop_loss") or 0), float(pos.get("tp1") or 0), float(pos.get("tp2") or 0)
                hit = (lambda lv, up: lv > 0 and ((px >= lv) if up else (px <= lv)))
                why = ("sl" if hit(sl, short) else "tp2" if hit(tp2, not short) else
                       "tp1" if (hit(tp1, not short) and not pos.get("tp1_done")) else "")
                if not why:
                    why = "time" if vw.futures_exit_reason(sym, px) == "time" else ""
                if not why:
                    continue
                if why == "tp1":
                    r = vw.reduce_futures(sym, xp, 0.6, "tp1")
                    if r.get("ok"):
                        vw.futures[sym]["tp1_done"] = True
                else:
                    r = vw.close_futures(sym, xp, {"tp2": "tp"}.get(why, why))
                if r.get("ok"):
                    changed = True
                    t = r.get("trade", {})
                    _ledger_close(uid, t, why, final=(sym not in vw.futures))
                    if sym not in vw.futures:
                        _track_close_b68(engine, pos.get("signal_id"), xp, why)
                    closed += 1
                    notes.append(_close_msg(sym, pos["side"], why, t))
            if changed:
                _sm().save_virtual_wallet(uid, vw.to_dict())
                for n in notes:
                    await _notify(send_fn, uid, n)
        except Exception as e:
            logger.debug(f"auto exits uid={uid}: {e}")
    return closed


def _ledger_close(uid, t: dict, why: str, final: bool):
    ledger_add(uid, {"ev": "close", "pos_id": t.get("pos_id"), "symbol": t.get("symbol"), "side": t.get("side", "long"),
                     "exit": t.get("price"), "pnl": float(t.get("pnl") or 0), "reason": why, "final": bool(final),
                     "regime": t.get("regime", ""), "ts": time.time(), "ver": AUTO_LOGIC_VER})


def _close_msg(sym, side, why, t) -> str:
    _w = {"sl": "🛑 وقف الخسارة", "tp1": "🎯 الهدف 1 (60%)", "tp2": "🎯 الهدف 2 (الباقي)", "time": "⏰ الخروج الزمني"}.get(why, why)
    p = float(t.get("pnl") or 0)
    return (f"🤖 *إغلاق آلي افتراضي* — {sym} {'Long' if side == 'long' else 'Short'}\n"
            f"• السبب: {_w}\n• الصافي: {'+' if p >= 0 else '-'}${abs(p):,.2f} (بعد الرسوم والانزلاق)\n"
            "/autostats لنتائج القياس")


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
        ("متوسط الصافي لكل صفقة > 0 بعد الرسوم", n > 0 and tot / n > 0, f"{(tot / n if n else 0):+,.2f}$"),
        (f"أقصى تراجع ≤ {CRIT_MAX_DD_PCT:.0f}% من رأس المال", dd_pct <= CRIT_MAX_DD_PCT, f"{dd_pct:.1f}%"),
        (f"صفقات في {CRIT_MIN_REGIMES}+ حالات سوق", len(regimes) >= CRIT_MIN_REGIMES, "، ".join(sorted(regimes)) or "—"),
    ]
    return {"n": n, "open": len([p for p in opens if p not in done]), "wins": len(wins), "total": tot,
            "avg": (tot / n if n else 0.0), "pf": pf, "dd": dd, "dd_pct": dd_pct, "by_side": by_side,
            "regimes": sorted(regimes), "criteria": crit, "ready": all(c[1] for c in crit)}


def format_auto_stats_b68(st: dict) -> str:
    L = ["📊 *قياس التداول الآلي الافتراضي* — منطق /signal المجمّد (b68)", "━━━━━━━━━━━━━━━━━━"]
    n = st["n"]
    L.append(f"• صفقات مغلقة: {n} | مفتوحة: {st['open']}")
    if n:
        L.append(f"• نسبة الربح: {st['wins'] / n:.0%} ({st['wins']}/{n})")
        L.append(f"• الصافي الكلي: {'+' if st['total'] >= 0 else '-'}${abs(st['total']):,.2f} | المتوسط: {st['avg']:+,.2f}$ لكل صفقة")
        _pf = "∞" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        L.append(f"• عامل الربح: {_pf} | أقصى تراجع: ${st['dd']:,.2f} ({st['dd_pct']:.1f}%)")
        for sd, b in st["by_side"].items():
            L.append(f"  ↳ {'Long Spot' if sd == 'long' else 'Short Futures 1x'}: {b['n']} صفقة، "
                     f"ربح {b['w']}/{b['n']}، صافي {'+' if b['pnl'] >= 0 else '-'}${abs(b['pnl']):,.2f}")
    L += ["", "🎯 *شروط العبور إلى تجربة حقيقية صغيرة*"]
    for lbl, ok, val in st["criteria"]:
        L.append(f"{'✅' if ok else '⬜'} {lbl} — الحالي: {val}")
    L.append("✅ *الشروط مستوفاة — يمكن نقاش تجربة حقيقية صغيرة*" if st["ready"]
             else "⏳ القياس مستمر — لا عبور قبل استيفاء الشروط كلها")
    L.append("\nℹ️ يحسب الصفقات الآلية بعد التجميد فقط؛ الرسوم والانزلاق محسوبة. افتراضي — لا أموال حقيقية.")
    return "\n".join(L)

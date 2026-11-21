"""AC1 (b103، رحال 2026-11-21 07:17 «١. سويا ٢. فقط الخاص ٣. نفذه»): تنبيه فرص الحساب الحقيقي — للمالك في الخاص فقط.

(أ) «فرصة للحساب الحقيقي»: لحظة استيفاء حكم /signal الكامل (البوابة نفسها التي تفتح صفقة القياس الافتراضية) —
    يُرسل قبل فتح صفقة القياس، مع فحص رصيد OKX الحقيقي ونسبة الحجم وصلاحية الفرصة وتحذير «غير مثبتة».
(ب) «فرصة تقترب»: حين ينقص الأصل شرط واحد فقط (R/R قريب من 1:1 بعد اكتمال الجهة والثقة، أو شرط Short واحد بلا مانع)
    — مرة لكل أصل وجهة كل 24 ساعة.

تنبيه فقط: لا تنفيذ على الحساب الحقيقي ولا أي تغيير في منطق القرار المجمّد (b68)."""
import logging
import time

logger = logging.getLogger(__name__)

NEAR_COOLDOWN_H = 24.0      # (ب) مرة لكل أصل وجهة كل 24 ساعة
OPP_COOLDOWN_H = 12.0       # (أ) لا تكرار للفرصة نفسها في كل مسح (المسح كل 4 ساعات)
NEAR_RR_MIN = 0.90          # (ب) R/R «قريب» من 1:1
NEAR_CONF_MIN = 0.65        # (ب) الشراء: الجهة مؤكدة (حد تحديد الجهة)
VALID_PCT = 1.0             # (أ) صلاحية السعر حول الدخول
_BLOCKERS = ("السيناريو ملغى", "ذروة بيع، لا Short")


def _sm():
    from core.state_manager import state_manager
    return state_manager


def owner_uid() -> int:
    try:
        from core.access_guard import owner_id
        return int(owner_id() or 0)
    except Exception:
        return 0


def _md(t) -> str:
    return str(t or "").replace("*", "").replace("_", " ").replace("`", "").replace("[", "(").replace("]", ")").strip()


def _cool_mark(key: str, now: float = None) -> None:
    """AI1 (b106): يُسجَّل الإرسال بعد نجاحه فقط (كان يُسجَّل قبله ⇒ فشل واحد يحجب التنبيه 24 ساعة بصمت)."""
    try:
        _sm().kv_set(f"realalert_b103:{key}", f"{(now or time.time()):.0f}")
    except Exception:
        pass


def _cool_ok(key: str, hours: float, now: float = None, mark: bool = True) -> bool:
    """True إن لم يُرسل التنبيه نفسه خلال المدة؛ ويسجّل الإرسال."""
    now = now or time.time()
    try:
        last = float(_sm().kv_get(f"realalert_b103:{key}", 0) or 0)
    except Exception:
        last = 0.0
    if last and now - last < hours * 3600:
        return False
    if mark:
        try:
            _sm().kv_set(f"realalert_b103:{key}", f"{now:.0f}")
        except Exception:
            pass
    return True


async def real_balance_b103(engine, uid: int):
    """رصيد USDT الحقيقي على المنصة المربوطة (Spot ثم Futures). None ⇒ غير مربوطة/تعذّر."""
    try:
        inf = engine.get_user_exchange(uid) if engine and hasattr(engine, "get_user_exchange") else None
    except Exception:
        inf = None
    if not inf:
        return None
    out = {"name": str(inf.get("name") or "").upper(), "free": 0.0, "total": 0.0, "ok": False}
    try:
        b = await inf["exchange"].get_balance("USDT")
        out.update(free=float(b.free or 0), total=float(b.total or 0), ok=True)
    except Exception:
        pass
    if out["total"] <= 0:
        try:
            fb = await inf["exchange"].get_balance("USDT", account_type="futures")
            if float(fb.total or 0) > 0:
                out.update(free=float(fb.free or 0), total=float(fb.total or 0), ok=True, acct="Futures")
        except Exception:
            pass
    return out


def near_one_long_b103(rec: dict, why: str, rr_now: float) -> str:
    """(ب) الشراء: ينقصه شرط واحد فقط ⇒ نص الشرط، وإلا "".
    - R/R عند السعر الحالي < 1 وكل ما سواه مكتمل (البوابة وصلت لفحص R/R).
    - AUTO-FAIL بسبب R/R وحده (G3) مع جهة شراء مؤكدة (ثقة ≥ 65%)."""
    try:
        from core.auto_trader_b68 import rr_fmt_b79
        if str(why).startswith("R/R عند السعر الحالي") and NEAR_RR_MIN <= float(rr_now or 0) < 1.0:
            return f"R/R المرجّح عند السعر الحالي 1:{rr_fmt_b79(rr_now)} (المطلوب ≥ 1:1)"
        r = rec or {}
        if str(r.get("verdict", "")).upper() != "AUTO-FAIL":
            return ""
        import re
        vr = str(r.get("verdict_reason") or "")
        g = re.search(r"\bG(\d)\b", vr)
        if g and g.group(1) != "3":
            return ""
        m = re.search(r"R/R = 1:([\d.]+)", vr)
        rr = float(m.group(1)) if m else float(r.get("rr") or 0)
        conf = float(r.get("confidence") or 0)
        # AF1 (b104): «شرط واحد» = قائمة «ما الذي يغيّر الحكم؟» في بطاقة /signal لا ينقص فيها إلا R/R:
        # لا هبوط قائم، وجهة شراء مؤكدة (ثقة ≥ 65%)، وتأكيدات شراء ≥ 2/4 (كان يُفحص R/R والثقة فقط)
        try:
            n_conf = int(r.get("n_conf") or 0)
        except (TypeError, ValueError):
            n_conf = 0
        if (str(r.get("direction", "")).lower() in ("long", "bullish", "buy") and conf >= NEAR_CONF_MIN
                and not r.get("bear_confirmed") and n_conf >= 2 and NEAR_RR_MIN <= rr < 1.0):
            return f"R/R المرجّح 1:{rr_fmt_b79(rr)} (المطلوب ≥ 1:1)"
    except Exception:
        pass
    return ""


def near_one_short_b103(sh: dict, why: str, rr_now: float) -> str:
    """(ب) Short: شرط واحد ناقص بلا مانع (fails_b86)، أو R/R عند السعر الحالي قريب من 1:1 والباقي مكتمل."""
    try:
        from core.auto_trader_b68 import rr_fmt_b79
        sh = sh or {}
        if sh.get("eligible"):
            if str(why).startswith("R/R عند السعر الحالي") and NEAR_RR_MIN <= float(rr_now or 0) < 1.0:
                return f"R/R المرجّح عند السعر الحالي 1:{rr_fmt_b79(rr_now)} (المطلوب ≥ 1:1)"
            return ""
        fl = [str(f) for f in (sh.get("fails_b86") or []) if f]
        if len(fl) == 1 and not any(b in fl[0] for b in _BLOCKERS):
            if "R/R" in fl[0]:
                m = __import__("re").search(r"1:([\d.]+)", fl[0])
                if m and float(m.group(1)) < NEAR_RR_MIN:
                    return ""
            # AJ1 (b107، رحال 2026-11-21 17:12): نقص التأكيدات «قريب» فقط إن كان الناقص تأكيداً واحداً
            # (1/4 والحد 2) — 0/4 يحتاج تأكيدين (LINK/XLM/CC/AVAX 17:09 كانت تُعدّ «شرطاً واحداً»)
            mc = __import__("re").search(r"تأكيدات Short (\d+)/\d+ \(الحد الأدنى (\d+)\)", fl[0])
            if mc and int(mc.group(2)) - int(mc.group(1)) > 1:
                return ""
            return fl[0]
    except Exception:
        pass
    return ""


def _stats_line(uid: int) -> str:
    try:
        from core.auto_trader_b68 import ledger_get, auto_stats_b68, CRIT_MIN_TRADES
        st = auto_stats_b68(ledger_get(uid) or [])
        return (f"⚠️ الاستراتيجية في مرحلة القياس ({st['n']}/{CRIT_MIN_TRADES} صفقة مغلقة، رابحة {st['wins']}، "
                f"الصافي {'+' if st['total'] >= 0 else '-'}${abs(st['total']):,.2f}) — غير مثبتة بعد؛ القرار لك")
    except Exception:
        return "⚠️ الاستراتيجية في مرحلة القياس — غير مثبتة بعد؛ القرار لك"


def _bal_line(bal, need_pct: float) -> str:
    pct = max(0.0, float(need_pct or 0)) * 100
    if bal is None:
        return f"💱 الحساب الحقيقي غير مربوط — /live للربط | حجم القياس {pct:.1f}% من رأس المال"
    if bal.get("ok") is False and bal.get("total", 0) <= 0:
        return f"💱 تعذّر جلب رصيد {bal.get('name') or 'المنصة'} الآن | حجم القياس {pct:.1f}% من رأس المال"
    tot = float(bal.get("total") or 0)
    free = float(bal.get("free") or 0)
    if tot <= 0:
        return (f"💱 رصيدك الحقيقي على {bal.get('name') or 'المنصة'}: $0.00 — لا يمكن التنفيذ؛ "
                f"حجم القياس {pct:.1f}% من رأس المال (مثلاً $1,000 ⇒ ${10 * pct:,.2f})")
    amt = free * pct / 100
    return (f"💱 رصيدك الحقيقي: ${free:,.2f} متاح من ${tot:,.2f} ({bal.get('name') or ''}) — "
            f"بنفس نسبة القياس {pct:.1f}% ⇒ ≈ ${amt:,.2f}")


def opp_text_b103(o: dict, bal, uid: int, next_scan: str = "") -> str:
    """(أ) نص تنبيه «فرصة للحساب الحقيقي»."""
    from handlers.analysis import _fmt_price
    short = o.get("side") == "short"
    px = float(o.get("px") or 0)

    def _pct(v):
        try:
            v = float(v or 0)
            return f"{(v / px - 1) * 100:+.1f}%" if v > 0 and px > 0 else ""
        except Exception:
            return ""
    lo, hi = px * (1 - VALID_PCT / 100), px * (1 + VALID_PCT / 100)
    L = [f"🔔 *فرصة للحساب الحقيقي* — {o['sym']} {'🔴 Short' if short else '🟢 Long'} (Futures)",
         "━━━━━━━━━━━━━━━━━━",
         "حكم /signal الكامل مستوفى الآن — نفس الإشارة التي تفتح صفقة القياس الافتراضية",
         f"• الدخول: {_fmt_price(px)} (السعر الحالي + انزلاق 0.1% — نفس دخول صفقة القياس)",   # AG1 (b105)
         f"• 🛑 الوقف: {_fmt_price(o.get('sl'))} ({_pct(o.get('sl'))})",
         f"• 🎯 TP1 (60%): {_fmt_price(o.get('tp1'))} ({_pct(o.get('tp1'))}) ← TP2 (40%): {_fmt_price(o.get('tp2'))} ({_pct(o.get('tp2'))})"]
    try:
        from core.auto_trader_b68 import rr_fmt_b79
        ln = f"• R/R المرجّح: 1:{rr_fmt_b79(o.get('rr'))}"
    except Exception:
        ln = ""
    cf = float(o.get("conf") or 0)
    cf = cf * 100 if cf <= 1 else cf
    if cf > 0:
        ln += f" | {'ميل الهبوط' if short else 'الثقة'} {cf:.0f}%"
    if o.get("n_conf") is not None:
        ln += f" | التأكيدات {o.get('n_conf')}/4"
    if ln:
        L.append(ln)
    if o.get("why"):
        L.append("• السبب: " + _md(o.get("why"))[:120])
    L.append(_bal_line(bal, o.get("size_frac")))
    L.append(f"⏳ صالحة ما دام السعر بين {_fmt_price(lo)} و{_fmt_price(hi)} (±{VALID_PCT:.0f}%)"
             + (f" وحتى المسح القادم ({_md(next_scan)})" if next_scan else " وحتى المسح القادم"))
    L.append(_stats_line(uid))
    L.append("🔒 تنبيه فقط — لا تنفيذ آلي على حسابك الحقيقي")
    return "\n".join(L)


def near_text_b103(n: dict, bal) -> str:
    """(ب) نص تنبيه «فرصة تقترب»."""
    from handlers.analysis import _fmt_price
    short = n.get("side") == "short"
    sc = float(n.get("score") or 0)
    L = [f"⏳ *فرصة تقترب* — {n['sym']} {'🔴 Short' if short else '🟢 Long'} (Futures)",
         f"• السعر: {_fmt_price(n.get('px'))} | {'ميل الهبوط' if short else 'الثقة'} {sc:.0%}",
         "• ينقصها شرط واحد: " + _md(n.get("missing"))[:140],
         f"• للتفاصيل: /signal {n['sym']}"]
    if bal is not None and float(bal.get("total") or 0) <= 0:
        L.append(f"💱 رصيدك الحقيقي على {bal.get('name') or 'المنصة'} $0.00 — جهّز الإيداع إن أردت التنفيذ عند اكتمالها")
    elif bal is None:
        L.append("💱 الحساب الحقيقي غير مربوط — /live للربط")
    L.append(f"🔕 لا يتكرر هذا التنبيه لـ {n['sym']} قبل {int(NEAR_COOLDOWN_H)} ساعة | تنبيه فقط")
    return "\n".join(L)


async def send_opp_b103(engine, send_fn, o: dict, next_scan: str = "") -> bool:
    """(أ) يرسل للمالك وحده — قبل فتح صفقة القياس. مرة لكل أصل وجهة كل 12 ساعة."""
    uid = owner_uid()
    if not uid or not send_fn:
        return False
    _k = f"opp:{o['sym']}:{o['side']}"
    if not _cool_ok(_k, OPP_COOLDOWN_H, mark=False):
        logger.info(f"AC1 b103 opp {o['sym']} {o['side']}: محجوب — أُرسل خلال {OPP_COOLDOWN_H:g} ساعة")
        return False
    bal = await real_balance_b103(engine, uid)
    try:
        _r = await send_fn(opp_text_b103(o, bal, uid, next_scan), user_id=uid, parse_mode="Markdown")
        if _r is False:   # AI1 (b106): _send_one فشل (سُجّل «send to …» في السجل) ⇒ لا تسجيل إرسال
            raise RuntimeError("send_fn أعاد False")
        _cool_mark(_k)
        logger.info(f"AC1 b103 opp {o['sym']} {o['side']}: أُرسل للمالك")
        return True
    except Exception as e:
        logger.warning(f"AC1 b103 opp {o['sym']} {o['side']}: فشل الإرسال — {type(e).__name__}: {e}")
        return False


async def send_near_b103(engine, send_fn, items: list) -> int:
    """(ب) يرسل للمالك وحده — مرة لكل أصل وجهة كل 24 ساعة؛ لا يُرسل لأصل أُرسلت له فرصة (أ) في الدورة نفسها."""
    uid = owner_uid()
    if not uid or not send_fn or not items:
        return 0
    sent, bal, got_bal = 0, None, False
    for n in items:
        _k = f"near:{n['sym']}:{n['side']}"
        if not _cool_ok(_k, NEAR_COOLDOWN_H, mark=False):
            logger.info(f"AC1 b103 near {n['sym']} {n['side']}: محجوب — أُرسل خلال {NEAR_COOLDOWN_H:g} ساعة")
            continue
        if not got_bal:
            bal, got_bal = await real_balance_b103(engine, uid), True
        try:
            _r = await send_fn(near_text_b103(n, bal), user_id=uid, parse_mode="Markdown")
            if _r is False:   # AI1 (b106)
                raise RuntimeError("send_fn أعاد False")
            _cool_mark(_k)
            sent += 1
            logger.info(f"AC1 b103 near {n['sym']} {n['side']}: أُرسل للمالك")
        except Exception as e:
            logger.warning(f"AC1 b103 near {n['sym']} {n['side']}: فشل الإرسال — {type(e).__name__}: {e}")
    return sent

"""
trade.py — أوامر /trade Spot Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
أوامر التداول الفوري المستقلة (Spot) على المنصة المربوطة للمستخدم
متاح للمشترك الفضي فأعلى بصلاحيات مختلفة

الأوامر:
  /trade [رمز]          — فتح صفقة
  /trade_auto on/off    — تفعيل/إيقاف Auto
  /trade_stop           — إيقاف التداول
  /trade_close [رمز]    — إغلاق صفقة
  /trade_plan           — خطة أسبوعية
  /trade_plan_month     — خطة شهرية
  /trade_portfolio      — المحفظة الحالية
  /trade_history        — السجل

صلاحيات الباقات:
  ماسي+: رقمية + مُرمَّزة، تأكيد تلقائي 15 دق، تعارض مسموح
  ذهبي:  رقمية + مُرمَّزة، يدوي، تعارض مرفوض
  فضي:   رقمية فقط، يدوي، تعارض مرفوض

الدفعة 43 (إصلاح شامل):
  • الحجم يُحسب فعلاً (Risk Engine + رصيد المنصة) بدل None الذي كان يُفشل كل تنفيذ.
  • العرض والتنفيذ من نفس الاقتراح المحفوظ (لا إعادة حساب مختلفة عند التأكيد).
  • التأكيد التلقائي يتحقق من السعر/Kill Switch/الإيقاف قبل التنفيذ، ومحاولة تنفيذ واحدة
    (لا إعادة محاولات عمياء قد تكرر أمراً حقيقياً).
  • الإغلاق/الإيقاف/الخطط أزرارها تعمل، والتخزين عبر state_manager.kv_*.
"""
import asyncio
import logging
import time
import json
from datetime import datetime, timezone, timedelta
from typing import List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from core.symbol_classify import is_tokenized_x_ticker
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Auditing Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    from core.auditing_agent import audit_content, audit_financial_content
    _AUDITING = True
except ImportError:
    def audit_content(c, source="default"): return True, c
    def audit_financial_content(c, source="trade"): return True, c
    _AUDITING = False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NYSE tokens (تحذير خارج ساعات التداول)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    from handlers.analysis import _NYSE_TOKENS
except ImportError:
    _NYSE_TOKENS = set()
try:
    from handlers.analysis import _get_market_hours_warning
    _NYSE_CHECK = True
except ImportError:
    _NYSE_CHECK = False

    def _get_market_hours_warning(sym):
        return ""


def _nyse_warning(sym: str) -> str:
    """نص التحذير إن كان السوق مغلقاً، وإلا نص فارغ."""
    try:
        return (_get_market_hours_warning(sym) or "").strip()
    except Exception:
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ثوابت
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRADE_CONFIRM_TIMEOUT = 15 * 60   # 15 دقيقة للماسي
PROPOSAL_TTL          = 20 * 60   # عمر الاقتراح الصالح للتأكيد
MAX_PRICE_DRIFT_PCT   = 1.5       # أقصى انحراف للسعر بين الاقتراح والتنفيذ

# صلاحيات الباقات
TIER_PERMISSIONS = {
    "diamond": {
        "tokenized": True,
        "auto_confirm": True,
        "conflict": True,
        "modify_sl_tp": True,
        "max_trades": None,
    },
    "gold": {
        "tokenized": True,
        "auto_confirm": False,
        "conflict": False,
        "modify_sl_tp": False,
        "max_trades": 5,
    },
    "silver": {
        "tokenized": False,
        "auto_confirm": False,
        "conflict": False,
        "modify_sl_tp": False,
        "max_trades": 3,
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _eng(context):
    return context.bot_data.get("raed_engine")


def _sm():
    from core.state_manager import state_manager
    return state_manager


def _get_tier(engine, user_id: int) -> str:
    try:
        tier = _sm().get_tier(user_id)
        return (tier or "free").lower()
    except Exception:
        return "free"


def _tier_perms(tier: str) -> dict:
    if tier == "admin":
        tier = "diamond"
    return TIER_PERMISSIONS.get(tier, {})


def _is_tokenized(symbol: str) -> bool:
    s = symbol.upper().strip().split("/")[0]
    return s in _NYSE_TOKENS or is_tokenized_x_ticker(s)


def _get_om(engine, user_id: int):
    return engine.get_user_order_manager(user_id)


def _get_exchange(engine, user_id: int):
    ex_data = engine.get_user_exchange(user_id) or {}
    return ex_data.get("exchange")


async def _get_open_trades(engine, user_id: int) -> List:
    om = _get_om(engine, user_id)
    if not om:
        return []
    try:
        return om.get_open_trades(user_id) or []
    except Exception:
        return []


def _fmt_price(p: float) -> str:
    if p >= 10:
        return f"${p:,.2f}"
    elif p >= 1:
        return f"${p:,.4f}"
    return f"${p:.6f}"


SIGNAL_DECISION_TTL = 1800


def _signal_decision(user_id: int, base: str) -> dict:
    """batch58: آخر قرار /signal للمستخدم على هذا الأصل (يحفظه /signal)."""
    try:
        raw = _sm().kv_get(f"sigdec:{int(user_id)}:{str(base).upper()}", "") or ""
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _perp_only_msg_b59(base: str) -> str:
    """batch59 (#691/#692): أصل بلا Spot حقيقي بهذا الرمز (XAG = الفضة كعقد دائم؛ XAG-USDT Spot = سهم مُرمَّز آخر)."""
    try:
        from core.data_layer import _PERP_ONLY_COMMODITY
        if str(base).upper() in _PERP_ONLY_COMMODITY:
            return (f"ℹ️ {str(base).upper()} = الفضة — متاحة على OKX كعقد دائم (Futures) فقط.\n"
                    f"التنفيذ الحقيقي في رائد Spot فقط، فلا تنفيذ. للتحليل: /signal {str(base).upper()}؛ "
                    f"للتدريب: /execute {str(base).upper()} buy 100 ← Futures ← 🎮 افتراضي")
    except Exception:
        pass
    return ""


def _signal_gate_b58(rec: dict, base: str, price: float, now: float = None) -> str:
    """batch58 (#682): يعيد سبب الرفض ('' = مسموح). /trade لا ينفّذ إلا قرار دخول صالح من /signal."""
    now = now or time.time()
    _po = _perp_only_msg_b59(base)
    if _po:
        return _po
    if not rec:
        return (f"ℹ️ /trade ينفّذ قرار /signal نفسه.\n"
                f"أرسل /signal {base} أولاً (Spot)، ثم /trade {base} خلال 30 دقيقة.")
    if now - float(rec.get("ts") or 0) > SIGNAL_DECISION_TTL:
        return f"⌛ آخر قرار /signal لـ {base} أقدم من 30 دقيقة — أعد /signal {base} ثم /trade {base}"
    if str(rec.get("mkt", "spot")) != "spot":
        return f"ℹ️ قرار /signal الأخير لـ {base} كان Futures — /trade حقيقي Spot فقط. أعد /signal {base} واختر Spot"
    # batch60 (#741): سطر واحد بالسبب + عمر القرار + الخطوة التالية
    _age60 = max(0, int((now - float(rec.get("ts") or 0)) / 60))
    from core.virtual_wallet import _count_ar_b60 as _ca60
    _age_txt60 = "أقل من دقيقة" if _age60 < 1 else _ca60(_age60, "دقيقة واحدة", "دقيقتين", "دقائق", "دقيقة")
    _next60 = (f"\n🔁 قرار /signal قبل {_age_txt60} — /trade ينفّذ فقط حين يعطي /signal قرار دخول "
               f"(R/R ≥ 1:1 وحجم > 0%). أعد /signal {base} لاحقاً")
    if str(rec.get("verdict", "")).upper() == "AUTO-FAIL":
        _why60 = str(rec.get('verdict_reason', '')).replace('*', '').replace("❌", "").replace("AUTO-FAIL |", "").strip()
        return f"⛔ *{base}* — لا تنفيذ (AUTO-FAIL): {_why60 or 'تقييم /signal مرفوض'}" + _next60
    if rec.get("wait") or float(rec.get("size_pct") or 0) <= 0:
        return (f"⏳ *{base}* — لا تنفيذ: قرار /signal «انتظار» (الحجم {rec.get('size_rule') or '0%'})" + _next60)
    if str(rec.get("direction", "")).lower() not in ("bullish", "long", "buy"):
        return f"⏳ *{base}* — لا تنفيذ: /signal لا يعطي اتجاه شراء ({rec.get('direction') or 'غير مؤكد'})"
    sl, tp1 = float(rec.get("sl") or 0), float(rec.get("tp1") or 0)
    if sl <= 0 or tp1 <= 0:
        return f"⚠️ *{base}* — قرار /signal بلا وقف/هدف صالح — لا تنفيذ"
    if price <= sl:
        return f"⛔ *{base}* — السعر الحالي {_fmt_price(price)} عند/دون وقف /signal {_fmt_price(sl)} — لا تنفيذ"
    if price >= tp1:
        return f"⛔ *{base}* — السعر الحالي تجاوز الهدف الأول {_fmt_price(tp1)} — لا تنفيذ بهذه البنية"
    return ""


def _age_ar_b60(m) -> str:
    try:
        from core.virtual_wallet import _count_ar_b60 as _ca
        m = int(m or 0)
        return "أقل من دقيقة" if m < 1 else _ca(m, "دقيقة واحدة", "دقيقتين", "دقائق", "دقيقة")
    except Exception:
        return f"{m} دقيقة"


def _qb58(q) -> str:
    """batch58 (#688): كمية بلا صيغة علمية (8.781e-05 → 0.00008781)."""
    try:
        return f"{float(q):.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(q)


# ── أعلام حالة المستخدم (Redis عبر state_manager.kv_*) ──────
def _flag_get(user_id: int, name: str) -> bool:
    try:
        return str(_sm().kv_get(f"trade_bot:{name}:{user_id}", "0")) == "1"
    except Exception:
        return False


def _flag_set(user_id: int, name: str, value: bool) -> None:
    _sm().kv_set(f"trade_bot:{name}:{user_id}", "1" if value else "0")


def _history_get(user_id: int) -> list:
    try:
        return json.loads(_sm().kv_get(f"trade_bot:history:{user_id}", "[]") or "[]")
    except Exception:
        return []


def _history_add(user_id: int, rec: dict) -> None:
    hist = _history_get(user_id)
    hist.insert(0, rec)
    _sm().kv_set(f"trade_bot:history:{user_id}", json.dumps(hist[:100], ensure_ascii=False))


def _pending(context) -> dict:
    return context.user_data.setdefault("trade_pending", {})


def _job_name(user_id: int, symbol: str) -> str:
    return f"trade_auto_{user_id}_{symbol}"


def _cancel_jobs(context, user_id: int, symbol: str = None):
    """يلغي مهام التأكيد التلقائي (لرمز محدد أو لكل رموز المستخدم)."""
    try:
        jq = context.job_queue
        if not jq:
            return
        prefix = f"trade_auto_{user_id}_"
        for j in jq.jobs():
            nm = j.name or ""
            if (symbol and nm == _job_name(user_id, symbol)) or \
               (not symbol and nm.startswith(prefix)):
                j.schedule_removal()
    except Exception as e:
        logger.debug(f"_cancel_jobs: {e}")


async def _build_proposal(engine, user_id: int, base: str):
    """
    يحسب اقتراح الصفقة: السعر، SL/TP، الحجم (Risk Engine + الرصيد).
    يُعيد (proposal | None, رسالة خطأ).
    """
    _po = _perp_only_msg_b59(base)
    if _po:
        return None, _po
    pd = await engine.data_layer.get_price(base, quote="USDT", mkttype="spot")
    price = float((pd or {}).get("price", 0) or 0)
    if price <= 0:
        # batch59 (#691): السبب المرجّح بدل «تعذّر» المبهم
        return None, (f"❌ لا سعر Spot لـ {base} مقابل USDT على OKX الآن\n"
                      f"• قد لا يكون للرمز زوج Spot (بعض الأصول عقود دائمة فقط)، أو تعذّر الجلب مؤقتاً\n"
                      f"• /trade ينفّذ Spot حقيقياً فقط — للتحليل: /signal {base}")

    # batch58 (#682–#684، قرار رحال 2026-11-04): /trade ينفّذ قرار /signal نفسه —
    # لا تنفيذ مع «انتظار» أو AUTO-FAIL، ونفس الوقف/الأهداف/الحجم المعروضة في /signal.
    rec = _signal_decision(user_id, base)
    gate = _signal_gate_b58(rec, base, price)
    if gate:
        return None, gate
    atr_pct = float(rec.get("atr_pct") or 0) or 2.0
    rsi = 50.0
    sl_price = float(rec["sl"])
    sl_pct = (price - sl_price) / price * 100
    _min_sl = 1.5 * atr_pct                      # #681: ≥1.5×ATR بدقة من سعر التنفيذ
    if sl_pct < _min_sl:
        sl_pct = _min_sl
        sl_price = price * (1 - sl_pct / 100)
    tp1_price, tp2_price = float(rec["tp1"]), float(rec.get("tp2") or 0)
    tp1_pct = (tp1_price / price - 1) * 100
    tp2_pct = (tp2_price / price - 1) * 100 if tp2_price > price else tp1_pct
    rr_now = tp1_pct / sl_pct if sl_pct > 0 else 0
    if rr_now < 1.0:
        return None, (f"⛔ *{base}* — لا حافة إحصائية عند السعر الحالي {_fmt_price(price)}\n"
                      f"R/R الآن 1:{rr_now:.2f} (الهدف {_fmt_price(tp1_price)} +{tp1_pct:.1f}% مقابل وقف −{sl_pct:.1f}%)\n"
                      f"لا تنفيذ — راجع /signal {base}")

    # الحجم: Risk Engine على إجمالي محفظة المستخدم الفعلية، ومحدود برصيد USDT المتاح
    ex = _get_exchange(engine, user_id)
    free = total = 0.0
    try:
        bal = await ex.get_balance("USDT")
        free, total = float(bal.free), float(bal.total)
    except Exception as e:
        return None, f"❌ تعذّر قراءة رصيدك من المنصة: {str(e)[:80]}"
    open_map = {}
    for t in await _get_open_trades(engine, user_id):
        open_map[t.symbol] = open_map.get(t.symbol, 0.0) + float(t.size_usd or 0)
    equity = total + sum(open_map.values())
    regime_val = "unknown"
    try:
        btc = await engine.data_layer.get_ohlcv("BTC", "1d", 200)
        if btc and len(btc) >= 30:
            regime_val = engine.regime_detector.detect(btc).regime.value
    except Exception:
        pass
    from core.risk_engine import RiskDecision
    risk = engine.risk_engine.assess(
        symbol=base, direction="long", confidence=float(rec.get("confidence") or 0.55), price=price,
        atr_pct=atr_pct, regime=regime_val,
        portfolio_value=equity if equity > 0 else None,
        user_id=user_id, open_positions=open_map,
        stop_pct=sl_pct / 100, target_pct=tp1_pct / 100)
    if risk.decision == RiskDecision.REJECT:
        return None, ("❌ *مرفوض من Risk Engine*\n\n"
                      + engine.risk_engine.format_assessment_ar(risk, base))
    try:
        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
    except Exception:
        ev_mult, ev_reason = 1.0, ""
    if ev_mult == 0:
        return None, f"⛔ مرفوض — حدث ماكرو حرج\n{ev_reason or ''}"
    size = min(float(risk.approved_size or 0) * float(ev_mult or 1.0), free * 0.95)
    # batch58: لا يتجاوز نسبة الحجم التي عرضها /signal من المحفظة
    _cap_sig = (equity if equity > 0 else total) * float(rec.get("size_pct") or 0) / 100
    if _cap_sig > 0:
        size = min(size, _cap_sig)
    try:
        min_ord = await ex.get_min_order_size(base)
    except Exception:
        min_ord = 1.0
    if size < min_ord:
        return None, (f"❌ الحجم المتاح ${size:,.2f} أقل من الحد الأدنى "
                      f"${min_ord:,.2f} لـ {base} (رصيدك الحر ${free:,.2f})")
    return {
        "symbol": f"{base}/USDT", "base": base, "price": price,
        "sl_pct": sl_pct, "tp1_pct": tp1_pct, "tp2_pct": tp2_pct,
        "sl": price * (1 - sl_pct / 100),
        "tp1": price * (1 + tp1_pct / 100),
        "tp2": price * (1 + tp2_pct / 100),
        "size": round(size, 2), "rsi": rsi, "atr_pct": atr_pct,
        "rr": rr_now, "sig_age_min": max(0, int((time.time() - float(rec.get("ts") or 0)) / 60)),
        "sig_label": str(rec.get("decision_label") or ""), "sig_size": str(rec.get("size_rule") or ""),
        "created": time.time(),
    }, ""


def _proposal_text(p: dict) -> str:
    return (
        f"📊 *صفقة {p['base']} — Spot*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 السعر الحالي: {_fmt_price(p['price'])}\n"
        f"📈 الاتجاه: شراء (Long)\n"
        f"💵 الحجم: ${p['size']:,.2f}\n\n"
        f"🎯 *نقاط الصفقة:*\n"
        f"• الدخول: {_fmt_price(p['price'])}\n"
        f"• وقف الخسارة: {_fmt_price(p['sl'])} (-{p['sl_pct']:.1f}%)\n"
        f"• الهدف: {_fmt_price(p['tp1'])} (+{p['tp1_pct']:.1f}%)\n"
        f"• هدف 2 (مرجعي): {_fmt_price(p['tp2'])} (+{p['tp2_pct']:.1f}%)\n"
        f"• R/R عند السعر الحالي: 1:{p.get('rr', 0):.2f}\n"
        f"• SL/TP يوضعان على المنصة نفسها متى دعمت ذلك\n\n"
        f"📡 المصدر: قرار /signal قبل {_age_ar_b60(p.get('sig_age_min', 0))}"
        + (f" — {p['sig_label']}" if p.get("sig_label") else "") + "\n"
        f"🛑 الوقف ≥ 1.5×ATR من سعر التنفيذ، والحجم ضمن حد /signal ومخاطرة الباقة\n\n"
        f"⚠️ التحليل استرشادي — القرار للمستخدم"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade — فتح صفقة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.effective_message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)
    perms   = _tier_perms(tier)
    reply   = update.effective_message.reply_text

    if tier == "free" or not perms:
        await reply("🔒 /trade متاح للمشترك الفضي وأعلى فقط\nللاشتراك: /upgrade")
        return

    if not engine.user_has_live_trading(user_id):
        await reply("⚠️ لا توجد منصة مربوطة.\nللربط: /live connect [منصة] [key] [secret]")
        return

    if engine.kill_switch.is_active:
        await reply("🔴 التنفيذ متوقف — Kill Switch مفعّل")
        return

    if _flag_get(user_id, "stopped"):
        await reply("⛔ التداول موقوف حالياً\nلإعادة التفعيل: /trade_auto on")
        return

    args = context.args or []
    if not args:
        await reply("⚠️ يرجى تحديد الرمز\nمثال: /trade BTC أو /trade XSPY")
        return

    base = args[0].upper().strip().split("/")[0]
    for _sfx in ("USDT", "BUSD", "USDC"):
        if base.endswith(_sfx) and len(base) > len(_sfx):
            base = base[:-len(_sfx)]
            break
    symbol = f"{base}/USDT"
    skip_nyse = len(args) > 1 and args[1] == "nyse_ok"

    if _is_tokenized(base) and not perms.get("tokenized"):
        await reply(f"🔒 الأصول المُرمَّزة (مثل {base}) للذهبي وأعلى فقط")
        return

    if _is_tokenized(base) and _NYSE_CHECK and not skip_nyse:
        warn = _nyse_warning(base)
        if warn:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ متابعة رغم الإغلاق", callback_data=f"trade_nyse_ok:{base}"),
                InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
            ]])
            await reply(f"{warn}\n\n⚠️ هل تريد المتابعة رغم إغلاق السوق؟", reply_markup=kb)
            return

    open_trades = await _get_open_trades(engine, user_id)
    if not perms.get("conflict"):
        if any(t.symbol.upper() == base for t in open_trades):
            await reply(
                f"⚠️ لديك صفقة مفتوحة على {base}\n"
                f"التعارض غير مسموح لباقتك ({tier})\n"
                f"أغلق الصفقة أولاً: /trade_close {base}")
            return
    mx = perms.get("max_trades")
    if mx is not None and len(open_trades) >= mx:
        await reply(f"⚠️ بلغت الحد الأقصى للصفقات المفتوحة لباقتك ({mx}). أغلق صفقة أولاً: /trade_close")
        return

    msg = await reply(f"🔍 جاري تحليل {symbol}...")
    try:
        proposal, err = await _build_proposal(engine, user_id, base)
    except Exception as e:
        logger.error(f"trade proposal: {e}", exc_info=True)
        await msg.edit_text(f"❌ خطأ في التحليل: {str(e)[:100]}")
        return
    if not proposal:
        await msg.edit_text(err, parse_mode=ParseMode.MARKDOWN)
        return

    _pending(context)[base] = proposal
    summary = proposal_text = _proposal_text(proposal)
    if perms.get("auto_confirm"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد الآن", callback_data=f"trade_confirm:{base}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"trade_cancel:{base}"),
        ]])
        await msg.edit_text(
            summary + "\n\n⏰ سيُنفَّذ تلقائياً بعد 15 دقيقة إن لم تلغِ "
                      "(بعد إعادة التحقق من السعر)",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        _cancel_jobs(context, user_id, base)
        if context.job_queue:
            context.job_queue.run_once(
                _auto_confirm_job, TRADE_CONFIRM_TIMEOUT,
                data={"user_id": user_id, "base": base, "proposal": proposal,
                      "chat_id": update.effective_chat.id},
                name=_job_name(user_id, base))
    else:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data=f"trade_confirm:{base}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"trade_cancel:{base}"),
        ]])
        await msg.edit_text(summary, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# تأكيد تلقائي (Job)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _auto_confirm_job(context: ContextTypes.DEFAULT_TYPE):
    data    = context.job.data
    user_id = data["user_id"]
    engine  = _eng(context)
    if not engine:
        return
    try:
        await _execute_trade(engine, user_id, data["proposal"], context,
                             data["chat_id"], "تأكيد تلقائي (15 دق)")
    except Exception as e:
        logger.error(f"auto_confirm_job: {e}", exc_info=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# تنفيذ الصفقة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _execute_trade(engine, user_id: int, p: dict, context,
                         chat_id: int, source: str = "يدوي"):
    """
    ينفّذ اقتراحاً محفوظاً بعد إعادة التحقق: Kill Switch، إيقاف التداول، عمر الاقتراح،
    انحراف السعر، ثم أمر واحد فقط (بلا إعادة محاولات عمياء).
    """
    async def _say(text, **kw):
        try:
            await context.bot.send_message(chat_id, text, **kw)
        except Exception as e:
            logger.warning(f"trade notify: {e}")

    om = _get_om(engine, user_id)
    if not om:
        await _say("❌ لا توجد منصة مربوطة\nللربط: /live connect [منصة] [key] [secret]")
        return None
    base = p["base"]
    if engine.kill_switch.is_active:
        await _say(f"🔴 أُلغي تنفيذ {base}: Kill Switch مفعّل")
        return None
    if _flag_get(user_id, "stopped"):
        await _say(f"⛔ أُلغي تنفيذ {base}: التداول موقوف (/trade_auto on لإعادته)")
        return None
    if time.time() - p["created"] > PROPOSAL_TTL + TRADE_CONFIRM_TIMEOUT:
        await _say(f"⌛ انتهت صلاحية اقتراح {base} — أعد /trade {base}")
        return None
    # سعر حي من المنصة نفسها للتحقق من الانحراف
    try:
        live = float(await om.exchange.get_price(base) or 0)
    except Exception:
        live = 0.0
    if live <= 0:
        await _say(f"❌ تعذّر التحقق من سعر {base} الحالي — لم يُنفَّذ شيء")
        return None
    drift = abs(live - p["price"]) / p["price"] * 100
    if drift > MAX_PRICE_DRIFT_PCT:
        await _say(
            f"⚠️ لم يُنفَّذ {base}: السعر تحرك {drift:.1f}% منذ الاقتراح "
            f"({_fmt_price(p['price'])} → {_fmt_price(live)}). أعد /trade {base}")
        return None
    # نفس نسب SL/TP المعروضة تُطبَّق على السعر الفعلي لتنفيذ المنصة
    trade = await om.open_trade(
        symbol=base, side="Buy", size_usd=p["size"], entry_price=live,
        stop_loss_pct=p["sl_pct"], take_profit_pct=p["tp1_pct"],
        order_type="MARKET", user_id=user_id,
        auto_protect=_get_tier(engine, user_id) in ("gold", "diamond", "admin"))

    if not trade:
        await _say(f"❌ فشل تنفيذ {p['symbol']}\nالسبب: {(om.last_error or 'رفضت المنصة الأمر')[:150]}")
        return None

    notif = (
        f"✅ *صفقة مُنفَّذة — {base}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 المصدر: {source}\n"
        f"💰 سعر التنفيذ: {_fmt_price(trade.entry_price)}"
        + (" _(تقديري)_" if trade.fill_unconfirmed else "") + "\n"
        f"📦 الكمية: {_qb58(getattr(trade, 'qty_gross', 0) or trade.qty)} (${trade.size_usd:,.2f})"
        + (f" — المتاح بعد رسوم المنصة: {_qb58(trade.qty)}"
           if (getattr(trade, 'qty_gross', 0) or 0) - trade.qty > 1e-12 else "") + "\n"
        f"🛑 وقف الخسارة: {_fmt_price(trade.stop_loss)}\n"
        f"🎯 الهدف: {_fmt_price(trade.take_profit)}\n"
        f"{trade.prot_note}\n"
        f"📋 رقم الأمر: {trade.order_id}\n\n"
        f"⚠️ هذا تنفيذ حقيقي — راقب صفقتك"
    )
    _ok, notif = audit_content(notif, source="trade")
    await _say(notif, parse_mode=ParseMode.MARKDOWN)
    try:
        _history_add(user_id, {
            "symbol": p["symbol"], "price": trade.entry_price, "sl": trade.stop_loss,
            "tp1": trade.take_profit, "tp2": p["tp2"], "source": source,
            "time": datetime.now(timezone.utc).isoformat(),
            "order_id": trade.order_id, "size": trade.size_usd,
        })
    except Exception as e:
        logger.warning(f"trade history save: {e}")
    return trade


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: كل أزرار /trade
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cb_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data or ""
    user_id = query.from_user.id
    engine  = _eng(context)
    if not engine:
        await query.edit_message_text("⚠️ النظام غير متاح")
        return
    if _get_tier(engine, user_id) == "free":
        await query.edit_message_text("🔒 هذه الميزة للمشترك الفضي وأعلى")
        return

    if data == "trade_cancel" or data.startswith("trade_cancel:"):
        base = data.split(":", 1)[1] if ":" in data else None
        _cancel_jobs(context, user_id, base)
        if base:
            _pending(context).pop(base, None)
        await query.edit_message_text("❌ تم إلغاء الصفقة")
        return

    if data.startswith("trade_confirm:"):
        base = data.split(":", 1)[1].split("/")[0].upper()
        p = _pending(context).pop(base, None)
        if not p:
            await query.edit_message_text(f"⌛ لا يوجد اقتراح صالح لـ {base} — أعد /trade {base}")
            return
        if time.time() - p["created"] > PROPOSAL_TTL:
            await query.edit_message_text(f"⌛ انتهت صلاحية الاقتراح — أعد /trade {base}")
            return
        _cancel_jobs(context, user_id, base)
        await query.edit_message_text(f"⏳ جاري تنفيذ صفقة {p['symbol']}...")
        await _execute_trade(engine, user_id, p, context,
                             query.message.chat_id, "تأكيد يدوي")
        return

    if data.startswith("trade_nyse_ok:"):
        base = data.split(":", 1)[1].split("/")[0].upper()
        await query.edit_message_text(f"⏳ جاري التحليل لـ {base} رغم إغلاق السوق...")
        context.args = [base, "nyse_ok"]
        await cmd_trade(update, context)
        return

    if data.startswith("trade_close_confirm:"):
        base = data.split(":", 1)[1].split("/")[0].upper()
        om = _get_om(engine, user_id)
        t = om.find_open(base, user_id) if om else None
        if not t:
            await query.edit_message_text(f"ℹ️ لا توجد صفقة مفتوحة على {base}")
            return
        closed = await om.close_trade(t.trade_id, "manual")
        if closed:
            ic = "📈" if closed.pnl_usd >= 0 else "📉"
            await query.edit_message_text(
                f"✅ *أُغلقت {base}*\n{ic} {closed.pnl_pct:+.2f}% (${closed.pnl_usd:+,.2f})",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(
                f"❌ تعذّر إغلاق {base}: {(om.last_error or 'رفضت المنصة')[:150]}\n"
                f"الصفقة ما زالت مفتوحة.")
        return

    if data == "trade_stop_keep_trail":
        await query.edit_message_text("✅ أُبقي Trailing Stop وSL/TP للصفقات القائمة.")
        return

    if data == "trade_stop_remove_trail":
        om = _get_om(engine, user_id)
        n = 0
        for t in (om.get_open_trades(user_id) if om else []):
            t.trailing_pct = 0.0
            t.trailing_activated = False
            t.auto_protect = False
            om._save_trade_to_redis(t)
            n += 1
        await query.edit_message_text(
            f"✅ أُوقف Trailing Stop لـ {n} صفقة. وقف الخسارة الثابت يبقى فعّالاً.")
        return

    if data == "trade_stop_close_all":
        om = _get_om(engine, user_id)
        ok = fail = 0
        for t in (om.get_open_trades(user_id) if om else []):
            if await om.close_trade(t.trade_id, "manual"):
                ok += 1
            else:
                fail += 1
        await query.edit_message_text(
            f"🔴 أُغلقت {ok} صفقة" + (f" • تعذّر إغلاق {fail} (راجع /trades)" if fail else ""))
        return

    if data.startswith("trade_plan_month:"):
        when = "هذا الشهر" if data.endswith("current") else "الشهر القادم"
        await query.edit_message_text(
            f"🗓️ خطة {when}: أرسل /planmonth لعرض الخطة الشهرية الكاملة بالمستويات.")
        return

    if data.startswith("trade_plan:"):
        when = "هذا الأسبوع" if data.endswith("weekly") else "الأسبوع القادم"
        await query.edit_message_text(
            f"📅 خطة {when}: أرسل /planweek لعرض الخطة الأسبوعية الكاملة بالمستويات.")
        return

    await query.edit_message_text("⚠️ خيار غير معروف")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_close — إغلاق صفقة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    reply   = update.effective_message.reply_text
    if not engine:
        await reply("⚠️ النظام لم يُهيَّأ بعد")
        return
    trades = await _get_open_trades(engine, user_id)
    if not trades:
        await reply("📋 لا توجد صفقات مفتوحة")
        return

    args = context.args or []
    if not args:
        rows = [[InlineKeyboardButton(f"🔴 إغلاق {t.symbol}",
                                      callback_data=f"trade_close_confirm:{t.symbol}")]
                for t in trades[:8]]
        lines = ["📋 *صفقاتك المفتوحة:*\n"] + [
            f"• {t.symbol} — دخول {_fmt_price(t.entry_price)}" for t in trades[:8]]
        await reply("\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(rows))
        return

    base = args[0].upper().strip().split("/")[0]
    if not any(t.symbol.upper() == base for t in trades):
        await reply(f"ℹ️ لا توجد صفقة مفتوحة على {base}")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ إغلاق {base}", callback_data=f"trade_close_confirm:{base}"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await reply(f"⚠️ هل تريد إغلاق صفقة *{base}* بالسعر الحالي؟",
                reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_auto — الوضع التلقائي
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade_auto on  — إعادة تفعيل التداول وتفعيل الوضع التلقائي
    /trade_auto off — إيقاف الوضع التلقائي
    """
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)
    reply   = update.effective_message.reply_text

    if tier not in ("diamond", "gold", "silver", "admin"):
        await reply("🔒 Auto Mode للمشترك الفضي وأعلى")
        return
    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        cur = "✅ مُفعَّل" if _flag_get(user_id, "auto") else "⛔ مُوقَف"
        await reply(f"⚠️ استخدام: /trade_auto on أو /trade_auto off\nالحالة الحالية: {cur}")
        return

    mode = args[0].lower() == "on"
    try:
        _flag_set(user_id, "auto", mode)
        if mode:
            _flag_set(user_id, "stopped", False)
    except Exception as e:
        await reply(f"❌ خطأ: {e}")
        return

    status = "✅ مُفعَّل" if mode else "⛔ مُوقَف"
    await reply(
        f"🤖 *Auto Mode — {status}*\n\n"
        + ("التداول مُفعَّل. الفرص تصلك بزر تأكيد (للماسي: تنفيذ تلقائي بعد 15 دقيقة "
           "بعد إعادة التحقق من السعر)." if mode else
           "لن يُنفَّذ أي تأكيد تلقائي حتى تُعيد التفعيل.")
        + "\n\n⚠️ لا ينفذ خارج ساعات NYSE للأصول المُرمَّزة بدون موافقتك",
        parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_stop — إيقاف التداول
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    reply   = update.effective_message.reply_text
    try:
        _flag_set(user_id, "stopped", True)
        _flag_set(user_id, "auto", False)
    except Exception as e:
        await reply(f"❌ خطأ: {e}")
        return
    _cancel_jobs(context, user_id)          # يلغي أي تأكيد تلقائي معلّق
    trades = await _get_open_trades(engine, user_id) if engine else []
    n_trades = len(trades)

    rows = [[
        InlineKeyboardButton("✅ أبقِ Trailing Stop", callback_data="trade_stop_keep_trail"),
        InlineKeyboardButton("❌ أوقف Trailing Stop", callback_data="trade_stop_remove_trail"),
    ]]
    if n_trades > 0:
        rows.append([InlineKeyboardButton("🔴 أغلق جميع الصفقات",
                                          callback_data="trade_stop_close_all")])
    await reply(
        f"⛔ *تم إيقاف التداول*\n\n"
        f"• Auto Mode: مُوقَف\n"
        f"• التأكيدات التلقائية المعلّقة: أُلغيت\n"
        f"• الصفقات المفتوحة: {n_trades}\n\n"
        f"ماذا تريد مع الصفقات القائمة؟ (لإعادة التداول: /trade_auto on)",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_portfolio — المحفظة الحالية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)
    if tier == "free":
        await update.effective_message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    msg    = await update.effective_message.reply_text("📊 جاري جلب المحفظة...")
    om     = _get_om(engine, user_id)
    trades = await _get_open_trades(engine, user_id)
    try:
        ex = _get_exchange(engine, user_id)
        free = locked = 0.0
        if ex:
            try:
                bal = await ex.get_balance("USDT")
                free, locked = float(bal.free), float(bal.locked)
            except Exception as e:
                logger.debug(f"trade_portfolio balance: {e}")
        lines = [
            "💼 *محفظتك — رائد Spot Bot*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 الرصيد المتاح: ${free:,.2f} USDT",
            f"🔒 المجمَّد: ${locked:,.2f} USDT",
            f"📊 الصفقات المفتوحة: {len(trades)}",
            "",
        ]
        if trades:
            lines.append("*📋 الصفقات:*")
            for t in trades[:10]:
                lines.append(
                    f"• {t.symbol}\n"
                    f"  دخول: {_fmt_price(t.entry_price)} | SL: {_fmt_price(t.stop_loss)} "
                    f"| TP: {_fmt_price(t.take_profit)}")
        else:
            lines.append("_لا توجد صفقات مفتوحة_")
        if om:
            try:
                lines += ["", f"📈 إجمالي PnL المغلق: ${om.total_pnl(user_id):+,.2f}"]
            except Exception:
                pass
        lines += ["", "⚠️ هذه بيانات استرشادية — القرار للمستخدم"]
        text = "\n".join(lines)
        _ok, text = audit_content(text, source="trade")
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_trade_portfolio: {e}")
        await msg.edit_text("❌ خطأ في جلب المحفظة")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_history — السجل
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    if _get_tier(engine, user_id) == "free":
        await update.effective_message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    args   = context.args or []
    period = (args[0].lower() if args else "daily")
    hist   = _history_get(user_id)

    now = datetime.now(timezone.utc)
    if period == "daily":
        cutoff, label = now - timedelta(days=1), "اليوم"
    elif period == "weekly":
        cutoff, label = now - timedelta(weeks=1), "الأسبوع"
    else:
        cutoff, label = datetime(2020, 1, 1, tzinfo=timezone.utc), "الكل"

    filtered = []
    for h in hist:
        try:
            if datetime.fromisoformat(h.get("time", "")) >= cutoff:
                filtered.append(h)
        except Exception:
            filtered.append(h)

    if not filtered:
        await update.effective_message.reply_text(f"📋 لا توجد صفقات في ({label})")
        return

    lines = [f"📋 *سجل الصفقات — {label}* ({len(filtered)} صفقة)", "━━━━━━━━━━━━━━━━━━"]
    for h in filtered[:20]:
        lines.append(f"• {h.get('symbol', '?')} @ {_fmt_price(float(h.get('price', 0) or 0))} "
                     f"| {h.get('source', '')} | {h.get('time', '')[:10]}")
    text = "\n".join(lines)
    _ok, text = audit_content(text, source="trade")
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_plan, /trade_plan_month
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    if _get_tier(engine, user_id) == "free":
        await update.effective_message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 هذا الأسبوع", callback_data="trade_plan:weekly"),
        InlineKeyboardButton("🗓️ الأسبوع القادم", callback_data="trade_plan:next"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await update.effective_message.reply_text(
        "📅 *خطة التداول الأسبوعية*\n\nمتى تريد تطبيق الخطة؟",
        reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def cmd_trade_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id
    if _get_tier(engine, user_id) == "free":
        await update.effective_message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 هذا الشهر", callback_data="trade_plan_month:current"),
        InlineKeyboardButton("🗓️ الشهر القادم", callback_data="trade_plan_month:next"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await update.effective_message.reply_text(
        "🗓️ *خطة التداول الشهرية*\n\nمتى تريد تطبيق الخطة؟",
        reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# تسجيل الأوامر
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def register(app):
    """تسجيل جميع أوامر /trade في التطبيق"""
    app.add_handler(CommandHandler("trade",            cmd_trade))
    app.add_handler(CommandHandler("trade_auto",       cmd_trade_auto))
    app.add_handler(CommandHandler("trade_stop",       cmd_trade_stop))
    app.add_handler(CommandHandler("trade_close",      cmd_trade_close))
    app.add_handler(CommandHandler("trade_plan",       cmd_trade_plan))
    app.add_handler(CommandHandler("trade_plan_month", cmd_trade_plan_month))
    app.add_handler(CommandHandler("trade_portfolio",  cmd_trade_portfolio))
    app.add_handler(CommandHandler("trade_history",    cmd_trade_history))

    app.add_handler(CallbackQueryHandler(cb_trade, pattern=r"^trade_"))

    logger.info("✅ trade handlers مُسجَّلة")

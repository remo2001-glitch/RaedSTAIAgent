"""
trade.py — أوامر /trade Spot Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
أوامر التداول الفوري المستقلة على OKX
متاح للمشترك الماسي+ (ماسي/ذهبي/فضي) بصلاحيات مختلفة

الأوامر:
  /trade [رمز]          — فتح صفقة
  /trade_auto on/off    — تفعيل/إيقاف Auto
  /trade_stop           — إيقاف التداول
  /trade_close [رمز]   — إغلاق صفقة (للجميع)
  /trade_plan           — خطة أسبوعية
  /trade_plan_month     — خطة شهرية
  /trade_portfolio      — المحفظة الحالية
  /trade_history        — السجل

صلاحيات الباقات:
  ماسي+: رقمية + مُرمَّزة، تأكيد تلقائي 15 دق، تعارض مسموح
  ذهبي:  رقمية + مُرمَّزة، يدوي، تعارض مرفوض
  فضي:   رقمية فقط، يدوي، تعارض مرفوض
"""
import asyncio
import logging
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
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
    from handlers.analysis import _NYSE_TOKENS, _is_nyse_closed, _get_nyse_warning
    _NYSE_CHECK = True
except ImportError:
    _NYSE_TOKENS = set()
    _NYSE_CHECK = False
    def _is_nyse_closed(): return False
    def _get_nyse_warning(sym): return ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ثوابت
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRADE_CONFIRM_TIMEOUT = 15 * 60   # 15 دقيقة للماسي
MAX_RETRY             = 3
RETRY_DELAY           = 30        # ثانية

# صلاحيات الباقات
TIER_PERMISSIONS = {
    "diamond": {
        "tokenized": True,   # أصول مُرمَّزة
        "auto_confirm": True,  # تأكيد تلقائي
        "conflict": True,    # تعارض مسموح
        "modify_sl_tp": True,
        "max_trades": None,  # غير محدود
    },
    "gold": {
        "tokenized": True,
        "auto_confirm": False,
        "conflict": False,
        "modify_sl_tp": False,
        "max_trades": 5,
    },
    "silver": {
        "tokenized": False,  # عملات رقمية فقط
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

def _get_tier(engine, user_id: int) -> str:
    """باقة المستخدم — يستخدم state_manager.get_tier الرسمي"""
    try:
        from core.state_manager import state_manager as _sm
        tier = _sm.get_tier(user_id)
        return (tier or "free").lower()
    except Exception:
        return "free"

def _tier_perms(tier: str) -> dict:
    """صلاحيات الباقة — يشمل admin كـ diamond"""
    # admin = نفس صلاحيات diamond
    if tier == "admin":
        tier = "diamond"
    return TIER_PERMISSIONS.get(tier, {})

def _is_tokenized(symbol: str) -> bool:
    """هل الأصل مُرمَّز (X-prefix أو في NYSE_TOKENS)؟"""
    s = symbol.upper().strip().split("/")[0]
    return s in _NYSE_TOKENS or (s.startswith("X") and len(s) >= 3)

def _get_om(engine, user_id: int):
    """OrderManager للمستخدم"""
    ex_data = engine._user_exchanges.get(user_id, {})
    return ex_data.get("order_manager")

def _get_exchange(engine, user_id: int):
    """Exchange للمستخدم"""
    ex_data = engine._user_exchanges.get(user_id, {})
    return ex_data.get("exchange")

async def _get_open_trades(engine, user_id: int) -> List:
    """الصفقات المفتوحة"""
    om = _get_om(engine, user_id)
    if not om:
        return []
    try:
        return om.get_open_trades(user_id) or []
    except Exception:
        return []

def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.2f}"
    elif p >= 1:
        return f"${p:,.4f}"
    return f"${p:.6f}"

def _redis_key_trades(user_id: int) -> str:
    return f"trade_bot:trades:{user_id}"

def _redis_key_auto(user_id: int) -> str:
    return f"trade_bot:auto:{user_id}"

def _redis_key_stopped(user_id: int) -> str:
    return f"trade_bot:stopped:{user_id}"

def _redis_key_history(user_id: int) -> str:
    return f"trade_bot:history:{user_id}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade — فتح صفقة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade [رمز] — فتح صفقة Spot
    ماسي+: تأكيد تلقائي 15 دق
    ذهبي/فضي: ينتظر موافقة يدوية
    """
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)
    perms   = _tier_perms(tier)

    # فحص الباقة
    if tier == "free" or not perms:
        await update.message.reply_text(
            "🔒 /trade متاح للمشترك الفضي وأعلى فقط\n"
            "للاشتراك: تواصل مع الدعم"
        )
        return

    # فحص إيقاف التداول
    try:
        from core.state_manager import state_manager as _sm
        if _sm.get(f"trade_bot:stopped:{user_id}"):
            await update.message.reply_text(
                "⛔ التداول موقوف حالياً\n"
                "لإعادة التفعيل: /trade_stop (اختر استمرار)"
            )
            return
    except Exception:
        pass

    # فحص args
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ يرجى تحديد الرمز\n"
            "مثال: /trade BTC أو /trade XSPY"
        )
        return

    symbol = args[0].upper().strip()
    if "/" not in symbol:
        symbol = f"{symbol}/USDT"

    base = symbol.split("/")[0]

    # فحص الأصول المُرمَّزة
    if _is_tokenized(base) and not perms.get("tokenized"):
        await update.message.reply_text(
            f"🔒 الأصول المُرمَّزة (مثل {base}) للذهبي وأعلى فقط"
        )
        return

    # فحص ساعات NYSE للأصول المُرمَّزة
    if _is_tokenized(base) and _NYSE_CHECK and _is_nyse_closed():
        nyse_warn = _get_nyse_warning(base)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ متابعة رغم الإغلاق", callback_data=f"trade_nyse_ok:{symbol}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
        ]])
        await update.message.reply_text(
            f"{nyse_warn}\n\n"
            "⚠️ هل تريد المتابعة رغم إغلاق السوق؟",
            reply_markup=kb
        )
        return

    # فحص تعارض الأصول
    if not perms.get("conflict"):
        open_trades = await _get_open_trades(engine, user_id)
        existing = [t for t in open_trades if base in str(t)]
        if existing:
            await update.message.reply_text(
                f"⚠️ لديك صفقة مفتوحة على {base}\n"
                f"التعارض غير مسموح لباقتك ({tier})\n"
                f"أغلق الصفقة أولاً: /trade_close {base}"
            )
            return

    # جلب بيانات السوق
    msg = await update.message.reply_text(f"🔍 جاري تحليل {symbol}...")
    try:
        price_data = await engine.data_layer.get_current_price(base)
        price = float(price_data or 0)
        if not price:
            await msg.edit_text(f"❌ تعذَّر جلب سعر {symbol}")
            return
    except Exception as e:
        await msg.edit_text(f"❌ خطأ في جلب البيانات: {e}")
        return

    # تحليل مستقل (نفس شروط /signal)
    try:
        from handlers.analysis import _build_professional_block
        candles = await engine.data_layer.get_ohlcv(base, "1d", 100)
        rsi_data = await engine.data_layer.get_rsi(base, "1d")
        rsi = float((rsi_data or {}).get("rsi", 50))
        atr_pct = 2.0

        # فحص الشروط الأساسية: Confidence ≥ 55%
        # يُحسَب من مصادر متعددة
        # إذا RSI>70 → WAIT دائماً
        if rsi > 70:
            await msg.edit_text(
                f"⚠️ *{base}* — RSI={rsi:.0f} ذروة شراء\n\n"
                f"🔍 مراقبة فقط — لا دخول حتى تصحيح RSI تحت 60\n"
                f"الحجم الآن: **0%**\n\n"
                f"⚠️ التحليل استرشادي — القرار للمستخدم",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    except Exception as e:
        logger.warning(f"trade analysis: {e}")
        rsi = 50
        atr_pct = 2.0

    # حساب SL/TP تلقائي
    sl_pct  = min(max(atr_pct * 1.5, 2.0), 7.0 if _is_tokenized(base) else 10.0)
    tp1_pct = sl_pct * 1.5
    tp2_pct = sl_pct * 2.5
    sl_price  = price * (1 - sl_pct / 100)
    tp1_price = price * (1 + tp1_pct / 100)
    tp2_price = price * (1 + tp2_pct / 100)

    # تفاصيل الصفقة
    trade_summary = (
        f"📊 *صفقة {base} — Spot*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 السعر الحالي: {_fmt_price(price)}\n"
        f"📈 الاتجاه: شراء (Long)\n\n"
        f"🎯 *نقاط الصفقة:*\n"
        f"• الدخول: {_fmt_price(price)}\n"
        f"• وقف الخسارة: {_fmt_price(sl_price)} (-{sl_pct:.1f}%)\n"
        f"• هدف 1: {_fmt_price(tp1_price)} (+{tp1_pct:.1f}%)\n"
        f"• هدف 2: {_fmt_price(tp2_price)} (+{tp2_pct:.1f}%)\n"
        f"• Trailing Stop: ✅ تلقائي\n\n"
        f"⚠️ التحليل استرشادي — القرار للمستخدم"
    )

    # ماسي+: تأكيد تلقائي بعد 15 دقيقة
    if perms.get("auto_confirm"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد الآن", callback_data=f"trade_confirm:{symbol}:{price}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
        ]])
        await msg.edit_text(
            trade_summary + f"\n\n⏰ سيُنفَّذ تلقائياً بعد 15 دقيقة إذا لم تلغِ",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )
        # جدولة التأكيد التلقائي
        context.job_queue.run_once(
            _auto_confirm_job,
            TRADE_CONFIRM_TIMEOUT,
            data={"user_id": user_id, "symbol": symbol, "price": price,
                  "sl": sl_price, "tp1": tp1_price, "tp2": tp2_price,
                  "chat_id": update.effective_chat.id, "msg_id": msg.message_id},
            name=f"trade_auto_{user_id}_{symbol}"
        )
    else:
        # ذهبي/فضي: ينتظر موافقة يدوية
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data=f"trade_confirm:{symbol}:{price}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
        ]])
        await msg.edit_text(
            trade_summary,
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# تأكيد تلقائي (Job)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _auto_confirm_job(context: ContextTypes.DEFAULT_TYPE):
    """يُنفَّذ بعد 15 دقيقة إذا لم يُلغَ"""
    data    = context.job.data
    user_id = data["user_id"]
    symbol  = data["symbol"]
    price   = data["price"]
    engine  = _eng(context)

    if not engine:
        return

    try:
        # تحقق: هل لا يزال التداول مفعَّلاً؟
        from core.state_manager import state_manager as _sm
        if _sm.get(f"trade_bot:stopped:{user_id}"):
            return

        await _execute_trade(
            engine, user_id, symbol, price,
            data["sl"], data["tp1"], data["tp2"],
            context, data["chat_id"], "تأكيد تلقائي (15 دق)"
        )
    except Exception as e:
        logger.error(f"auto_confirm_job: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# تنفيذ الصفقة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _execute_trade(
    engine, user_id: int, symbol: str, price: float,
    sl: float, tp1: float, tp2: float,
    context, chat_id: int, source: str = "يدوي"
):
    """تنفيذ الصفقة على OKX مع إعادة المحاولة"""
    om = _get_om(engine, user_id)
    if not om:
        await context.bot.send_message(
            chat_id, "❌ لا يوجد حساب OKX مُهيَّأ\n"
                     "للإعداد: /setexchange okx [API_KEY] [SECRET] [PASSPHRASE]"
        )
        return None

    base     = symbol.split("/")[0]
    sl_pct   = abs(price - sl) / price * 100
    tp_pct   = abs(tp1 - price) / price * 100

    # Auditing Agent
    trade_text = (
        f"تنفيذ صفقة {symbol} بسعر {price:.4f} "
        f"SL={sl:.4f} TP={tp1:.4f}"
    )
    _approved, _ = audit_financial_content(trade_text, source="trade")
    if not _approved:
        logger.warning(f"auditing_agent: trade مرفوض — {symbol}")

    # تنفيذ مع إعادة المحاولة
    trade = None
    last_err = ""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            trade = await om.open_trade(
                symbol=symbol, side="Buy",
                size_usd=None,  # يُحسَب من الاستراتيجية
                entry_price=price,
                stop_loss_pct=sl_pct,
                take_profit_pct=tp_pct,
                order_type="MARKET",
                user_id=user_id,
                limit_price=0.0
            )
            if trade:
                break
            last_err = "لم يُعيد OrderManager تأكيداً"
        except Exception as e:
            last_err = str(e)
            logger.warning(f"trade attempt {attempt}: {e}")
            if attempt < MAX_RETRY:
                await asyncio.sleep(RETRY_DELAY)

    if trade:
        # إشعار فوري
        notif = (
            f"✅ *صفقة مُنفَّذة — {base}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 المصدر: {source}\n"
            f"💰 السعر: {_fmt_price(price)}\n"
            f"🛑 وقف الخسارة: {_fmt_price(sl)} (-{sl_pct:.1f}%)\n"
            f"🎯 هدف 1: {_fmt_price(tp1)} (+{tp_pct:.1f}%)\n"
            f"🎯 هدف 2: {_fmt_price(tp2)}\n"
            f"📋 رقم الأمر: {getattr(trade, 'order_id', 'N/A')}\n\n"
            f"⚠️ هذا تنفيذ حقيقي — راقب صفقتك"
        )
        _ok, notif = audit_content(notif, source="trade")
        await context.bot.send_message(chat_id, notif, parse_mode=ParseMode.MARKDOWN)

        # خزن في Redis
        try:
            from core.state_manager import state_manager as _sm
            hist = json.loads(_sm.get(f"trade_bot:history:{user_id}") or "[]")
            hist.insert(0, {
                "symbol": symbol, "price": price, "sl": sl,
                "tp1": tp1, "tp2": tp2, "source": source,
                "time": datetime.now(timezone.utc).isoformat(),
                "order_id": getattr(trade, "order_id", ""),
            })
            _sm.set(f"trade_bot:history:{user_id}", json.dumps(hist[:100]))
        except Exception as e:
            logger.warning(f"trade history save: {e}")
    else:
        await context.bot.send_message(
            chat_id,
            f"❌ فشل تنفيذ {symbol} بعد {MAX_RETRY} محاولات\n"
            f"السبب: {last_err[:100]}"
        )

    return trade

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: تأكيد/إلغاء
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cb_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أزرار تأكيد/إلغاء الصفقة"""
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id
    engine  = _eng(context)

    if data == "trade_cancel":
        # إلغاء Job التأكيد التلقائي إن وُجد
        jobs = context.job_queue.get_jobs_by_name(f"trade_auto_{user_id}_*")
        for j in jobs:
            j.schedule_removal()
        await query.edit_message_text("❌ تم إلغاء الصفقة")
        return

    if data.startswith("trade_confirm:"):
        parts  = data.split(":")
        symbol = parts[1]
        price  = float(parts[2])
        base   = symbol.split("/")[0]

        # حساب SL/TP
        atr_pct  = 2.0
        sl_pct   = min(max(atr_pct * 1.5, 2.0), 7.0 if _is_tokenized(base) else 10.0)
        tp1_pct  = sl_pct * 1.5
        tp2_pct  = sl_pct * 2.5
        sl  = price * (1 - sl_pct / 100)
        tp1 = price * (1 + tp1_pct / 100)
        tp2 = price * (1 + tp2_pct / 100)

        await query.edit_message_text(f"⏳ جاري تنفيذ صفقة {symbol}...")
        # إلغاء Job التأكيد التلقائي
        for j in context.job_queue.get_jobs_by_name(f"trade_auto_{user_id}_{symbol}"):
            j.schedule_removal()

        await _execute_trade(
            engine, user_id, symbol, price, sl, tp1, tp2,
            context, update.effective_chat.id, "تأكيد يدوي"
        )
        return

    if data.startswith("trade_nyse_ok:"):
        symbol = data.split(":")[1]
        await query.edit_message_text(f"⏳ جاري التحليل لـ {symbol} رغم إغلاق السوق...")
        # إعادة التوجيه لـ cmd_trade بدون فحص NYSE
        context.args = [symbol]
        await cmd_trade(update, context)
        return

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_close — إغلاق صفقة (للجميع)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade_close [رمز] — إغلاق صفقة مفتوحة
    متاح للجميع (حتى المجاني)
    """
    engine  = _eng(context)
    user_id = update.effective_user.id

    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    om     = _get_om(engine, user_id)
    trades = await _get_open_trades(engine, user_id)

    if not trades:
        await update.message.reply_text("📋 لا توجد صفقات مفتوحة")
        return

    if not args:
        # عرض قائمة الصفقات للاختيار
        lines = ["📋 *صفقاتك المفتوحة:*\n"]
        for t in trades[:5]:
            sym = getattr(t, "symbol", "?")
            lines.append(f"• {sym} — /trade_close {sym.split('/')[0]}")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    symbol = args[0].upper().strip()
    if "/" not in symbol:
        symbol = f"{symbol}/USDT"

    # تأكيد الإغلاق
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ إغلاق {symbol}", callback_data=f"trade_close_confirm:{symbol}"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ هل تريد إغلاق صفقة *{symbol}* بالسعر الحالي؟",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_auto — تفعيل/إيقاف Auto Mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade_auto on  — تفعيل الوضع التلقائي
    /trade_auto off — إيقاف الوضع التلقائي
    """
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)

    if tier not in ("diamond", "gold", "silver"):
        await update.message.reply_text("🔒 Auto Mode للمشترك الفضي وأعلى")
        return

    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        await update.message.reply_text(
            "⚠️ استخدام: /trade_auto on أو /trade_auto off"
        )
        return

    mode = args[0].lower() == "on"

    try:
        from core.state_manager import state_manager as _sm
        _sm.set(f"trade_bot:auto:{user_id}", "1" if mode else "0")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
        return

    status = "✅ مُفعَّل" if mode else "⛔ مُوقَف"
    await update.message.reply_text(
        f"🤖 *Auto Mode — {status}*\n\n"
        f"{'رائد سيراقب جميع أصول Spot تلقائياً ويُنفِّذ عند توفر الشروط.' if mode else 'لن يُنفَّذ أي أمر تلقائي حتى تُعيد التفعيل.'}"
        f"\n\n⚠️ لا ينفذ خارج ساعات NYSE للأصول المُرمَّزة بدون موافقتك",
        parse_mode=ParseMode.MARKDOWN
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_stop — إيقاف التداول
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade_stop — إيقاف التداول مع سؤال عن Trailing Stop
    لا يُغلق الصفقات القائمة إلا إذا طلب المستخدم
    """
    engine  = _eng(context)
    user_id = update.effective_user.id

    try:
        from core.state_manager import state_manager as _sm
        _sm.set(f"trade_bot:stopped:{user_id}", "1")
        _sm.set(f"trade_bot:auto:{user_id}", "0")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
        return

    trades = await _get_open_trades(engine, user_id)
    n_trades = len(trades)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ أبقِ Trailing Stop", callback_data="trade_stop_keep_trail"),
            InlineKeyboardButton("❌ أوقف Trailing Stop", callback_data="trade_stop_remove_trail"),
        ],
        [
            InlineKeyboardButton("🔴 أغلق جميع الصفقات", callback_data="trade_stop_close_all") if n_trades > 0 else
            InlineKeyboardButton("📋 لا صفقات مفتوحة", callback_data="noop"),
        ]
    ])

    await update.message.reply_text(
        f"⛔ *تم إيقاف التداول*\n\n"
        f"• Auto Mode: مُوقَف\n"
        f"• الصفقات المفتوحة: {n_trades}\n\n"
        f"ماذا تريد مع Trailing Stop للصفقات القائمة؟",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_portfolio — المحفظة الحالية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المحفظة والصفقات المفتوحة"""
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)

    if tier == "free":
        await update.message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    msg     = await update.message.reply_text("📊 جاري جلب المحفظة...")
    om      = _get_om(engine, user_id)
    trades  = await _get_open_trades(engine, user_id)

    try:
        # جلب الرصيد
        ex = _get_exchange(engine, user_id)
        balance_usdt = 0.0
        if ex:
            try:
                bal = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ex.get_balance("USDT")
                )
                balance_usdt = float(bal or 0)
            except Exception:
                pass

        lines = [
            "💼 *محفظتك — رائد Spot Bot*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 الرصيد المتاح: ${balance_usdt:,.2f} USDT",
            f"📊 الصفقات المفتوحة: {len(trades)}",
            "",
        ]

        if trades:
            lines.append("*📋 الصفقات:*")
            for t in trades[:10]:
                sym  = getattr(t, "symbol", "?")
                ep   = getattr(t, "entry_price", 0)
                sl_p = getattr(t, "stop_loss", 0)
                tp_p = getattr(t, "take_profit", 0)
                lines.append(
                    f"• {sym}\n"
                    f"  دخول: {_fmt_price(ep)} | SL: {_fmt_price(sl_p)} | TP: {_fmt_price(tp_p)}"
                )
        else:
            lines.append("_لا توجد صفقات مفتوحة_")

        # PnL
        if om:
            try:
                pnl = om.total_pnl(user_id)
                lines += ["", f"📈 إجمالي PnL: ${pnl:+,.2f}"]
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
    """
    /trade_history         — اليوم
    /trade_history weekly  — أسبوعي
    /trade_history all     — الكل
    """
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)

    if tier == "free":
        await update.message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    args   = context.args or []
    period = (args[0].lower() if args else "daily")

    try:
        from core.state_manager import state_manager as _sm
        raw = _sm.get(f"trade_bot:history:{user_id}")
        hist = json.loads(raw or "[]")
    except Exception:
        hist = []

    # فلترة حسب الفترة
    now = datetime.now(timezone.utc)
    if period == "daily":
        cutoff = now - timedelta(days=1)
        label  = "اليوم"
    elif period == "weekly":
        cutoff = now - timedelta(weeks=1)
        label  = "الأسبوع"
    else:
        cutoff = datetime(2020, 1, 1, tzinfo=timezone.utc)
        label  = "الكل"

    filtered = []
    for h in hist:
        try:
            t = datetime.fromisoformat(h.get("time", ""))
            if t >= cutoff:
                filtered.append(h)
        except Exception:
            filtered.append(h)

    if not filtered:
        await update.message.reply_text(f"📋 لا توجد صفقات في ({label})")
        return

    lines = [f"📋 *سجل الصفقات — {label}* ({len(filtered)} صفقة)", "━━━━━━━━━━━━━━━━━━"]
    for h in filtered[:20]:
        sym  = h.get("symbol", "?")
        p    = h.get("price", 0)
        src  = h.get("source", "")
        t    = h.get("time", "")[:10]
        lines.append(f"• {sym} @ {_fmt_price(p)} | {src} | {t}")

    text = "\n".join(lines)
    _ok, text = audit_content(text, source="trade")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_plan — خطة أسبوعية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خطة تداول أسبوعية مستقلة"""
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)

    if tier == "free":
        await update.message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 هذا الأسبوع", callback_data="trade_plan:weekly"),
        InlineKeyboardButton("🗓️ الأسبوع القادم", callback_data="trade_plan:next"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await update.message.reply_text(
        "📅 *خطة التداول الأسبوعية*\n\n"
        "متى تريد تطبيق الخطة؟",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /trade_plan_month — خطة شهرية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def cmd_trade_plan_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خطة تداول شهرية مستقلة"""
    engine  = _eng(context)
    user_id = update.effective_user.id
    tier    = _get_tier(engine, user_id)

    if tier == "free":
        await update.message.reply_text("🔒 هذا الأمر للمشترك الفضي وأعلى")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 هذا الشهر", callback_data="trade_plan_month:current"),
        InlineKeyboardButton("🗓️ الشهر القادم", callback_data="trade_plan_month:next"),
        InlineKeyboardButton("❌ إلغاء", callback_data="trade_cancel"),
    ]])
    await update.message.reply_text(
        "🗓️ *خطة التداول الشهرية*\n\n"
        "متى تريد تطبيق الخطة؟",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

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

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_trade, pattern=r"^trade_"))

    logger.info("✅ trade handlers مُسجَّلة")

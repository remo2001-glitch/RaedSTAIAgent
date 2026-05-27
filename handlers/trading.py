"""
⚡ رائد — handlers/trading.py
أوامر: /autotrade /execute /killswitch /risk /setportfolio /live /trades
نظام هجين:
- المحفظة الافتراضية للجميع (افتراضي)
- التداول الحقيقي اختياري لكل مستخدم عبر /live connect
"""

import logging
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode

from core.kill_switch import KillReason
from core.risk_engine  import RiskDecision

logger = logging.getLogger(__name__)


def _eng(context): return context.bot_data.get("raed_engine")

def _clean(text: str) -> str:
    if not text: return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        parts = line.split("*")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                part = part.replace("_", " ").replace("`", "'")
            result.append(part)
        clean.append("*".join(result))
    return "\n".join(clean)


# ════════════════════════════════════════════════════════════════
# /autotrade
# ════════════════════════════════════════════════════════════════
async def cmd_autotrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args   = context.args or []
    action = (args[0].lower() if args else "").strip()

    if action not in ("on", "off", "تشغيل", "إيقاف"):
        await update.message.reply_text(
            "⚠️ الاستخدام:\n"
            "/autotrade on — تشغيل\n"
            "/autotrade off — إيقاف"); return

    try:
        if engine.kill_switch.is_active:
            await update.message.reply_text(
                f"🔴 لا يمكن التشغيل — Kill Switch مفعّل\n"
                f"لإعادة التشغيل: /killswitch reset"); return

        user_id    = update.effective_user.id
        has_live   = engine.user_has_live_trading(user_id)
        trade_mode = "حقيقي 💰" if has_live else "افتراضي 🎮"

        if action in ("on", "تشغيل"):
            engine.auto_trade_enabled = True
            engine.audit_logger.log_event("autotrade_enabled", {"by": user_id})

            engine_obj = context.bot_data.get("raed_engine")
            scan_txt = ""
            if engine_obj and engine_obj.scheduler:
                scan_txt = engine_obj.scheduler.next_scan_ar()

            await update.message.reply_text(
                f"✅ *التداول التلقائي مُفعَّل*\n"
                f"وضع التداول: {trade_mode}\n\n"
                "رائد يُنفّذ عند:\n"
                "• ثقة ≥ 65٪\n"
                "• موافقة Risk Engine\n"
                "• حدود المخاطر المُعدَّة\n\n"
                "⏰ *جدول المسح*\n"
                "01:00 · 05:00 · 09:00 · 13:00 · 17:00 · 21:00 KSA\n"
                + (f"\n🔜 {scan_txt}" if scan_txt else "") +
                "\n\nللإيقاف: /autotrade off",
                parse_mode=ParseMode.MARKDOWN)
        else:
            engine.auto_trade_enabled = False
            engine.audit_logger.log_event("autotrade_disabled", {"by": user_id})
            await update.message.reply_text(
                "🛑 *التداول التلقائي مُوقَف*\n"
                "رائد في وضع المراقبة فقط.\n"
                "للتشغيل: /autotrade on",
                parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_autotrade: {e}")
        await update.message.reply_text("❌ خطأ في تغيير حالة التداول")


# ════════════════════════════════════════════════════════════════
# /execute — تنفيذ فوري (افتراضي أو حقيقي)
# ════════════════════════════════════════════════════════════════
async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ الاستخدام: /execute [رمز] [buy|sell] [مبلغ اختياري]\n"
            "مثال: /execute BTC buy 500"); return

    symbol    = args[0].upper()
    direction = args[1].lower()
    try:
        size_usd = float(args[2]) if len(args) > 2 else 500.0
    except (ValueError, TypeError):
        size_usd = 500.0

    if direction not in ("buy", "sell", "شراء", "بيع"):
        await update.message.reply_text("⚠️ الاتجاه: buy أو sell"); return

    if engine.kill_switch.is_active:
        await update.message.reply_text(
            f"🔴 التنفيذ متوقف — Kill Switch مفعّل"); return

    user_id  = update.effective_user.id
    has_live = engine.user_has_live_trading(user_id)
    msg = await update.message.reply_text(
        f"🔍 جاري تقييم {symbol} ({direction}) بـ ${size_usd:,.0f}...\n"
        f"الوضع: {'💰 حقيقي' if has_live else '🎮 افتراضي'}")

    try:
        candles  = (await engine.data_layer.get_ohlcv(symbol, "1d", 200)) or []
        price_d  = await engine.data_layer.get_price(symbol)
        fear     = (await engine.data_layer.get_fear_greed()) or {"value": 50}
        onchain  = (await engine.data_layer.get_onchain()) or {}

        if not price_d:
            await msg.edit_text(f"❌ لا يوجد سعر لـ {symbol}"); return

        price    = float(price_d.get("price") or 0)
        fear_val = int(fear.get("value") or 50)

        if price <= 0:
            await msg.edit_text(f"❌ سعر {symbol} غير صالح"); return

        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        if ev_mult == 0:
            await msg.edit_text(
                f"⛔ مرفوض — حدث ماكرو حرج\n{_clean(ev_reason or '')}"); return

        atr_pct = 3.0
        if len(candles) > 14:
            try:
                trs = [max(
                    float(candles[i]["high"]) - float(candles[i]["low"]),
                    abs(float(candles[i]["high"]) - float(candles[i-1]["close"])),
                    abs(float(candles[i]["low"])  - float(candles[i-1]["close"]))
                ) for i in range(1, len(candles))]
                atr = sum(trs[-14:]) / 14
                atr_pct = (atr / price * 100) if price > 0 else 3.0
            except Exception:
                pass

        from core.regime_detector import Regime, RegimeResult
        if len(candles) >= 30:
            regime = engine.regime_detector.detect(candles, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        trade_dir = "long" if direction in ("buy", "شراء") else "short"
        risk = engine.risk_engine.assess(
            symbol=symbol, direction=trade_dir,
            confidence=0.70, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
        )

        if risk.decision == RiskDecision.REJECT:
            await msg.edit_text(
                f"❌ *مرفوض من Risk Engine*\n\n" +
                engine.risk_engine.format_assessment_ar(risk, symbol),
                parse_mode=ParseMode.MARKDOWN); return

        final_size = min(
            float(risk.approved_size or size_usd),
            size_usd * ev_mult
        )
        final_size = max(final_size, 10.0)

        is_buy   = direction in ("buy", "شراء")
        slip     = 0.1
        ep       = price * (1 + slip/100) if is_buy else price * (1 - slip/100)
        sl_price = ep * (1 - float(risk.stop_loss_pct or 5) / 100) if is_buy \
                   else ep * (1 + float(risk.stop_loss_pct or 5) / 100)
        tp_price = ep * (1 + float(risk.take_profit_pct or 10) / 100) if is_buy \
                   else ep * (1 - float(risk.take_profit_pct or 10) / 100)

        # شبه آلي: إذا لديه live trading → زر تأكيد
        if has_live:
            kb = build_confirm_keyboard(symbol, trade_dir, final_size)
            await msg.edit_text(
                f"📋 *تأكيد التنفيذ الحقيقي*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪙 {symbol} | {'🟢 شراء' if is_buy else '🔴 بيع'}\n"
                f"💰 الحجم: ${final_size:,.2f}\n"
                f"📈 السعر: ${ep:,.4f}\n"
                f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}٪)\n"
                f"🎯 هدف الربح: ${tp_price:,.4f} ({risk.take_profit_pct:.1f}٪)\n\n"
                f"⚠️ هذا تنفيذ حقيقي على حسابك في Binance",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb)
            return

        # افتراضي: تنفيذ مباشر على المحفظة الافتراضية
        engine.risk_engine.register_trade(symbol, final_size, trade_dir)
        engine.audit_logger.log_trade(
            symbol=symbol, direction=direction,
            size=final_size, confidence=0.70,
            regime=regime.regime.value, reason="user_manual_virtual")

        lines = [
            "✅ *تم التنفيذ — محفظة افتراضية 🎮*",
            "━━━━━━━━━━━━━━━━━━",
            f"🪙 {symbol} | {'🟢 شراء' if is_buy else '🔴 بيع'}",
            f"💰 الحجم: ${final_size:,.2f}",
            f"📈 سعر الدخول: ${ep:,.4f}",
            f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}٪)",
            f"🎯 هدف الربح: ${tp_price:,.4f} ({risk.take_profit_pct:.1f}٪)",
            f"⏰ أقصى مدة: {risk.max_hold_hours} ساعة",
            "",
            "💡 للتداول الحقيقي: /live connect [binance/bybit] [key] [secret]",
        ]
        await msg.edit_text(_clean("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_execute: {e}")
        await msg.edit_text(f"❌ خطأ في التنفيذ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /live — إدارة التداول الحقيقي
# ════════════════════════════════════════════════════════════════
async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /live — عرض الحالة
    /live connect [binance|bybit] [key] [secret] [testnet?] — ربط
    /live off — فصل
    /live balance — عرض الرصيد
    """
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or []
    action  = args[0].lower() if args else "status"
    user_id = update.effective_user.id
    msg = await update.message.reply_text("🔍 جاري التحقق...")

    try:
        # ── عرض الحالة ──────────────────────────────────────
        if action == "status" or not args:
            has_live = engine.user_has_live_trading(user_id)
            if has_live:
                info    = engine.get_user_exchange(user_id)
                ex_name = info.get("name", "").upper()
                testnet = info.get("testnet", False)
                om      = engine.get_user_order_manager(user_id)
                trades  = om.get_open_trades(user_id) if om else []
                pnl     = om.total_pnl(user_id) if om else 0
                balance = await info["exchange"].get_balance("USDT")
                portfolio_v = engine.get_user_portfolio(user_id)

                lines = [
                    f"🏦 *التداول الحقيقي — {ex_name}*",
                    "━━━━━━━━━━━━━━━━━━",
                    f"{'🔴 Testnet' if testnet else '🟢 Live'} | الاتصال: ✅",
                    "",
                    "💰 *الرصيد*",
                    f"• USDT متاح: ${balance.free:,.2f}",
                    f"• USDT مُجمَّد: ${balance.locked:,.2f}",
                    f"• الإجمالي: ${balance.total:,.2f}",
                    "",
                    f"📊 صفقات مفتوحة: {len(trades)}",
                    f"💹 إجمالي PnL: ${pnl:+,.2f}",
                    f"🎯 حجم المحفظة: ${portfolio_v:,.0f}",
                    "",
                    "للفصل: /live off",
                ]
                if balance.total == 0:
                    lines.insert(8, "⚠️ الرصيد صفر — أضف USDT لحساب Spot")
            else:
                lines = [
                    "🎮 *وضع المحفظة الافتراضية*",
                    "━━━━━━━━━━━━━━━━━━",
                    "جميع التنفيذات على محفظة افتراضية.",
                    "",
                    "💡 *للتداول الحقيقي:*",
                    "`/live connect binance YOUR_KEY YOUR_SECRET`",
                    "أو",
                    "`/live connect bybit YOUR_KEY YOUR_SECRET`",
                    "",
                    "⚠️ ابدأ بـ testnet أولاً:",
                    "`/live connect binance YOUR_KEY YOUR_SECRET testnet`",
                ]
            await msg.edit_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        # ── ربط Exchange ─────────────────────────────────────
        elif action == "connect":
            if len(args) < 4:
                await msg.edit_text(
                    "⚠️ الاستخدام:\n"
                    "/live connect [binance|bybit] [key] [secret]\n"
                    "/live connect [binance|bybit] [key] [secret] testnet\n\n"
                    "مثال:\n"
                    "/live connect binance abc123 xyz789")
                return

            ex_name = args[1].lower()
            api_key = args[2]
            api_sec = args[3]
            testnet = len(args) > 4 and args[4].lower() == "testnet"

            if ex_name not in ("binance", "bybit"):
                await msg.edit_text("⚠️ البورصات المدعومة: binance · bybit")
                return

            await msg.edit_text(
                f"⏳ جاري الاتصال بـ {ex_name.upper()}...\n"
                f"{'🔴 Testnet' if testnet else '🟢 Live'}")

            ok = await engine.connect_user_exchange(
                user_id, ex_name, api_key, api_sec, testnet)

            if ok:
                info    = engine.get_user_exchange(user_id)
                balance = await info["exchange"].get_balance("USDT")
                await msg.edit_text(
                    f"✅ *تم الربط بنجاح — {ex_name.upper()}*\n"
                    f"{'🔴 Testnet' if testnet else '🟢 Live'}\n\n"
                    f"💰 USDT متاح: ${balance.free:,.2f}\n"
                    f"💰 الإجمالي: ${balance.total:,.2f}\n\n"
                    f"الآن /execute سيستخدم التداول الحقيقي\n"
                    f"وستظهر زر ✅ نفّذ للتأكيد قبل كل صفقة",
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await msg.edit_text(
                    f"❌ فشل الاتصال بـ {ex_name.upper()}\n\n"
                    "الأسباب المحتملة:\n"
                    "• API Key خاطئ\n"
                    "• البورصة محجوبة من Railway\n"
                    "• صلاحيات غير كافية\n\n"
                    "تحقق من Railway Logs للتفاصيل")

        # ── فصل Exchange ─────────────────────────────────────
        elif action == "off":
            if engine.user_has_live_trading(user_id):
                engine.disconnect_user_exchange(user_id)
                await msg.edit_text(
                    "✅ تم الفصل — تعمل الآن بالمحفظة الافتراضية 🎮\n"
                    "للإعادة: /live connect [exchange] [key] [secret]")
            else:
                await msg.edit_text("ℹ️ لا يوجد تداول حقيقي مُفعَّل")

        # ── عرض الرصيد ──────────────────────────────────────
        elif action == "balance":
            if not engine.user_has_live_trading(user_id):
                await msg.edit_text(
                    "⚠️ لا يوجد تداول حقيقي مُفعَّل\n"
                    "للربط: /live connect [exchange] [key] [secret]")
                return
            info    = engine.get_user_exchange(user_id)
            balance = await info["exchange"].get_balance("USDT")
            await msg.edit_text(
                f"💰 *الرصيد — {info['name'].upper()}*\n"
                f"• متاح: ${balance.free:,.2f}\n"
                f"• مُجمَّد: ${balance.locked:,.2f}\n"
                f"• الإجمالي: ${balance.total:,.2f}",
                parse_mode=ParseMode.MARKDOWN)

        else:
            await msg.edit_text(
                "⚠️ الخيارات:\n"
                "/live — عرض الحالة\n"
                "/live connect [exchange] [key] [secret]\n"
                "/live balance — عرض الرصيد\n"
                "/live off — فصل التداول الحقيقي")

    except Exception as e:
        logger.error(f"cmd_live: {e}")
        try:
            await msg.edit_text(f"❌ خطأ: {str(e)[:100]}")
        except Exception:
            await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /trades — سجل الصفقات
# ════════════════════════════════════════════════════════════════
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    user_id = update.effective_user.id

    try:
        if not engine.user_has_live_trading(user_id):
            await update.message.reply_text(
                "🎮 أنت في وضع المحفظة الافتراضية\n"
                "للتداول الحقيقي: /live connect [exchange] [key] [secret]")
            return

        om = engine.get_user_order_manager(user_id)
        if not om:
            await update.message.reply_text("⚠️ خطأ في Order Manager"); return

        trades = om.get_all_trades(user_id)[:10]
        if not trades:
            await update.message.reply_text("📋 لا توجد صفقات بعد"); return

        lines = ["📋 *آخر الصفقات الحقيقية*", "━━━━━━━━━━━━━━━━━━", ""]
        for t in trades:
            lines.append(om.format_trade_ar(t))
            lines.append("")

        total = om.total_pnl(user_id)
        lines += ["━━━━━━━━━━━━━━━━━━",
                   f"💹 إجمالي PnL: ${total:+,.2f}"]
        await update.message.reply_text(
            _clean("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_trades: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# Inline Callback — تأكيد التنفيذ الحقيقي
# ════════════════════════════════════════════════════════════════
async def handle_trade_callback(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    engine  = _eng(context)
    if not engine:
        await query.edit_message_text("⚠️ النظام غير متاح"); return

    data    = query.data
    parts   = data.split("_")
    action  = parts[0]
    user_id = query.from_user.id

    if action == "cancel":
        await query.edit_message_text("🚫 تم إلغاء الصفقة"); return

    if action == "confirm" and len(parts) >= 4:
        try:
            symbol    = parts[1]
            direction = parts[2]
            size_usd  = float(parts[3])

            om = engine.get_user_order_manager(user_id)
            if not om:
                await query.edit_message_text(
                    "⚠️ لا يوجد تداول حقيقي\n/live connect للإعداد"); return

            info  = engine.get_user_exchange(user_id)
            price = await info["exchange"].get_price(symbol)
            if price <= 0:
                await query.edit_message_text(
                    f"❌ لا يوجد سعر لـ {symbol}"); return

            side  = "Buy" if direction == "long" else "Sell"
            trade = await om.open_trade(
                symbol=symbol, side=side,
                size_usd=size_usd, entry_price=price,
                stop_loss_pct=5.0, take_profit_pct=10.0,
                order_type="MARKET", user_id=user_id,
            )

            if trade:
                icon = "🟢" if side == "Buy" else "🔴"
                await query.edit_message_text(
                    f"✅ *تم التنفيذ الحقيقي*\n\n"
                    f"{icon} {symbol} {side}\n"
                    f"• الحجم: ${size_usd:,.0f}\n"
                    f"• السعر: ${price:,.4f}\n"
                    f"• وقف الخسارة: ${trade.stop_loss:,.4f}\n"
                    f"• هدف الربح: ${trade.take_profit:,.4f}\n"
                    f"• رقم الأمر: {trade.order_id}\n\n"
                    f"لمتابعة الصفقات: /trades",
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(
                    "❌ فشل التنفيذ\nتحقق من الرصيد وصلاحيات API")
        except Exception as e:
            logger.error(f"handle_trade_callback: {e}")
            await query.edit_message_text(f"❌ خطأ: {str(e)[:100]}")


def build_confirm_keyboard(symbol: str, direction: str,
                             size_usd: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ نفّذ الآن",
            callback_data=f"confirm_{symbol}_{direction}_{size_usd:.0f}"),
        InlineKeyboardButton(
            "🚫 إلغاء",
            callback_data=f"cancel_{symbol}_{direction}_{size_usd:.0f}"),
    ]])


# ════════════════════════════════════════════════════════════════
# /killswitch
# ════════════════════════════════════════════════════════════════
async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args   = context.args or []
    action = args[0].lower() if args else "status"

    try:
        if action == "status" or not args:
            await update.message.reply_text(
                engine.kill_switch.status_ar(), parse_mode=ParseMode.MARKDOWN)
        elif action in ("reset", "إعادة"):
            if not engine.kill_switch.is_active:
                await update.message.reply_text(
                    "✅ Kill Switch غير مفعّل"); return
            engine.kill_switch.reset(reset_by="user")
            engine.audit_logger.log_event("kill_switch_reset", {"by": "user"})
            await update.message.reply_text(
                "✅ *تم إعادة تشغيل النظام*\n"
                "للتداول التلقائي: /autotrade on",
                parse_mode=ParseMode.MARKDOWN)
        elif action in ("trigger", "إيقاف"):
            engine.kill_switch.trigger(KillReason.MANUAL, triggered_by="user")
            engine.auto_trade_enabled = False
            await update.message.reply_text(
                "🔴 *Kill Switch مُفعَّل يدوياً*\n"
                "لإعادة التشغيل: /killswitch reset",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "⚠️ الخيارات:\n"
                "/killswitch status\n"
                "/killswitch trigger\n"
                "/killswitch reset")
    except Exception as e:
        logger.error(f"cmd_killswitch: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════════
# /risk
# ════════════════════════════════════════════════════════════════
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    try:
        pv     = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        report = engine.risk_engine.status_report(pv) or {}
        ev_mult, _ = engine.event_risk.get_exposure_multiplier()
        cfg    = engine.risk_engine.cfg

        lines = [
            "⚖️ *حالة Risk Engine — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 المحفظة: ${report.get('portfolio',0):,.0f}",
            f"📈 القمة:   ${report.get('peak',0):,.0f}",
            f"📉 Drawdown: {report.get('drawdown_pct',0):.1f}٪",
            f"💸 PnL اليوم: ${report.get('today_pnl',0):+,.2f}",
            f"📊 صفقات مفتوحة: {report.get('open_positions',0)}",
            f"🔥 حد الخسارة اليومية: {report.get('daily_loss_used',0):.0f}٪ مُستهلك",
            f"📅 تعرض الأحداث: {ev_mult:.0%}",
            "",
            "⚙️ *الحدود المُعدَّة*",
            f"• خسارة يومية: {float(cfg.get('max_daily_loss',0.05)):.0%}",
            f"• أقصى Drawdown: {float(cfg.get('max_drawdown',0.15)):.0%}",
            f"• أقصى صفقات: {cfg.get('max_open_positions',5)}",
            f"• عتبة الثقة: {float(cfg.get('min_confidence',0.65)):.0%}",
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_risk: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════════
# /setportfolio
# ════════════════════════════════════════════════════════════════
async def cmd_setportfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or []
    user_id = update.effective_user.id

    if not args:
        current = engine.get_user_portfolio(user_id)
        await update.message.reply_text(
            f"💰 *محفظتك الحالية: ${current:,.0f}*\n\n"
            f"لتغييرها: /setportfolio [مبلغ]\n"
            f"مثال: /setportfolio 50000",
            parse_mode=ParseMode.MARKDOWN)
        return

    try:
        amount = float(args[0].replace(",", ""))
        if amount < 100:
            await update.message.reply_text("⚠️ الحد الأدنى $100"); return
        engine.set_user_portfolio(user_id, amount)
        await update.message.reply_text(
            f"✅ *تم ضبط محفظتك: ${amount:,.0f}*\n"
            f"جميع التحليلات ستستخدم هذا المبلغ.",
            parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text(
            "⚠️ أدخل رقماً صحيحاً\nمثال: /setportfolio 50000")
    except Exception as e:
        logger.error(f"cmd_setportfolio: {e}")
        await update.message.reply_text("❌ خطأ في ضبط المحفظة")


# ════════════════════════════════════════════════════════════════
# Register
# ════════════════════════════════════════════════════════════════
def register(app):
    app.add_handler(CommandHandler("autotrade",     cmd_autotrade))
    app.add_handler(CommandHandler("execute",       cmd_execute))
    app.add_handler(CommandHandler("killswitch",    cmd_killswitch))
    app.add_handler(CommandHandler("risk",          cmd_risk))
    app.add_handler(CommandHandler("setportfolio",  cmd_setportfolio))
    app.add_handler(CommandHandler("live",          cmd_live))
    app.add_handler(CommandHandler("trades",        cmd_trades))
    app.add_handler(CallbackQueryHandler(
        handle_trade_callback, pattern=r"^(confirm|cancel)_"))

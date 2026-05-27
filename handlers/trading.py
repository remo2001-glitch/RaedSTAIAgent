"""
⚡ رائد — handlers/trading.py
أوامر: /autotrade /execute /killswitch /risk
جميع النتائج محمية من None — لا TypeError أبداً
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
    if not text:
        return ""
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
# /autotrade on|off
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
                f"{engine.kill_switch.status_ar()}\n\n"
                f"لإعادة التشغيل: /killswitch reset"); return

        if action in ("on", "تشغيل"):
            engine.auto_trade_enabled = True
            engine.audit_logger.log_event("autotrade_enabled", {"by": "user"})
            engine_obj = context.bot_data.get("raed_engine")
            scan_txt = ""
            if engine_obj and engine_obj.scheduler:
                scan_txt = engine_obj.scheduler.next_scan_ar()
            await update.message.reply_text(
                "✅ *التداول التلقائي مُفعَّل*\n\n"
                "رائد يُنفّذ الصفقات تلقائياً عند:\n"
                "• ثقة ≥ 65٪ في مسح كل ٤ ساعات\n"
                "• موافقة Risk Engine\n"
                "• حدود المخاطر المُعدَّة\n\n"
                "⏰ *جدول المسح*\n"
                "01:00 · 05:00 · 09:00 · 13:00 · 17:00 · 21:00 KSA\n"
                + (f"\n🔜 {scan_txt}" if scan_txt else "") +
                "\n\n⚠️ المحفظة الافتراضية — لا تداول حقيقي\n"
                "للإيقاف: /autotrade off",
                parse_mode=ParseMode.MARKDOWN)
        else:
            engine.auto_trade_enabled = False
            engine.audit_logger.log_event("autotrade_disabled", {"by": "user"})
            await update.message.reply_text(
                "🛑 *التداول التلقائي مُوقَف*\n"
                "رائد في وضع المراقبة فقط.\n"
                "للتشغيل: /autotrade on",
                parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_autotrade: {e}")
        await update.message.reply_text("❌ خطأ في تغيير حالة التداول")


# ════════════════════════════════════════════════════════════════
# /execute
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
            f"🔴 التنفيذ متوقف — Kill Switch مفعّل\n"
            f"{engine.kill_switch.status_ar()}"); return

    msg = await update.message.reply_text(
        f"🔍 جاري تقييم {symbol} ({direction}) بـ ${size_usd:,.0f}...")

    try:
        # جمع البيانات مع حماية None
        candles  = (await engine.data_layer.get_ohlcv(symbol, "1d", 200)) or []
        price_d  = await engine.data_layer.get_price(symbol)
        fear     = await engine.data_layer.get_fear_greed() or {"value": 50}
        onchain  = await engine.data_layer.get_onchain()    or {}

        if not price_d or not isinstance(price_d, dict):
            await msg.edit_text(
                f"❌ لا يوجد سعر لـ {symbol}\n"
                f"تأكد من صحة الرمز أو أعد المحاولة بعد لحظة"); return

        price    = float(price_d.get("price") or 0)
        fear_val = int(fear.get("value") or 50)

        if price <= 0:
            await msg.edit_text(f"❌ سعر {symbol} غير صالح"); return

        # Event Risk
        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        if ev_mult == 0:
            await msg.edit_text(
                f"⛔ *التنفيذ مرفوض — حدث ماكرو حرج*\n{_clean(ev_reason)}",
                parse_mode=ParseMode.MARKDOWN); return

        # ATR
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
            except (ValueError, TypeError, ZeroDivisionError):
                atr_pct = 3.0

        # Regime
        from core.regime_detector import Regime, RegimeResult
        if len(candles) >= 30:
            regime = engine.regime_detector.detect(candles, fear_greed=fear_val)
        else:
            regime = RegimeResult(Regime.UNKNOWN, 0.3, "⚪ غير محدد",
                                   ["reduce_size"], {}, "reduce_size")

        # Risk Engine
        trade_dir = "long" if direction in ("buy", "شراء") else "short"
        risk = engine.risk_engine.assess(
            symbol=symbol, direction=trade_dir,
            confidence=0.70, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
        )

        if risk.decision == RiskDecision.REJECT:
            await msg.edit_text(
                f"❌ *تنفيذ مرفوض من Risk Engine*\n\n" +
                engine.risk_engine.format_assessment_ar(risk, symbol),
                parse_mode=ParseMode.MARKDOWN); return

        # Liquidity
        liq = await engine.microstructure.analyze(symbol, size_usd)
        adj_size, liq_reason = engine.microstructure.adjust_size_for_liquidity(
            size_usd * ev_mult, liq) if liq else (size_usd * ev_mult, "")
        final_size = min(float(adj_size or size_usd), float(risk.approved_size or size_usd))
        final_size = max(final_size, 10.0)

        # Human Override
        needs = engine.human_override.needs_approval(
            risk_score=float(risk.risk_score or 0),
            size_usd=final_size,
            confidence=0.70,
            macro_event=(ev_mult < 1.0),
        )
        if needs:
            apid = str(uuid.uuid4())[:8].upper()
            await engine.human_override.request_approval(
                approval_id=apid, reason=needs,
                description=f"{symbol} {direction} ${final_size:,.0f}",
                data={"symbol": symbol, "direction": direction,
                      "size": final_size, "price": price},
            )
            await msg.edit_text(
                f"👤 *طلب موافقة*\n"
                f"السبب: {needs.value}\n"
                f"الصفقة: {symbol} {direction} ${final_size:,.0f}\n"
                f"الرمز: `{apid}`\n\n"
                f"للموافقة: /approve {apid}\n"
                f"للرفض: /reject {apid}",
                parse_mode=ParseMode.MARKDOWN); return

        # تنفيذ على المحفظة الافتراضية
        engine.risk_engine.register_trade(symbol, final_size, trade_dir)
        engine.audit_logger.log_trade(
            symbol=symbol, direction=direction,
            size=final_size, confidence=0.70,
            regime=regime.regime.value, reason="user_manual")

        slip     = float((liq.estimated_slippage_pct if liq else 0) or 0)
        is_buy   = direction in ("buy", "شراء")
        ep       = price * (1 + slip/100) if is_buy else price * (1 - slip/100)
        sl_price = ep * (1 - float(risk.stop_loss_pct   or 5) / 100) if is_buy \
                   else ep * (1 + float(risk.stop_loss_pct   or 5) / 100)
        tp_price = ep * (1 + float(risk.take_profit_pct or 10)/ 100) if is_buy \
                   else ep * (1 - float(risk.take_profit_pct or 10)/ 100)

        lines = [
            "✅ *تم التنفيذ — محفظة افتراضية*",
            "━━━━━━━━━━━━━━━━━━",
            f"🪙 العملة: {symbol}",
            f"📍 الاتجاه: {'🟢 شراء' if is_buy else '🔴 بيع'}",
            f"💰 الحجم: ${final_size:,.2f}",
            f"📈 سعر الدخول: ${ep:,.4f}",
            f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}٪)",
            f"🎯 هدف الربح: ${tp_price:,.4f} ({risk.take_profit_pct:.1f}٪)",
            f"⏰ أقصى مدة: {risk.max_hold_hours} ساعة",
            f"📊 Slippage: {slip:.3f}٪",
        ]
        if liq_reason:
            lines.append(f"⚠️ {_clean(liq_reason)}")
        if risk.warnings:
            lines += ["", "⚠️ *تحذيرات:*"] + [f"• {w}" for w in risk.warnings]
        lines += [
            "",
            "⚠️ محفظة افتراضية — لا تداول حقيقي",
            "لربط محفظة حقيقية أضف Binance API Key",
        ]
        final = _clean("\n".join(lines))
        await msg.edit_text(final, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_execute: {e}")
        await msg.edit_text(f"❌ خطأ في التنفيذ: {str(e)[:100]}")


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
                    "✅ Kill Switch غير مفعّل — النظام يعمل طبيعياً"); return
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
                "جميع التداولات متوقفة.\n"
                "لإعادة التشغيل: /killswitch reset",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "⚠️ الخيارات:\n"
                "/killswitch status\n"
                "/killswitch trigger — إيقاف فوري\n"
                "/killswitch reset — إعادة تشغيل")
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
            f"• أقصى تعرض لعملة: {float(cfg.get('max_single_exposure',0.20)):.0%}",
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_risk: {e}")
        await update.message.reply_text(f"❌ خطأ في جلب حالة المخاطر: {str(e)[:80]}")


async def cmd_setportfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setportfolio [مبلغ] — يضبط حجم المحفظة للمستخدم الحالي
    مثال: /setportfolio 50000
    """
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args = context.args or []
    if not args:
        user_id  = update.effective_user.id
        current  = engine.get_user_portfolio(user_id)
        await update.message.reply_text(
            f"💰 *محفظتك الحالية: ${current:,.0f}*\n\n"
            f"لتغييرها: /setportfolio [مبلغ]\n"
            f"مثال: /setportfolio 50000",
            parse_mode="Markdown"
        )
        return

    try:
        amount  = float(args[0].replace(",", ""))
        user_id = update.effective_user.id
        if amount < 100:
            await update.message.reply_text("⚠️ الحد الأدنى $100")
            return
        engine.set_user_portfolio(user_id, amount)
        await update.message.reply_text(
            f"✅ *تم ضبط محفظتك: ${amount:,.0f}*\n"
            f"جميع التحليلات ستستخدم هذا المبلغ.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("⚠️ أدخل رقماً صحيحاً\nمثال: /setportfolio 50000")
    except Exception as e:
        logger.error(f"cmd_setportfolio: {e}")
        await update.message.reply_text("❌ خطأ في ضبط المحفظة")


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /live — عرض حالة التداول الحقيقي + ربط API Keys
    """
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    # رسالة أولية فورية لتأكيد استلام الأمر
    msg = await update.message.reply_text("🔍 جاري التحقق من حالة التداول...")

    try:
        if getattr(engine, "live_trading", False) and getattr(engine, "exchange", None):
            # جلب الرصيد
            balance = await engine.exchange.get_balance("USDT")
            trades  = engine.order_manager.get_open_trades() if engine.order_manager else []
            total_pnl = engine.order_manager.total_pnl() if engine.order_manager else 0

            balance_note = ""
            if balance.total == 0:
                balance_note = "\n⚠️ الرصيد صفر — أضف USDT لحساب Spot في Bybit"

            lines = [
                f"🏦 *التداول الحقيقي — {engine._exchange_name.upper()}*",
                "━━━━━━━━━━━━━━━━━━",
                f"{'🔴 Testnet' if engine._exchange_test else '🟢 Live'} | الاتصال: ✅",
                "",
                "💰 *الرصيد*",
                f"• USDT متاح: ${balance.free:,.2f}",
                f"• USDT مُجمَّد: ${balance.locked:,.2f}",
                f"• الإجمالي: ${balance.total:,.2f}" + balance_note,
                "",
                f"📊 صفقات مفتوحة: {len(trades)}",
                f"💹 إجمالي PnL: ${total_pnl:+,.2f}",
            ]
            if trades:
                lines += ["", "📋 *الصفقات المفتوحة*"]
                for t in trades[:3]:
                    icon = "🟢" if t.side == "Buy" else "🔴"
                    lines.append(f"• {icon} {t.symbol} ${t.size_usd:,.0f} | دخول ${t.entry_price:,.2f}")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.edit_text(
                "⚠️ *التداول الحقيقي غير مُفعَّل*\n\n"
                "لتفعيله أضف في Railway Variables:\n"
                "EXCHANGE = bybit\n"
                "EXCHANGE API KEY = مفتاحك\n"
                "EXCHANGE API SECRET = سرّك\n"
                "EXCHANGE TESTNET = false\n\n"
                "احصل على API Key من Bybit:\n"
                "الإعدادات → API Management → Create New Key\n"
                "صلاحيات: Read + Trade (Spot فقط)",
                parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_live: {e}")
        try:
            await msg.edit_text(f"❌ خطأ: {str(e)[:100]}")
        except Exception:
            await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trades — عرض الصفقات الحقيقية
    """
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    if not engine.live_trading or not engine.order_manager:
        await update.message.reply_text(
            "⚠️ التداول الحقيقي غير مُفعَّل\nراجع /live للإعداد")
        return

    try:
        user_id = update.effective_user.id
        trades  = engine.order_manager.get_all_trades(user_id)[:10]
        if not trades:
            await update.message.reply_text("📋 لا توجد صفقات بعد"); return

        lines = ["📋 *آخر الصفقات الحقيقية*", "━━━━━━━━━━━━━━━━━━", ""]
        for t in trades:
            lines.append(engine.order_manager.format_trade_ar(t))
            lines.append("")

        total = engine.order_manager.total_pnl(user_id)
        lines += [f"━━━━━━━━━━━━━━━━━━", f"💹 إجمالي PnL: ${total:+,.2f}"]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_trades: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


async def handle_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج Inline Buttons للتأكيد/الرفض الشبه آلي
    """
    query = update.callback_query
    await query.answer()

    engine = _eng(context)
    if not engine:
        await query.edit_message_text("⚠️ النظام غير متاح"); return

    data = query.data  # "confirm_BTC_long_500" | "cancel_BTC_long_500"
    parts = data.split("_")
    action = parts[0]   # confirm | cancel

    if action == "cancel":
        await query.edit_message_text("🚫 تم إلغاء الصفقة")
        return

    if action == "confirm" and len(parts) >= 4:
        try:
            symbol    = parts[1]
            direction = parts[2]   # long | short
            size_usd  = float(parts[3])
            user_id   = query.from_user.id

            if not engine.live_trading or not engine.order_manager:
                await query.edit_message_text(
                    "⚠️ التداول الحقيقي غير مُفعَّل\n"
                    "راجع /live للإعداد")
                return

            # جلب السعر الحالي
            price = await engine.exchange.get_price(symbol)
            if price <= 0:
                await query.edit_message_text(f"❌ لا يوجد سعر لـ {symbol}")
                return

            side = "Buy" if direction == "long" else "Sell"
            trade = await engine.order_manager.open_trade(
                symbol=symbol, side=side,
                size_usd=size_usd,
                entry_price=price,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
                order_type="MARKET",
                user_id=user_id,
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
                    "❌ فشل التنفيذ — تحقق من /live والرصيد")
        except Exception as e:
            logger.error(f"handle_trade_callback: {e}")
            await query.edit_message_text(f"❌ خطأ: {str(e)[:100]}")


def build_confirm_keyboard(symbol: str, direction: str,
                             size_usd: float) -> InlineKeyboardMarkup:
    """يبني لوحة تأكيد/إلغاء للصفقة الشبه آلية."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ نفّذ الآن",
            callback_data=f"confirm_{symbol}_{direction}_{size_usd:.0f}"),
        InlineKeyboardButton(
            "🚫 إلغاء",
            callback_data=f"cancel_{symbol}_{direction}_{size_usd:.0f}"),
    ]])


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

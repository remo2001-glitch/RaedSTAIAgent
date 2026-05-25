"""
⚡ رائد — handlers/trading.py
أوامر: /autotrade /execute /killswitch /risk
جميع النتائج محمية من None — لا TypeError أبداً
"""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from core.kill_switch import KillReason
from core.risk_engine  import RiskDecision

logger = logging.getLogger(__name__)


def _eng(context): return context.bot_data.get("raed_engine")

def _clean(text: str) -> str:
    if not text: return ""
    parts = text.split("*")
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace("_", " ").replace("`", "'")
        result.append(part)
    return "*".join(result)


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
            await update.message.reply_text(
                "✅ *التداول التلقائي مُفعَّل*\n\n"
                "رائد يراقب السوق ويُنفّذ الصفقات تلقائياً بناء على:\n"
                "• عتبة الثقة ≥ 65٪\n"
                "• موافقة Risk Engine\n"
                "• حدود المخاطر المُعدَّة\n"
                "• تقييم السيولة والأحداث\n\n"
                "⚠️ التداول على المحفظة الافتراضية\n"
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
        candles  = await engine.data_layer.get_ohlcv(symbol, "1d", 200) or []
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
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

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


def register(app):
    app.add_handler(CommandHandler("autotrade",  cmd_autotrade))
    app.add_handler(CommandHandler("execute",    cmd_execute))
    app.add_handler(CommandHandler("killswitch", cmd_killswitch))
    app.add_handler(CommandHandler("risk",       cmd_risk))

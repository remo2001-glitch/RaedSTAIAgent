"""
⚡ رائد — handlers/trading.py
أوامر: /autotrade /execute /killswitch /risk /quality
"""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from core.kill_switch import KillReason
from core.risk_engine import RiskDecision

logger = logging.getLogger(__name__)


def _get_engine(context):
    return context.bot_data.get("raed_engine")


# ════════════════════════════════════════════════════════════════
# /autotrade on|off — تشغيل/إيقاف التداول التلقائي
# ════════════════════════════════════════════════════════════════
async def cmd_autotrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    action = args[0].lower() if args else ""

    if action not in ("on", "off", "تشغيل", "إيقاف"):
        await update.message.reply_text(
            "⚠️ الاستخدام:\n"
            "/autotrade on — تشغيل التداول التلقائي\n"
            "/autotrade off — إيقاف التداول التلقائي"
        )
        return

    if engine.kill_switch.is_active:
        await update.message.reply_text(
            "🔴 لا يمكن تشغيل التداول التلقائي — Kill Switch مفعّل\n"
            f"{engine.kill_switch.status_ar()}\n\n"
            "لإعادة التشغيل: /killswitch reset"
        )
        return

    try:
        if action in ("on", "تشغيل"):
            engine.auto_trade_enabled = True
            engine.audit_logger.log_event("autotrade_enabled", {"by": "user"})
            await update.message.reply_text(
                "✅ *التداول التلقائي مُفعَّل*\n\n"
                "رائد سيراقب السوق ويُنفّذ الصفقات تلقائياً بناء على:\n"
                "• عتبة الثقة ≥ ٦٥٪\n"
                "• موافقة Risk Engine\n"
                "• حدود المخاطر المُعدَّة\n"
                "• تقييم السيولة والأحداث\n\n"
                "⚠️ التداول على المحفظة الافتراضية الآن\n"
                "لإيقاف التداول: /autotrade off",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            engine.auto_trade_enabled = False
            engine.audit_logger.log_event("autotrade_disabled", {"by": "user"})
            await update.message.reply_text(
                "🛑 *التداول التلقائي مُوقَف*\n"
                "رائد في وضع المراقبة فقط.\n"
                "للتشغيل: /autotrade on",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"cmd_autotrade error: {e}")
        await update.message.reply_text("❌ خطأ في تغيير حالة التداول التلقائي")


# ════════════════════════════════════════════════════════════════
# /execute — تنفيذ فوري بطلب المستخدم
# ════════════════════════════════════════════════════════════════
async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ الاستخدام: /execute [رمز] [buy|sell] [مبلغ اختياري]\n"
            "مثال: /execute BTC buy 500\n"
            "مثال: /execute ETH sell 1000"
        )
        return

    symbol    = args[0].upper()
    direction = args[1].lower()
    size_usd  = float(args[2]) if len(args) > 2 else 500.0

    if direction not in ("buy", "sell", "شراء", "بيع"):
        await update.message.reply_text(
            "⚠️ الاتجاه يجب أن يكون: buy أو sell")
        return

    if engine.kill_switch.is_active:
        await update.message.reply_text(
            f"🔴 التنفيذ متوقف — Kill Switch مفعّل\n{engine.kill_switch.status_ar()}")
        return

    msg = await update.message.reply_text(
        f"🔍 جاري تقييم صفقة {symbol} ({direction}) بـ ${size_usd:,.0f}...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # جمع البيانات
        candles  = await engine.data_layer.get_ohlcv(symbol, "1d", 200)
        price_d  = await engine.data_layer.get_price(symbol)
        fear     = await engine.data_layer.get_fear_greed()
        onchain  = await engine.data_layer.get_onchain()
        liq      = await engine.microstructure.analyze(symbol, size_usd)

        if not price_d:
            await msg.edit_text(f"❌ لا يوجد سعر لـ {symbol}")
            return

        price = price_d["price"]

        # Regime
        regime = engine.regime_detector.detect(
            candles, fear_greed=fear.get("value", 50)) if len(candles) > 30 else None

        # Event Risk
        ev_mult, ev_reason = engine.event_risk.get_exposure_multiplier()
        if ev_mult == 0:
            await msg.edit_text(
                f"⛔ *التنفيذ مرفوض — حدث ماكرو حرج*\n{ev_reason}",
                parse_mode=ParseMode.MARKDOWN)
            return

        atr_pct = 3.0
        if len(candles) > 14:
            highs  = [c["high"] for c in candles]
            lows   = [c["low"]  for c in candles]
            closes = [c["close"] for c in candles]
            trs    = [max(highs[i]-lows[i],
                          abs(highs[i]-closes[i-1]),
                          abs(lows[i] -closes[i-1]))
                      for i in range(1, len(candles))]
            atr    = sum(trs[-14:]) / 14
            atr_pct = atr / price * 100 if price > 0 else 3.0

        # Risk Engine
        risk = engine.risk_engine.assess(
            symbol=symbol,
            direction="long" if direction in ("buy","شراء") else "short",
            confidence=0.70,
            price=price,
            atr_pct=atr_pct,
            regime=regime.regime.value if regime else "unknown",
        )

        if risk.decision == RiskDecision.REJECT:
            await msg.edit_text(
                f"❌ *تنفيذ مرفوض من Risk Engine*\n\n" +
                engine.risk_engine.format_assessment_ar(risk, symbol),
                parse_mode=ParseMode.MARKDOWN)
            return

        # تعديل الحجم بالسيولة
        adj_size, liq_reason = engine.microstructure.adjust_size_for_liquidity(
            size_usd * ev_mult, liq)

        final_size = min(adj_size, risk.approved_size)

        # Human Override للصفقات الكبيرة
        needs_override = engine.human_override.needs_approval(
            risk_score=risk.risk_score,
            size_usd=final_size,
            confidence=0.70,
            macro_event=(ev_mult < 1.0),
        )

        if needs_override:
            approval_id = str(uuid.uuid4())[:8].upper()
            await engine.human_override.request_approval(
                approval_id=approval_id,
                reason=needs_override,
                description=(f"{symbol} {direction} ${final_size:,.0f} "
                              f"— Risk Score: {risk.risk_score:.0%}"),
                data={"symbol": symbol, "direction": direction,
                      "size": final_size, "price": price},
            )
            await msg.edit_text(
                f"👤 *طلب موافقة للتنفيذ*\n\n"
                f"السبب: {needs_override.value}\n"
                f"الصفقة: {symbol} {direction} ${final_size:,.0f}\n"
                f"رمز الموافقة: `{approval_id}`\n\n"
                f"للموافقة: /approve {approval_id}\n"
                f"للرفض: /reject {approval_id}",
                parse_mode=ParseMode.MARKDOWN)
            return

        # تنفيذ على المحفظة الافتراضية
        engine.risk_engine.register_trade(symbol, final_size,
                                           "long" if direction in ("buy","شراء") else "short")
        engine.audit_logger.log_trade(
            symbol=symbol, direction=direction,
            size=final_size, confidence=0.70,
            regime=regime.regime.value if regime else "unknown",
            reason="user_manual",
        )

        # Slippage تقريبي
        est_slippage = liq.estimated_slippage_pct
        exec_price   = price * (1 + est_slippage/100) if direction in ("buy","شراء") \
                       else price * (1 - est_slippage/100)

        sl_price = exec_price * (1 - risk.stop_loss_pct/100)   if direction in ("buy","شراء") \
                   else exec_price * (1 + risk.stop_loss_pct/100)
        tp_price = exec_price * (1 + risk.take_profit_pct/100) if direction in ("buy","شراء") \
                   else exec_price * (1 - risk.take_profit_pct/100)

        lines = [
            f"✅ *تم التنفيذ على المحفظة الافتراضية*",
            f"━━━━━━━━━━━━━━━━━━",
            f"🪙 العملة:      {symbol}",
            f"📍 الاتجاه:     {'🟢 شراء' if direction in ('buy','شراء') else '🔴 بيع'}",
            f"💰 الحجم:       ${final_size:,.2f}",
            f"📈 سعر الدخول: ${exec_price:,.4f}",
            f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}٪)",
            f"🎯 هدف الربح:  ${tp_price:,.4f} ({risk.take_profit_pct:.1f}٪)",
            f"⏰ أقصى مدة:   {risk.max_hold_hours} ساعة",
            f"📊 Slippage:   {est_slippage:.3f}٪",
        ]

        if liq_reason:
            lines.append(f"⚠️ {liq_reason}")
        if risk.warnings:
            lines += ["", "⚠️ *تحذيرات Risk Engine:*"]
            lines += [f"• {w}" for w in risk.warnings]

        lines += [
            "",
            "⚠️ *تذكير:* هذه محفظة افتراضية — لا تداول حقيقي",
            "لربط محفظة حقيقية يُرجى إضافة Binance API Key",
        ]

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_execute error: {e}")
        await msg.edit_text(f"❌ خطأ في التنفيذ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /killswitch — إدارة Kill Switch
# ════════════════════════════════════════════════════════════════
async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    action = args[0].lower() if args else "status"

    if action == "status" or not args:
        await update.message.reply_text(
            engine.kill_switch.status_ar(),
            parse_mode=ParseMode.MARKDOWN)

    elif action in ("reset", "إعادة"):
        if not engine.kill_switch.is_active:
            await update.message.reply_text("✅ Kill Switch غير مفعّل — النظام يعمل بشكل طبيعي")
            return
        engine.kill_switch.reset(reset_by="user")
        engine.audit_logger.log_event("kill_switch_reset", {"by": "user"})
        await update.message.reply_text(
            "✅ *تم إعادة تشغيل النظام*\n"
            "رائد جاهز للتداول مجدداً.\n"
            "لتفعيل التداول التلقائي: /autotrade on",
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
            "/killswitch status — الحالة\n"
            "/killswitch trigger — إيقاف فوري\n"
            "/killswitch reset — إعادة تشغيل"
        )


# ════════════════════════════════════════════════════════════════
# /risk — حالة Risk Engine
# ════════════════════════════════════════════════════════════════
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _get_engine(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    portfolio_val = engine.risk_engine.cfg.get("portfolio_size", 10_000)
    report        = engine.risk_engine.status_report(portfolio_val)
    ev_mult, _    = engine.event_risk.get_exposure_multiplier()

    lines = [
        "⚖️ *حالة Risk Engine — رائد*",
        "━━━━━━━━━━━━━━━━━━",
        f"💰 المحفظة: ${report['portfolio']:,.0f}",
        f"📈 القمة:   ${report['peak']:,.0f}",
        f"📉 Drawdown: {report['drawdown_pct']:.1f}٪",
        f"💸 PnL اليوم: ${report['today_pnl']:+,.2f}",
        f"📊 صفقات مفتوحة: {report['open_positions']}",
        f"🔥 حد الخسارة اليومية: {report['daily_loss_used']:.0f}٪ مُستهلك",
        f"📅 تعرض الأحداث: {ev_mult:.0%}",
        "",
        "⚙️ *الحدود المُعدَّة*",
        f"• أقصى خسارة/يوم: {engine.risk_engine.cfg['max_daily_loss']:.0%}",
        f"• أقصى Drawdown: {engine.risk_engine.cfg['max_drawdown']:.0%}",
        f"• أقصى صفقات: {engine.risk_engine.cfg['max_open_positions']}",
        f"• عتبة الثقة الدنيا: {engine.risk_engine.cfg['min_confidence']:.0%}",
        f"• أقصى تعرض لعملة: {engine.risk_engine.cfg['max_single_exposure']:.0%}",
    ]
    try:
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_risk error: {e}")
        await update.message.reply_text("❌ خطأ في جلب حالة المخاطر")


# ── تسجيل الـ Handlers ─────────────────────────────────────
def register(app):
    app.add_handler(CommandHandler("autotrade",  cmd_autotrade))
    app.add_handler(CommandHandler("execute",    cmd_execute))
    app.add_handler(CommandHandler("killswitch", cmd_killswitch))
    app.add_handler(CommandHandler("risk",       cmd_risk))

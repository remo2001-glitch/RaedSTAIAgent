"""
⚡ رائد — handlers/trading.py
نظام هجين كامل:
- مجاني: Spot + OKX + 30 عملة
- مدفوع: جميع المنصات + Futures/Margin + 150 عملة
"""

import logging
from core.middleware import require_tier
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode

from core.kill_switch import KillReason
from core.risk_engine  import RiskDecision
from core.exchange     import SUPPORTED_EXCHANGES
from core.state_manager import state_manager as _sm, TIERS, CMD_TIER
from core.middleware    import require_tier

logger = logging.getLogger(__name__)

# منصات محجوبة من Railway بسبب قيود IP
BLOCKED_FROM_RAILWAY = {"binance"}
BLOCKED_MSG = (
    "⚠️ *{ex} محجوب من خادم Railway*\n\n"
    "Binance يحجب طلبات API من عناوين IP معينة.\n\n"
    "البدائل المتاحة:\n"
    "• /live connect okx KEY SECRET PASSPHRASE\n"
    "• /live connect bybit KEY SECRET\n"
    "• /live connect bitget KEY SECRET PASSPHRASE\n\n"
    "أو Testnet: /live connect binance KEY SECRET testnet"
)


def _fmt_price(price: float) -> str:
    """تنسيق السعر حسب حجمه — يعرض الأرقام المهمة دائماً."""
    if price <= 0:
        return "$0"
    elif price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.4f}"
    elif price >= 0.001:
        return f"${price:.6f}"
    elif price >= 0.000001:
        return f"${price:.8f}"
    else:
        return f"${price:.10f}"


def _eng(context):  return context.bot_data.get("raed_engine")
def _um(context):   return _eng(context).user_manager if _eng(context) else None

def _clean(text: str) -> str:
    if not text: return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        parts = line.split("*")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                part = part.replace("_"," ").replace("`","'")
            result.append(part)
        clean.append("*".join(result))
    return "\n".join(clean)


# ════════════════════════════════════════════════════════════════
# /live — إدارة التداول الحقيقي
# ════════════════════════════════════════════════════════════════
async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /live                   — عرض الحالة
    /live connect [ex] [key] [secret] [passphrase?] [testnet?]
    /live off               — فصل
    /live balance           — عرض الرصيد
    /live exchanges         — المنصات المتاحة
    """
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args    = context.args or []
    action  = args[0].lower() if args else "status"
    user_id = update.effective_user.id

    # تعريف um وprofile — مطلوبان في جميع المسارات
    from core.user_manager import user_manager as um
    profile = um.get(user_id)

    msg     = await update.message.reply_text("🔍 جاري التحقق...")

    try:
        # ── عرض المنصات المتاحة ──────────────────────────────
        if action == "exchanges":
            lines = [
                "🏦 *المنصات المتاحة*",
                "━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for ex_id, info in SUPPORTED_EXCHANGES.items():
                can_use = um.can_use_exchange(user_id, ex_id)
                badge   = "✅" if can_use else "🔒 مدفوع"
                futures = "⚡ Futures" if info.get("futures") else "🔵 Spot"
                lines.append(f"{badge} *{info['name']}* — {futures}")
            lines += [
                "",
                f"باقتك: {'💎 مدفوع' if profile.is_premium else '🆓 مجاني'}",
            ]
            if not profile.is_premium:
                lines.append("للترقية: /premium")
            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
            return

        # ── عرض الحالة ──────────────────────────────────────
        if action == "status" or not args:
            has_live = engine.user_has_live_trading(user_id)
            if has_live:
                info    = engine.get_user_exchange(user_id)
                ex_name = info.get("name","").upper()
                testnet = info.get("testnet", False)
                om      = engine.get_user_order_manager(user_id)
                trades  = om.get_open_trades(user_id) if om else []
                pnl     = om.total_pnl(user_id) if om else 0
                balance = await info["exchange"].get_balance("USDT")
                port_v  = engine.get_user_portfolio(user_id)
                futures_ok = um.can_use_futures(user_id)
                margin_ok  = um.can_use_margin(user_id)

                # إذا Spot صفر — نجرب Futures
                futures_balance = None
                if balance.total == 0 and futures_ok:
                    try:
                        futures_balance = await info["exchange"].get_balance(
                            "USDT", account_type="futures")
                    except Exception:
                        pass

                lines = [
                    f"🏦 *التداول الحقيقي — {ex_name}*",
                    "━━━━━━━━━━━━━━━━━━",
                    f"{'🔴 Testnet' if testnet else '🟢 Live'} | اتصال: ✅",
                    f"الباقة: {profile.tier_name}",
                    "",
                    "💰 *رصيد Spot*",
                    f"• USDT متاح:  ${balance.free:,.2f}",
                    f"• USDT مُجمَّد: ${balance.locked:,.2f}",
                    f"• الإجمالي:   ${balance.total:,.2f}",
                ]

                if futures_balance and futures_balance.total > 0:
                    lines += [
                        "",
                        "⚡ *رصيد Futures*",
                        f"• USDT متاح:  ${futures_balance.free:,.2f}",
                        f"• الإجمالي:   ${futures_balance.total:,.2f}",
                    ]

                if balance.total == 0 and (not futures_balance or futures_balance.total == 0):
                    zero_hints = [
                        "",
                        "⚠️ *الرصيد صفر — أسباب محتملة:*",
                        "• API Key لا يملك صلاحية Read",
                        "• Passphrase خاطئ (Bitget/OKX)",
                        "• الرصيد بعملة غير USDT",
                    ]
                    if ex_name == "okx":
                        zero_hints += [
                            "• الرصيد في Funding Account — حوّله لـ Trading",
                            "  (في OKX: Assets → Transfer → Funding → Trading)",
                        ]
                    else:
                        zero_hints.append(
                            "• الرصيد في حساب Futures — فعّل: /live futures on")
                    lines += zero_hints

                lines += [
                    "",
                    f"📊 صفقات مفتوحة: {len(trades)}",
                    f"💹 إجمالي PnL: ${pnl:+,.2f}",
                    f"🎯 حجم المحفظة: ${port_v:,.0f}",
                    "",
                    "⚡ *نوع التداول*",
                    f"• Spot: ✅",
                    f"• Futures: {'✅' if futures_ok else '❌ /live futures on'}",
                    f"• Margin: {'✅' if margin_ok else '❌ /live margin on'}",
                ]
                lines.append("\nللفصل: /live off")
            else:
                # عرض تفاصيل المحفظة الافتراضية
                port_v     = engine.get_user_portfolio(user_id)
                days_left  = um.get_free_autotrade_days(user_id) if not profile.is_premium else 0
                from core.state_manager import state_manager as _sm_lv
                autotrade_on = _sm_lv.is_autotrade_on(user_id)

                lines = [
                    "🎮 *المحفظة الافتراضية — رائد*",
                    "━━━━━━━━━━━━━━━━━━",
                    f"الباقة: {profile.tier_name}",
                    f"حجم المحفظة: ${port_v:,.0f}",
                    f"حد العملات: {profile.coin_limit} عملة",
                    f"🤖 التداول الآلي: {'✅ مُفعَّل — /autotrade off للإيقاف' if autotrade_on else '❌ مُوقَف — /autotrade on للتفعيل'}",
                ]
                if days_left > 0:
                    lines.append(f"⏰ تداول تلقائي مجاني: {days_left} يوم متبقٍ")
                lines += [
                    "",
                    "📊 *أوامر التداول الافتراضي:*",
                    "• /quicksignal — تحليل وإشارة سريعة",
                    "• /autotrade on — تشغيل التداول التلقائي",
                    "• /setportfolio [مبلغ] — ضبط حجم المحفظة",
                    "",
                    "🏦 *للتداول الحقيقي:*",
                    "`/live connect [منصة] [key] [secret]`",
                    "المنصات: okx · bybit · bitget · mexc",
                    "",
                    "لعرض المنصات: /live exchanges",
                ]
            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        # ── ربط Exchange ─────────────────────────────────────
        elif action == "connect":
            if len(args) < 4:
                await msg.edit_text(
                    "⚠️ الاستخدام:\n"
                    "/live connect [منصة] [key] [secret]\n"
                    "/live connect [منصة] [key] [secret] [passphrase]\n"
                    "/live connect [منصة] [key] [secret] [passphrase] testnet\n\n"
                    "المنصات: okx · bybit · bitget · mexc · mexc\n"
                    "لعرض المتاح: /live exchanges")
                return

            ex_name    = args[1].lower().strip()
            api_key    = args[2].strip()
            api_sec    = args[3].strip()
            passphrase = ""
            testnet    = False

            # تحليل بقية المعاملات
            for i in range(4, len(args)):
                val = args[i].strip()
                if val.lower() == "testnet":
                    testnet = True
                else:
                    passphrase = val

            # log للتشخيص (بدون إظهار القيم الحساسة)
            logger.info(
                f"live connect: {ex_name.upper()} | "
                f"key={api_key[:8]}... | "
                f"pass_len={len(passphrase)} | testnet={testnet}"
            )

            # فحص: هل المستخدم مسموح له؟
            # تحذير Binance محجوب من Railway (إلا Testnet)
            if ex_name == "binance" and not testnet:
                await msg.edit_text(
                    BLOCKED_MSG.format(ex="BINANCE"),
                    parse_mode="Markdown")
                return

            if not um.can_use_exchange(user_id, ex_name):
                await msg.edit_text(
                    f"🔒 *{ex_name.upper()} متاح للباقة المدفوعة فقط*\n\n"
                    "المنصات المتاحة حسب باقتك\n\n"
                    "للترقية: /premium",
                    parse_mode="Markdown")
                return

            await msg.edit_text(
                f"⏳ جاري الاتصال بـ {ex_name.upper()}...\n"
                f"{'🔴 Testnet' if testnet else '🟢 Live'}")

            ok = await engine.connect_user_exchange(
                user_id, ex_name, api_key, api_sec, testnet, passphrase)

            if ok:
                info    = engine.get_user_exchange(user_id)
                balance = await info["exchange"].get_balance("USDT")

                # تشخيص الرصيد
                balance_lines = []
                if balance.total > 0:
                    balance_lines = [
                        f"💰 USDT متاح:  ${balance.free:,.2f}",
                        f"💰 USDT مُجمَّد: ${balance.locked:,.2f}",
                        f"💰 الإجمالي:   ${balance.total:,.2f}",
                    ]
                else:
                    balance_lines = [
                        "💰 USDT متاح: $0.00",
                        "💰 الإجمالي: $0.00",
                        "",
                        "⚠️ الرصيد صفر — تحقق من:",
                        "• صلاحية Read في API Key",
                        "• صحة الـ Passphrase",
                        "• الرصيد في Spot وليس Futures",
                        "• /live futures on إذا رصيدك في Futures",
                    ]

                bal_text = "\n".join(balance_lines)
                await msg.edit_text(
                    f"✅ *تم الربط — {ex_name.upper()}*\n"
                    f"{'🔴 Testnet' if testnet else '🟢 Live'}\n\n"
                    f"{bal_text}\n\n"
                    f"📌 التنفيذ على {ex_name.upper()} فقط\n"
                    f"للتنفيذ: /execute [عملة] buy|sell [مبلغ]\n"
                    f"مثال: /execute BTC buy 100",
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await msg.edit_text(
                    f"❌ فشل الاتصال بـ {ex_name.upper()}\n\n"
                    "الأسباب:\n"
                    "• مفتاح API خاطئ\n"
                    "• صلاحيات غير كافية\n"
                    "• المنصة محجوبة من Railway\n\n"
                    "تحقق من Railway Logs")

        # ── تفعيل Futures ────────────────────────────────────
        elif action == "futures":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub in ("on", "تشغيل"):
                if not profile.is_premium:
                    await msg.edit_text(
                        "🔒 Futures متاح للباقة المدفوعة فقط\nللترقية: /premium")
                    return
                um.set_futures(user_id, True)
                await msg.edit_text("✅ Futures مُفعَّل\n⚠️ تداول بالعقود ينطوي على مخاطر عالية")
            elif sub in ("off", "إيقاف"):
                um.set_futures(user_id, False)
                await msg.edit_text("🛑 Futures مُوقَف")
            else:
                status = "✅ مفعّل" if profile.futures_enabled else "❌ موقوف"
                await msg.edit_text(
                    f"⚡ *Futures*: {status}\n\n"
                    f"/live futures on — تفعيل\n"
                    f"/live futures off — إيقاف")

        # ── تفعيل Margin ─────────────────────────────────────
        elif action == "margin":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub in ("on", "تشغيل"):
                if not profile.is_premium:
                    await msg.edit_text(
                        "🔒 Margin متاح للباقة المدفوعة فقط\nللترقية: /premium")
                    return
                um.set_margin(user_id, True)
                await msg.edit_text("✅ Margin مُفعَّل\n⚠️ تداول بالهامش ينطوي على مخاطر عالية")
            elif sub in ("off", "إيقاف"):
                um.set_margin(user_id, False)
                await msg.edit_text("🛑 Margin مُوقَف")
            else:
                status = "✅ مفعّل" if profile.margin_enabled else "❌ موقوف"
                await msg.edit_text(
                    f"📊 *Margin*: {status}\n\n"
                    f"/live margin on — تفعيل\n"
                    f"/live margin off — إيقاف")

        # ── فصل Exchange ─────────────────────────────────────
        elif action == "off":
            if engine.user_has_live_trading(user_id):
                engine.disconnect_user_exchange(user_id)
                await msg.edit_text(
                    "✅ تم الفصل — محفظة افتراضية 🎮\n"
                    "للإعادة: /live connect [exchange] [key] [secret]")
            else:
                await msg.edit_text("ℹ️ لا يوجد تداول حقيقي مُفعَّل")

        # ── عرض الرصيد ──────────────────────────────────────
        elif action == "balance":
            if not engine.user_has_live_trading(user_id):
                await msg.edit_text(
                    "⚠️ لا يوجد تداول حقيقي\n/live connect للربط"); return
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
                "/live — الحالة\n"
                "/live exchanges — المنصات\n"
                "/live connect [ex] [key] [secret]\n"
                "/live balance\n"
                "/live futures on|off\n"
                "/live margin on|off\n"
                "/live off")

    except Exception as e:
        logger.error(f"cmd_live: {e}")
        try:
            await msg.edit_text(f"❌ خطأ: {str(e)[:100]}")
        except Exception:
            await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /execute — تنفيذ فوري
# ════════════════════════════════════════════════════════════════
@require_tier("execute")
async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ الاستخدام: /execute [رمز] [buy|sell] [مبلغ]\n"
            "مثال: /execute BTC buy 500"); return

    symbol    = args[0].upper()
    direction = args[1].lower()
    try:
        size_usd = float(args[2]) if len(args) > 2 else 500.0
    except (ValueError, TypeError):
        size_usd = 500.0

    if direction not in ("buy","sell","شراء","بيع"):
        await update.message.reply_text("⚠️ الاتجاه: buy أو sell"); return

    if engine.kill_switch.is_active:
        await update.message.reply_text("🔴 التنفيذ متوقف — Kill Switch مفعّل"); return

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
                    float(candles[i].get("high",price)) - float(candles[i].get("low",price)),
                    abs(float(candles[i].get("high",price)) - float(candles[i-1].get("close",price))),
                    abs(float(candles[i].get("low",price))  - float(candles[i-1].get("close",price)))
                ) for i in range(1, min(len(candles), 20))]
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

        trade_dir = "long" if direction in ("buy","شراء") else "short"
        risk = engine.risk_engine.assess(
            symbol=symbol, direction=trade_dir,
            confidence=0.70, price=price,
            atr_pct=atr_pct, regime=regime.regime.value)

        if risk.decision == RiskDecision.REJECT:
            await msg.edit_text(
                f"❌ *مرفوض من Risk Engine*\n\n" +
                engine.risk_engine.format_assessment_ar(risk, symbol),
                parse_mode=ParseMode.MARKDOWN); return

        final_size = min(float(risk.approved_size or size_usd),
                          size_usd * ev_mult)
        final_size = max(final_size, 10.0)
        is_buy     = direction in ("buy","شراء")
        slip       = 0.1
        ep         = price * (1+slip/100) if is_buy else price * (1-slip/100)
        sl_price   = ep * (1-float(risk.stop_loss_pct or 5)/100) if is_buy \
                     else ep * (1+float(risk.stop_loss_pct or 5)/100)
        tp_price   = ep * (1+float(risk.take_profit_pct or 10)/100) if is_buy \
                     else ep * (1-float(risk.take_profit_pct or 10)/100)

        # اختيار أفضل منصة إذا live
        best_ex = None
        if has_live:
            best_ex = await engine.find_best_exchange(user_id, symbol)

        if best_ex:
            # شبه آلي — زر تأكيد
            kb = build_confirm_keyboard(symbol, trade_dir, final_size,
                                         best_ex.get("name",""))
            await msg.edit_text(
                f"📋 *تأكيد التنفيذ الحقيقي*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪙 {symbol} | {'🟢 شراء' if is_buy else '🔴 بيع'}\n"
                f"💰 الحجم: ${final_size:,.2f}\n"
                f"📈 السعر: ${ep:,.4f}\n"
                f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}٪)\n"
                f"🎯 هدف الربح: ${tp_price:,.4f} ({risk.take_profit_pct:.1f}٪)\n"
                f"🏦 المنصة: {best_ex.get('name','').upper()} "
                f"(حجم ${best_ex.get('volume_24h',0)/1e6:.0f}M)\n\n"
                f"⚠️ هذا تنفيذ حقيقي على حسابك",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb)
            return

        # افتراضي
        engine.risk_engine.register_trade(symbol, final_size, trade_dir)
        engine.audit_logger.log_trade(
            symbol=symbol, direction=direction, size=final_size,
            confidence=0.70, regime=regime.regime.value,
            reason="user_manual_virtual")

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
            "💡 للتداول الحقيقي: /live connect [منصة] [key] [secret]",
        ]
        await msg.edit_text(_clean("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_execute: {e}")
        await msg.edit_text(f"❌ خطأ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# /trades
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
                "/live connect للتداول الحقيقي"); return

        om     = engine.get_user_order_manager(user_id)
        if not om:
            await update.message.reply_text("⚠️ خطأ في Order Manager"); return

        trades = om.get_all_trades(user_id)[:10]
        if not trades:
            await update.message.reply_text("📋 لا توجد صفقات بعد"); return

        lines = ["📋 *آخر الصفقات الحقيقية*","━━━━━━━━━━━━━━━━━━",""]
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
# /premium — إدارة الباقات (للمدير فقط)
# ════════════════════════════════════════════════════════════════
async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    user_id = update.effective_user.id
    args    = context.args or []

    # عرض الحالة الشخصية (بدون args)
    if not args:
        await update.message.reply_text(
            _sm.format_profile_ar(user_id),
            parse_mode=ParseMode.MARKDOWN)
        return

    # ══ حماية صارمة: أوامر الإدارة للمدير فقط ══
    user_tier = _sm.get_tier(user_id)
    if user_tier != "admin":
        logger.warning(f"⚠️ محاولة وصول غير مصرح: user {user_id} → /premium {args}")
        await update.message.reply_text(
            "⚠️ هذا الأمر للمدير فقط")
        return

    sub = args[0].lower()
    try:
        if sub == "add" and len(args) >= 2:
            target_id = int(args[1])
            tier      = args[2].lower() if len(args) > 2 and args[2].lower() in TIERS else "silver"
            ok = _sm.set_tier(target_id, tier, by=str(user_id),
                               requester_id=user_id)
            if ok:
                tier_name = TIERS[tier]["name"]
                await update.message.reply_text(
                    f"✅ تم منح *{tier_name}* للمستخدم `{target_id}`",
                    parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ فشل — تحقق من الصلاحيات")

        elif sub == "remove" and len(args) >= 2:
            target_id = int(args[1])
            _sm.set_tier(target_id, "free", by=str(user_id),
                          requester_id=user_id)
            await update.message.reply_text(
                f"✅ تم إلغاء الباقة للمستخدم `{target_id}`",
                parse_mode="Markdown")

        elif sub == "list":
            premium_users = _sm.list_premium_users()
            if not premium_users:
                await update.message.reply_text("لا يوجد مستخدمون مدفوعون")
                return
            lines = [f"💎 *المستخدمون المدفوعون ({len(premium_users)})*",""]
            for u in premium_users:
                lines.append(f"• {u.user_id} {u.notes or ''}")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        elif sub == "info" and len(args) >= 2:
            target_id = int(args[1])
            await update.message.reply_text(
                _sm.format_profile_ar(target_id),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "⚠️ الخيارات (للمدير):\n"
                "/premium add [user_id] [ملاحظات]\n"
                "/premium remove [user_id]\n"
                "/premium list\n"
                "/premium info [user_id]")
    except ValueError:
        await update.message.reply_text("⚠️ أدخل user_id صحيحاً")
    except Exception as e:
        logger.error(f"cmd_premium: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")


# ════════════════════════════════════════════════════════════════
# Inline Callback
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

            # اختيار أفضل منصة
            best_ex = await engine.find_best_exchange(user_id, symbol)
            if not best_ex:
                await query.edit_message_text(
                    "⚠️ لا توجد منصة مرتبطة\n/live connect للإعداد"); return

            om    = best_ex.get("order_manager")
            if not om:
                await query.edit_message_text("⚠️ خطأ في Order Manager"); return

            price = await best_ex["exchange"].get_price(symbol)
            if price <= 0:
                await query.edit_message_text(f"❌ لا يوجد سعر لـ {symbol}"); return

            side  = "Buy" if direction == "long" else "Sell"
            trade = await om.open_trade(
                symbol=symbol, side=side, size_usd=size_usd,
                entry_price=price, stop_loss_pct=5.0, take_profit_pct=10.0,
                order_type="MARKET", user_id=user_id)

            if trade:
                icon = "🟢" if side == "Buy" else "🔴"
                await query.edit_message_text(
                    f"✅ *تم التنفيذ الحقيقي*\n\n"
                    f"{icon} {symbol} {side} على {best_ex.get('name','').upper()}\n"
                    f"• الحجم: ${size_usd:,.0f}\n"
                    f"• السعر: ${price:,.4f}\n"
                    f"• وقف الخسارة: ${trade.stop_loss:,.4f}\n"
                    f"• هدف الربح: ${trade.take_profit:,.4f}\n"
                    f"• رقم الأمر: {trade.order_id}\n\n"
                    f"/trades لمتابعة الصفقات",
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(
                    "❌ فشل التنفيذ\nتحقق من الرصيد وصلاحيات API")
        except Exception as e:
            logger.error(f"handle_trade_callback: {e}")
            await query.edit_message_text(f"❌ خطأ: {str(e)[:100]}")


def build_confirm_keyboard(symbol: str, direction: str,
                             size_usd: float,
                             exchange_name: str = "") -> InlineKeyboardMarkup:
    ex_label = f" ({exchange_name.upper()})" if exchange_name else ""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ نفّذ الآن{ex_label}",
            callback_data=f"confirm_{symbol}_{direction}_{size_usd:.0f}"),
        InlineKeyboardButton(
            "🚫 إلغاء",
            callback_data=f"cancel_{symbol}_{direction}_{size_usd:.0f}"),
    ]])


# ════════════════════════════════════════════════════════════════
# /autotrade, /killswitch, /risk, /setportfolio
# ════════════════════════════════════════════════════════════════
@require_tier("autotrade")
async def cmd_autotrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    from core.user_manager import user_manager as um
    args    = context.args or []
    action  = args[0].lower() if args else "status"
    user_id = update.effective_user.id

    try:
        if engine.kill_switch.is_active:
            await update.message.reply_text(
                "🔴 Kill Switch مفعّل — /killswitch reset للتشغيل"); return

        ex_info   = engine.get_user_exchange(user_id)
        mode      = f"💰 حقيقي ({ex_info['name'].upper()})" if ex_info else "🎮 افتراضي"
        tier_name = um.get(user_id).tier_name
        is_on     = _sm.is_autotrade_on(user_id)

        if action in ("on", "تشغيل"):
            _sm.set_autotrade_on(user_id, True)
            await update.message.reply_text(
                "✅ *التداول الآلي مُفعَّل*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"الوضع: {mode}\n"
                f"الباقة: {tier_name}\n\n"
                "⏰ *جدول المسح*\n"
                "01:00 · 05:00 · 09:00 · 13:00 · 17:00 · 21:00 KSA\n\n"
                "للإيقاف الفوري: /autotrade off",
                parse_mode=ParseMode.MARKDOWN)

        elif action in ("off", "إيقاف"):
            _sm.set_autotrade_on(user_id, False)
            await update.message.reply_text(
                "⏹️ *التداول الآلي مُوقَف*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "لن تُنفَّذ صفقات آلية حتى إعادة التفعيل.\n\n"
                "للتفعيل مجدداً: /autotrade on",
                parse_mode=ParseMode.MARKDOWN)

        else:
            # عرض الحالة الحالية
            schedule_text = (
                "⏰ *جدول المسح*\n"
                "01:00 · 05:00 · 09:00 · 13:00 · 17:00 · 21:00 KSA\n\n"
            ) if is_on else ""
            await update.message.reply_text(
                "🤖 *التداول الآلي — رائد*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"الحالة: {'✅ مُفعَّل' if is_on else '❌ مُوقَف'}\n"
                f"الوضع: {mode}\n"
                f"الباقة: {tier_name}\n\n"
                f"{schedule_text}"
                f"{'للإيقاف: /autotrade off' if is_on else 'للتفعيل: /autotrade on'}",
                parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_autotrade: {e}")
        await update.message.reply_text("❌ خطأ في التداول الآلي")


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
        elif action in ("reset","إعادة"):
            if not engine.kill_switch.is_active:
                await update.message.reply_text("✅ Kill Switch غير مفعّل"); return
            engine.kill_switch.reset(reset_by="user")
            await update.message.reply_text(
                "✅ *تم إعادة تشغيل النظام*",
                parse_mode=ParseMode.MARKDOWN)
        elif action in ("trigger","إيقاف"):
            engine.kill_switch.trigger(KillReason.MANUAL, triggered_by="user")
            engine.auto_trade_enabled = False
            await update.message.reply_text(
                "🔴 *Kill Switch مُفعَّل*\n/killswitch reset للإعادة",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "⚠️ /killswitch status|trigger|reset")
    except Exception as e:
        logger.error(f"cmd_killswitch: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:80]}")


@require_tier("risk")
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    try:
        pv     = float(engine.risk_engine.cfg.get("portfolio_size") or 10000)
        report = engine.risk_engine.status_report(pv) or {}
        ev_mult, _ = engine.event_risk.get_exposure_multiplier()
        cfg    = engine.risk_engine.cfg
        lines  = [
            "⚖️ *حالة Risk Engine*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 المحفظة: ${report.get('portfolio',0):,.0f}",
            f"📉 Drawdown: {report.get('drawdown_pct',0):.1f}٪",
            f"💸 PnL اليوم: ${report.get('today_pnl',0):+,.2f}",
            f"📊 صفقات مفتوحة: {report.get('open_positions',0)}",
            f"📅 تعرض الأحداث: {ev_mult:.0%}",
            "",
            f"• عتبة الثقة: {float(cfg.get('min_confidence',0.65)):.0%}",
            f"• أقصى Drawdown: {float(cfg.get('max_drawdown',0.15)):.0%}",
            f"• خسارة يومية: {float(cfg.get('max_daily_loss',0.05)):.0%}",
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_risk: {e}")
        await update.message.reply_text(f"❌ خطأ: {str(e)[:80]}")


async def cmd_setportfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return
    args    = context.args or []
    user_id = update.effective_user.id
    if not args:
        current = engine.get_user_portfolio(user_id)
        await update.message.reply_text(
            f"💰 *محفظتك: ${current:,.0f}*\n/setportfolio [مبلغ]",
            parse_mode=ParseMode.MARKDOWN); return
    try:
        amount = float(args[0].replace(",",""))
        if amount < 100:
            await update.message.reply_text("⚠️ الحد الأدنى $100"); return
        engine.set_user_portfolio(user_id, amount)
        await update.message.reply_text(
            f"✅ *تم ضبط المحفظة: ${amount:,.0f}*",
            parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("⚠️ مثال: /setportfolio 50000")
    except Exception as e:
        logger.error(f"cmd_setportfolio: {e}")
        await update.message.reply_text("❌ خطأ")


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
    app.add_handler(CommandHandler("premium",       cmd_premium))
    app.add_handler(CallbackQueryHandler(
        handle_trade_callback, pattern=r"^(confirm|cancel)_"))

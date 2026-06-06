"""
⚡ رائد — handlers/trading.py
نظام هجين كامل:
- مجاني: Spot + OKX + 30 عملة
- مدفوع: جميع المنصات + Futures/Margin + 150 عملة
"""

import asyncio
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

    # المجاني: لا ربط تداول حقيقي
    if action in ("connect", "on", "margin", "futures"):
        if _sm.get_tier(update.effective_user.id) == "free":
            await update.message.reply_text(
                "🔒 *ربط منصة التداول الحقيقي غير متاح في الباقة المجانية*\n\n"
                "⬆️ للترقية: /upgrade",
                parse_mode="Markdown"); return
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
                if trades:
                    lines += ["", "📋 *صفقاتك المفتوحة:*"]
                    for _t in trades[:3]:
                        lines.append(f"  • {_t.symbol}: هدف ${_t.take_profit:,.4f} | وقف ${_t.stop_loss:,.4f}")
                if balance.total > 0 and not trades:
                    lines += ["", "💡 معظم رأس المال متاح — جرب /signal لفرص جديدة"]
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

async def callback_execmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """M#112: معالجة اختيار المستخدم real/virtual في execute."""
    query   = update.callback_query
    await query.answer()
    data    = query.data  # execmode_real_BTC_buy_100.00_0.0000
    parts   = data.split("_")
    if len(parts) < 5: return
    mode     = parts[1]        # real | virtual
    symbol   = parts[2].upper()
    direction= parts[3]
    size_usd = float(parts[4])
    lp       = float(parts[5]) if len(parts) > 5 else 0.0

    # إعادة توجيه الأمر مع الوضع المحدد
    context.args = [symbol, direction, str(size_usd), mode]
    if lp > 0:
        context.args += ["limit", str(lp)]
    await query.edit_message_text(
        f"{'💰 تنفيذ حقيقي' if mode=='real' else '🎮 محفظة افتراضية'} — جاري التقييم...")
    update.message = query.message
    await cmd_execute(update, context)

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

    # M#90: قراءة Limit Price إذا حُدِّدت
    limit_price = 0.0
    if len(args) >= 5 and args[3].lower() == "limit":
        try:
            limit_price = float(args[4])
        except (ValueError, TypeError):
            await update.message.reply_text(
                "⚠️ سعر Limit غير صحيح\n"
                "مثال: `/execute FET buy 5 limit 0.259`",
                parse_mode="Markdown"); return

    if direction not in ("buy","sell","شراء","بيع"):
        await update.message.reply_text("⚠️ الاتجاه: buy أو sell"); return

    if engine.kill_switch.is_active:
        await update.message.reply_text("🔴 التنفيذ متوقف — Kill Switch مفعّل"); return

    user_id  = update.effective_user.id
    has_live   = engine.user_has_live_trading(user_id)
    force_mode = None  # None = يسأل, "real" = حقيقي, "virtual" = افتراضي
    if context.args and len(context.args) > 0:
        last_arg = context.args[-1].lower()
        if last_arg in ("real","حقيقي"):   force_mode = "real"
        elif last_arg in ("virtual","افتراضي","demo"): force_mode = "virtual"

    # M#112: إذا لديه ربط حقيقي → سؤال قبل التنفيذ
    if has_live and force_mode is None:
        info_q    = engine.get_user_exchange(user_id)
        ex_name_q = info_q.get("name","").upper() if info_q else "منصتك"
        kb_mode   = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ تداول حقيقي ({ex_name_q})",
                                 callback_data=f"execmode_real_{symbol}_{direction}_{size_usd:.2f}_{limit_price:.4f}"),
            InlineKeyboardButton("🎮 افتراضي",
                                 callback_data=f"execmode_virtual_{symbol}_{direction}_{size_usd:.2f}_{limit_price:.4f}"),
        ]])
        await update.message.reply_text(
            f"⚡ *كيف تريد تنفيذ الصفقة؟*\n\n"
            f"• {symbol} | {'شراء' if direction=='buy' else 'بيع'} | ${size_usd:,.2f}\n\n"
            f"اختر نوع التنفيذ:",
            parse_mode="Markdown",
            reply_markup=kb_mode)
        return  # ننتظر callback

    # إذا اختار virtual أو لا يوجد ربط → محفظة افتراضية
    if force_mode == "virtual" or not has_live:
        has_live = False

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

        # M#89: احترام حجم المستخدم بالضبط
        # risk_engine قد يُصغِّر الحجم للحماية — لكن لا يُكبِّره أبداً
        final_size = min(float(risk.approved_size or size_usd),
                          size_usd * ev_mult)
        # لا نرفع الحجم أبداً فوق ما طلبه المستخدم
        final_size = min(final_size, size_usd)
        # الحد الأدنى المطلق = $1 (ليس $10)
        final_size = max(final_size, 1.0)
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
            # M#89: فحص الحد الأدنى من المنصة
            try:
                ex_obj  = best_ex["exchange"]
                ex_name = best_ex.get("name", "")
                min_ord = await ex_obj.get_min_order_size(symbol) if hasattr(ex_obj, "get_min_order_size") else 1.0
                if final_size < min_ord:
                    await msg.edit_text(
                        f"❌ *الحجم أقل من الحد الأدنى*\n\n"
                        f"• طلبك: ${final_size:,.2f}\n"
                        f"• الحد الأدنى لـ {symbol} على {ex_name.upper()}: ${min_ord:,.2f}\n\n"
                        f"💡 جرّب: `/execute {symbol} {direction} {min_ord:.0f}`",
                        parse_mode="Markdown")
                    return
            except Exception as _me:
                pass

            # M#79: فحص الرصيد وعرضه في شاشة التأكيد
            balance_warn = ""
            try:
                actual_bal = await best_ex["exchange"].get_balance("USDT")
                if actual_bal < final_size:
                    balance_warn = (
                        f"\n⚠️ *تحذير:* رصيدك ${actual_bal:,.2f} أقل من الحجم ${final_size:,.2f}"
                        f"\n✏️ تعديل الحجم إلى ${actual_bal*0.9:.2f} تلقائياً"
                    )
                    final_size = actual_bal * 0.9
                elif actual_bal < final_size * 1.1:
                    balance_warn = f"\n💡 الرصيد المتاح: ${actual_bal:,.2f} (سيُستخدم {final_size/actual_bal*100:.0f}%)"
            except Exception:
                pass

            # إعادة حساب السعر والمستويات
            ep      = price * (1+0.1/100) if is_buy else price * (1-0.1/100)
            sl_price = ep * (1-float(risk.stop_loss_pct or 5)/100) if is_buy else ep * (1+float(risk.stop_loss_pct or 5)/100)
            tp_price = ep * (1+float(risk.take_profit_pct or 10)/100) if is_buy else ep * (1-float(risk.take_profit_pct or 10)/100)
            rr_ratio = float(risk.take_profit_pct or 10) / max(float(risk.stop_loss_pct or 5), 0.1)
            vol_m    = best_ex.get("volume_24h", 0) / 1e6

            kb = build_confirm_keyboard(symbol, trade_dir, final_size, best_ex.get("name",""))
            # M#90: عرض نوع الأمر (Market أو Limit)
            if limit_price > 0:
                is_b = direction == "long"
                if (is_b and price <= limit_price*1.005) or (not is_b and price >= limit_price*0.995):
                    order_type_ar = f"⚡ Market (السعر ${price:,.4f} أفضل من Limit)"
                else:
                    order_type_ar = f"⏳ Limit @ ${limit_price:,.4f} (السعر الحالي: ${price:,.4f})"
            else:
                order_type_ar = "⚡ Market (تنفيذ فوري)"
            # M#92: auto_protect للذهبي وأعلى
            from core.state_manager import state_manager as _sm2
            user_tier   = _sm2.get_tier(user_id)
            can_protect = user_tier in ("gold", "diamond", "admin")
            protect_note = "\n🛡️ الحماية التلقائية: ✅ مُفعَّلة" if can_protect else ""

            await msg.edit_text(
                f"📋 *تأكيد التنفيذ الحقيقي*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪙 {symbol} | {'🟢 شراء' if is_buy else '🔴 بيع'}\n"
                f"💰 الحجم: ${final_size:,.2f}\n"
                f"📈 نوع الأمر: {order_type_ar}\n"
                f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}%-)\n"
                f"🎯 هدف الربح:   ${tp_price:,.4f} ({risk.take_profit_pct:.1f}%+)\n"
                f"📊 R/R: 1:{rr_ratio:.1f}\n"
                f"🏦 المنصة: {best_ex.get('name','').upper()} (حجم ${vol_m:.0f}M)"
                + balance_warn + protect_note +
                f"\n\n⚠️ هذا تنفيذ حقيقي على حسابك",
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
            f"🛑 وقف الخسارة: ${sl_price:,.4f} ({risk.stop_loss_pct:.1f}%)",
            f"🎯 هدف الربح: ${tp_price:,.4f} ({risk.take_profit_pct:.1f}%)",
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
        # النقطة 1/7: بحث في Redis إذا لم توجد في الذاكرة
        if not trades and om and hasattr(om, "_load_trades_from_redis"):
            for d in om._load_trades_from_redis(user_id)[:10]:
                try:
                    from core.order_manager import LiveTrade as _LT2
                    trades.append(_LT2(
                        trade_id=d.get("trade_id",""), symbol=d.get("symbol",""),
                        side=d.get("side","Buy"), entry_price=float(d.get("entry_price",0)),
                        qty=float(d.get("qty",0)), size_usd=float(d.get("size_usd",0)),
                        stop_loss=float(d.get("stop_loss",0)),
                        take_profit=float(d.get("take_profit",0)),
                        status=d.get("status","CLOSED"),
                        pnl_usd=float(d.get("pnl_usd",0)),
                        pnl_pct=float(d.get("pnl_pct",0)),
                        user_id=int(d.get("user_id",user_id)),
                    ))
                except Exception: pass
        if not trades:
            await update.message.reply_text("📋 لا توجد صفقات مُسجَّلة بعد\n💡 جرّب /execute لتنفيذ صفقة"); return

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
            limit_p   = float(parts[4]) if len(parts) > 4 and parts[4] not in ("0", "0.0000") else 0.0

            # اختيار أفضل منصة
            best_ex = await engine.find_best_exchange(user_id, symbol)
            if not best_ex:
                await query.edit_message_text(
                    "⚠️ لا توجد منصة مرتبطة\n/live connect للإعداد"); return

            om    = best_ex.get("order_manager")
            if not om:
                await query.edit_message_text("⚠️ خطأ في Order Manager"); return

            # M#90: Limit Order → pending
            if limit_p > 0 and hasattr(om, "add_pending_limit"):
                from core.state_manager import state_manager as _sm3
                can_p = _sm3.get_tier(user_id) in ("gold","diamond","admin")
                is_bl = direction == "long"
                om.add_pending_limit(
                    symbol=symbol, side="Buy" if is_bl else "Sell",
                    size_usd=size_usd, limit_price=limit_p,
                    stop_loss_pct=5.0, take_profit_pct=10.0,
                    user_id=user_id, auto_protect=can_p,
                )
                await query.edit_message_text(
                    f"✅ *أمر Limit محفوظ*\n⏳ ينفَّذ عند: ${limit_p:,.4f}\n"
                    f"{'🛡️ الحماية مُفعَّلة' if can_p else ''}\n"
                    f"• إشعار عند التنفيذ\n• يُلغى بعد 24 ساعة",
                    parse_mode="Markdown")
                return

            # M#83: محاولة متعددة لجلب السعر مع fallback
            price = 0.0
            for _attempt in range(3):
                try:
                    price = await best_ex["exchange"].get_price(symbol)
                    if price > 0:
                        break
                    await asyncio.sleep(1)
                except Exception:
                    await asyncio.sleep(1)

            # fallback: OKX public API
            if price <= 0:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as _s:
                        async with _s.get(
                            f"https://www.okx.com/api/v5/market/ticker?instId={symbol.upper()}-USDT",
                            timeout=aiohttp.ClientTimeout(total=8)
                        ) as _r:
                            _d = await _r.json()
                            price = float((_d.get("data") or [{}])[0].get("last", 0) or 0)
                except Exception:
                    pass

            if price <= 0:
                await query.edit_message_text(
                    f"❌ تعذّر جلب سعر {symbol} حالياً\n\n"
                    f"• تحقق من أن {symbol} مدعومة على {best_ex.get('name','').upper()}\n"
                    f"• أعد المحاولة بعد دقيقة",
                    parse_mode="Markdown")
                return

            side = "Buy" if direction == "long" else "Sell"

            # M#79: تحقق من الرصيد الفعلي قبل التنفيذ
            try:
                actual_balance = await best_ex["exchange"].get_balance("USDT")
                if actual_balance < size_usd * 0.95:
                    await query.edit_message_text(
                        f"⚠️ *رصيد غير كافٍ*\n\n"
                        f"• رصيدك الفعلي: ${actual_balance:,.2f} USDT\n"
                        f"• حجم الصفقة: ${size_usd:,.2f}\n"
                        f"• الفرق: ${size_usd - actual_balance:,.2f}\n\n"
                        f"💡 أضف رصيداً أو قلل الحجم:\n"
                        f"`/execute {symbol} {'buy' if direction=='long' else 'sell'} {actual_balance*0.9:.0f}`",
                        parse_mode="Markdown")
                    return
                if actual_balance < size_usd * 1.05:
                    # تحذير: يستخدم معظم الرصيد
                    size_usd = actual_balance * 0.9  # استخدام 90% كحد أقصى
            except Exception as _be:
                logger.warning(f"balance check: {_be}")

            is_buy_ord = (side == "Buy")
            if limit_p > 0:
                if (is_buy_ord and price <= limit_p*1.005) or (not is_buy_ord and price >= limit_p*0.995):
                    actual_ot, actual_ep = "MARKET", price
                else:
                    actual_ot, actual_ep = "LIMIT", limit_p
            else:
                actual_ot, actual_ep = "MARKET", price
            trade = await om.open_trade(
                symbol=symbol, side=side, size_usd=size_usd,
                entry_price=actual_ep, stop_loss_pct=5.0, take_profit_pct=10.0,
                order_type=actual_ot, user_id=user_id,
                limit_price=limit_p if actual_ot=="LIMIT" else 0.0)

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
                # M#94: رسالة خطأ مفصلة بدل الرسالة العامة
                await query.edit_message_text(
                    "❌ *فشل التنفيذ*\n\n"
                    "الأسباب المحتملة:\n"
                    "• رصيد USDT غير كافٍ\n"
                    "• دقة الكمية غير مقبولة من المنصة\n"
                    "• صلاحيات API — تحقق من تفعيل Spot Trading\n"
                    "• الحد الأدنى للصفقة لم يُستوفَ\n\n"
                    "💡 جرّب /live لمراجعة حالة الاتصال",
                    parse_mode="Markdown")
        except Exception as e:
            logger.error(f"handle_trade_callback: {e}")
            await query.edit_message_text(f"❌ خطأ: {str(e)[:100]}")


def build_confirm_keyboard(symbol: str, direction: str,
                             size_usd: float,
                             exchange_name: str = "",
                             limit_price: float = 0.0) -> InlineKeyboardMarkup:
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

    # المجاني: لا ربط تداول حقيقي
    if action in ("connect", "on", "margin", "futures"):
        if _sm.get_tier(update.effective_user.id) == "free":
            await update.message.reply_text(
                "🔒 *ربط منصة التداول الحقيقي غير متاح في الباقة المجانية*\n\n"
                "⬆️ للترقية: /upgrade",
                parse_mode="Markdown"); return
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
            # تفعيل engine flag أيضاً
            _eng_ref = _eng(context)
            if _eng_ref:
                _eng_ref.auto_trade_enabled = True
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
            f"📉 Drawdown: {report.get('drawdown_pct',0):.1f}%",
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
# ══ /wallet ══════════════════════════════════════════════════════════════════

@security_check
async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """T2: عرض المحفظتين — الافتراضية والحقيقية."""
    user_id = update.effective_user.id
    await db.add_to_memory(user_id, "/wallet")
    from core.state_manager import state_manager as _sm_w
    from core.virtual_wallet import VirtualWallet as _VW_w

    # المحفظة الافتراضية
    vw_data = _sm_w.get_virtual_wallet(user_id) or {}
    if not vw_data:
        vw_data = await db.get_virtual_wallet(user_id) or {}
    vw = _VW_w(vw_data) if vw_data else _VW_w({})

    lines = [
        "💼 *محافظ رائد*",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🎮 *المحفظة الافتراضية*",
        f"• الرصيد النقدي: ${vw.balance:,.2f}",
        f"• مُستثمر: ${vw.invested:,.2f}",
        f"• إجمالي: ${vw.total_value:,.2f}",
        f"• مراكز مفتوحة: {len(vw.positions)}",
    ]

    # إجمالي PnL
    sells = [t for t in vw.history if t.get("type") == "sell"]
    net_pnl = sum(t.get("pnl", 0) for t in sells)
    if sells:
        lines.append(f"• صافي الربح: ${net_pnl:+,.2f}")

    # المحفظة الحقيقية
    lines += ["", "💱 *المحفظة الحقيقية*"]
    try:
        engine = context.bot_data.get("raed_engine")
        if engine:
            ex_info = engine.get_user_exchange(user_id) if hasattr(engine, "get_user_exchange") else None
            if ex_info:
                lines.append(f"• المنصة: {ex_info.get('exchange','').upper()} ✅")
            else:
                lines.append("• غير مربوطة — /live لربط منصة تداول")
    except Exception:
        lines.append("• غير مربوطة — /live لربط منصة تداول")

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 الصفقات الافتراضية", callback_data="goto_vtrades"),
        InlineKeyboardButton("💱 الصفقات الحقيقية",  callback_data="goto_trades"),
    ],[
        InlineKeyboardButton(f"{E['report']} تقرير كامل", callback_data="vhistory"),
        InlineKeyboardButton(f"{E['trash']} إعادة ضبط",  callback_data="vreset_confirm"),
    ]])
    await update.message.reply_text(
        "\n".join(lines) + _sig(),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=buttons)


# ══ /vtrades ══════════════════════════════════════════════════════════════════

@security_check
async def cmd_vtrades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """T1: إدارة الصفقات الافتراضية المفتوحة."""
    user_id = update.effective_user.id
    from core.state_manager import state_manager as _sm_vt
    from core.virtual_wallet import VirtualWallet as _VW_vt
    from core.data_layer import DataLayer

    vw_data = _sm_vt.get_virtual_wallet(user_id) or {}
    vw = _VW_vt(vw_data) if vw_data else _VW_vt({})

    if not vw.positions:
        await update.message.reply_text(
            "📋 *الصفقات الافتراضية*\n\n"
            "لا توجد صفقات مفتوحة حالياً.\n"
            "سيتم التنفيذ تلقائياً عند وجود إشارة قوية ≥ 80% ✅",
            parse_mode=ParseMode.MARKDOWN)
        return

    engine = context.bot_data.get("raed_engine")
    lines  = ["📋 *الصفقات الافتراضية المفتوحة*", "━━━━━━━━━━━━━━━━━━", ""]
    buttons_list = []

    for sym, pos in vw.positions.items():
        # السعر الحالي
        cur_price = pos["avg_price"]
        if engine:
            try:
                pd = await engine.data_layer.get_price(sym.replace("USDT",""))
                if pd: cur_price = float(pd.get("price", cur_price))
            except: pass

        live_pnl = (cur_price - pos["avg_price"]) * pos["quantity"]
        pnl_pct  = live_pnl / max(pos["cost"], 1) * 100
        sign     = "+" if live_pnl >= 0 else ""
        emoji    = "📈" if live_pnl >= 0 else "📉"

        lines += [
            f"*{sym}*",
            f"• دخول: ${pos['avg_price']:,.4f} | الحالي: ${cur_price:,.4f}",
            f"• PnL: {emoji} {sign}${live_pnl:,.2f} ({sign}{pnl_pct:.1f}%)",
            f"• TP: ${pos.get('take_profit',0):,.4f} | SL: ${pos.get('stop_loss',0):,.4f}",
            "",
        ]
        # أزرار الإدارة
        buttons_list.append([
            InlineKeyboardButton(f"✅ إغلاق {sym} كامل",   callback_data=f"vclose_{sym}_100"),
            InlineKeyboardButton(f"50% إغلاق",             callback_data=f"vclose_{sym}_50"),
        ])
        buttons_list.append([
            InlineKeyboardButton(f"📊 تفاصيل {sym}",       callback_data=f"vdetail_{sym}"),
        ])

    lines.append(f"💰 الرصيد المتاح: ${vw.balance:,.2f}")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons_list) if buttons_list else None)


# ══ /virtual ══════════════════════════════════════════════════════════════════

@security_check
async def cmd_virtual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /virtual buy BTC 500   — شراء وهمي بـ $500
    /virtual sell BTC      — بيع كامل مركز BTC
    /virtual sell BTC 0.01 — بيع كمية محددة
    """
    user_id = update.effective_user.id
    await db.add_to_memory(user_id, "/virtual")
    args = context.args

    if len(args) < 2:
        await update.message.reply_text(
            f"{E['virtual']} التداول الوهمي\n\n"
            f"الاستخدام:\n"
            f"  /virtual buy BTC 500   — شراء بـ $500\n"
            f"  /virtual sell BTC      — بيع المركز كاملاً\n"
            f"  /virtual sell BTC 0.01 — بيع كمية محددة\n\n"
            f"{E['wallet']} محفظتك: /wallet" + _sig()
        )
        return

    action = args[0].lower()
    symbol = args[1].upper().replace("USDT", "") + "USDT"

    wallet_data = await db.get_virtual_wallet(user_id)
    wallet = VirtualWallet(wallet_data)

    # سعر وهمي
    mock_prices = {
        "BTCUSDT": 67420.50, "ETHUSDT": 3521.30, "BNBUSDT": 598.40,
        "SOLUSDT": 172.80, "XRPUSDT": 0.5821, "ADAUSDT": 0.4521,
    }
    price = mock_prices.get(symbol, 1.0)

    if action == "buy":
        amount = float(args[2]) if len(args) > 2 else 100.0
        result = wallet.buy(symbol, price, amount)
    elif action == "sell":
        qty = float(args[2]) if len(args) > 2 else None
        result = wallet.sell(symbol, price, qty)
    else:
        await update.message.reply_text(
            f"{E['error']} أمر غير معروف. استخدم: buy أو sell" + _sig()
        )
        return

    await db.update_virtual_wallet(user_id, wallet.to_dict())
    await update.message.reply_text(result["msg"] + _sig())


# ══ /report ══════════════════════════════════════════════════════════════════

@security_check
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await db.add_to_memory(user_id, "/report")
    user = await db.get_user(user_id)
    if not user:
        return

    wallet_data = user.get("virtual_wallet", {})
    wallet = VirtualWallet(wallet_data)
    plan = user.get("plan", "free")

    total = wallet.balance + wallet.invested
    pnl   = total - 10000
    pct   = (pnl / 10000 * 100)
    sign  = "+" if pnl >= 0 else ""
    emoji = E["up"] if pnl >= 0 else E["down"]

    sells  = [t for t in wallet.history if t["type"] == "sell"]
    wins   = [t for t in sells if t.get("pnl", 0) > 0]
    wr     = (len(wins) / len(sells) * 100) if sells else 0

    msg = (
        f"{E['report']} تقرير الأداء اليومي\n\n"
        f"{'─' * 28}\n"
        f"👤 {user.get('full_name', 'مستخدم')}\n"
        f"📦 الباقة: {PLANS[plan]['name']}\n\n"
        f"💰 المحفظة الافتراضية:\n"
        f"  إجمالي: ${total:,.2f}\n"
        f"  {emoji} العائد: {sign}${pnl:,.2f} ({sign}{pct:.2f}%)\n"
        f"  ✅ نسبة الربح: {wr:.1f}%\n"
        f"  📊 إجمالي الصفقات: {len(sells)}\n\n"
        f"{'─' * 28}\n"
        f"{E['brain']} نصيحة رائد اليوم:\n"
        f"«الصبر أساس التداول الناجح. لا تتخذ قرارات عاطفية.»\n"
    )

    await update.message.reply_text(msg + _sig())


# ══ /about ═══════════════════════════════════════════════════════════════════

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"{E['bot']} رائد التداول الذكي\n"
        f"الإصدار 1.0.0\n\n"
        f"{'═' * 28}\n"
        f"رائد هو مساعد تداول ذكي يعمل\n"
        f"على مدار الساعة لمساعدتك في\n"
        f"متابعة الأسواق وإدارة محفظتك.\n\n"
        f"{'─' * 28}\n"
        f"{E['rocket']} المنصات المدعومة:\n"
        f"🟡 Binance | 🔵 OKX\n"
        f"🟠 Bybit  | ⚫ Bitget\n\n"
        f"{'─' * 28}\n"
        f"{E['lock']} الأمان:\n"
        f"تشفير AES-256 لجميع المفاتيح\n"
        f"حماية ضد الاحتيال والتصيد\n\n"
        f"{'─' * 28}\n"
        f"⚖️ الترخيص:\n"
        f"مبني على NexusTrader\n"
        f"MIT License — Quantweb3\n\n"
        f"تطوير: فريق رائد التداول الذكي\n"
        f"{'═' * 28}"
    )
    await update.message.reply_text(msg)


# ══ /upgrade ══════════════════════════════════════════════════════════════════

@security_check
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.add_to_memory(update.effective_user.id, "/upgrade")
    msg = (
        f"{E['star']} باقات رائد التداول الذكي\n\n"
        f"{'─' * 28}\n"
        f"🆓 مجاني — $0/شهر\n"
        f"محفظة افتراضية | 5 عملات | تنبيه واحد\n\n"
        f"🥈 فضي — $9/شهر\n"
        f"كل البورصات | 10 تنبيهات | تداول حقيقي\n\n"
        f"🥇 ذهبي — $29/شهر ⭐ الأكثر طلباً\n"
        f"تداول آلي TWAP | تحليل AI | أولوية الدعم\n\n"
        f"💎 ماسي — $99/شهر\n"
        f"كل الميزات | دعم 24/7 | استراتيجيات مخصصة\n\n"
        f"{'─' * 28}\n"
        f"للاشتراك تواصل مع: @admin"
    )
    await update.message.reply_text(msg + _sig(), reply_markup=_plan_keyboard())


# ══ /security ══════════════════════════════════════════════════════════════════

async def cmd_security(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"{E['shield']} مركز الأمان — رائد التداول الذكي\n\n"
        f"{'─' * 28}\n"
        f"{E['lock']} رائد لن يطلب أبداً:\n"
        f"  ❌ كلمة مرور أو رمز 2FA\n"
        f"  ❌ تحويل أموال إلى أي عنوان\n"
        f"  ❌ النقر على روابط خارجية\n"
        f"  ❌ مفاتيح محفظتك الخاصة\n"
        f"  ❌ عبارة الاسترداد (Seed Phrase)\n\n"
        f"{'─' * 28}\n"
        f"{E['ok']} نصائح الأمان:\n"
        f"  ✅ استخدم مفاتيح API بصلاحية Read+Trade فقط\n"
        f"  ✅ لا تعطِ صلاحية السحب أبداً\n"
        f"  ✅ فعّل 2FA على البورصة\n"
        f"  ✅ راجع الصلاحيات أسبوعياً\n\n"
        f"{'─' * 28}\n"
        f"{E['warn']} إذا شككت في أي رسالة:\n"
        f"تجاهلها فوراً وأبلغ: @admin"
    )
    await update.message.reply_text(msg)


# ══ /admin ════════════════════════════════════════════════════════════════════

@requires_admin
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await db.get_stats()
    user_counts = stats["users"]
    msg = (
        f"{E['settings']} لوحة المشرف — رائد\n\n"
        f"{'─' * 28}\n"
        f"👥 إجمالي المستخدمين: {user_counts['total']}\n"
        f"  🆓 مجاني:  {user_counts.get('free', 0)}\n"
        f"  🥈 فضي:   {user_counts.get('silver', 0)}\n"
        f"  🥇 ذهبي:  {user_counts.get('gold', 0)}\n"
        f"  💎 ماسي:  {user_counts.get('diamond', 0)}\n\n"
        f"{'─' * 28}\n"
        f"{E['shield']} الأمان:\n"
        f"  أنماط محظورة: {stats['blocked_patterns']}\n"
        f"  Redis: {'✅' if stats['redis_ping'] else '❌'}\n\n"
        f"الأوامر:\n"
        f"  /broadcast [رسالة] — إرسال للجميع\n"
        f"  /setplan [user_id] [plan] — تغيير الباقة\n"
        f"  /ban [user_id] — حظر مستخدم\n"
    )
    await update.message.reply_text(msg)


# ══ Callback Query Handler ════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cmd_wallet":
        user_id = query.from_user.id
        wallet_data = await db.get_virtual_wallet(user_id)
        wallet = VirtualWallet(wallet_data)
        await query.message.reply_text(wallet.report() + _sig())

    elif data == "cmd_help":
        user = await db.get_user(query.from_user.id)
        plan = user.get("plan", "free") if user else "free"
        await query.message.reply_text(
            f"{E['help']} اكتب /help لقائمة الأوامر الكاملة"
        )

    elif data == "cmd_upgrade":
        await query.message.reply_text(
            f"{E['star']} اكتب /upgrade لعرض الباقات"
        )

    elif data.startswith("vbuy_"):
        symbol = data.replace("vbuy_", "")
        if symbol == "prompt":
            await query.message.reply_text(
                f"{E['virtual']} للشراء الوهمي:\n/virtual buy BTC 100"
            )
        else:
            await query.message.reply_text(
                f"{E['virtual']} للشراء الوهمي:\n/virtual buy {symbol.replace('USDT','')} 100"
            )

    elif data == "vreset_confirm":
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، أعد الضبط", callback_data="vreset_do"),
            InlineKeyboardButton("❌ إلغاء", callback_data="vreset_cancel"),
        ]])
        await query.message.reply_text(
            f"{E['warn']} هل أنت متأكد من إعادة ضبط المحفظة الافتراضية؟\n"
            f"سيُعاد رصيدك إلى $10,000 وتُحذف جميع الصفقات.",
            reply_markup=buttons
        )

    elif data == "vreset_do":
        user_id = query.from_user.id
        wallet_data = await db.get_virtual_wallet(user_id)
        wallet = VirtualWallet(wallet_data)
        msg = wallet.reset()
        await db.update_virtual_wallet(user_id, wallet.to_dict())
        await query.message.reply_text(msg + _sig())

    elif data == "vreset_cancel":
        await query.message.reply_text(f"{E['ok']} تم الإلغاء")

    elif data.startswith("plan_"):
        plan = data.replace("plan_", "")
        plan_info = PLANS.get(plan, {})
        await query.message.reply_text(
            f"{plan_info.get('name', plan)}\n\n"
            f"السعر: ${plan_info.get('price', 0)}/شهر\n"
            f"للاشتراك: تواصل مع @admin"
        )


# ══ Unknown Command ══════════════════════════════════════════════════════════

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{E['help']} لم أفهم هذا الأمر.\n"
        f"اكتب /help لقائمة الأوامر.\n\n"
        f"أو اختر من القائمة:",
        reply_markup=_main_keyboard()
    )


# ══ Handle Text Messages ══════════════════════════════════════════════════════

@security_check
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية العادية"""
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # ردود بسيطة
    greetings = ["مرحبا", "هلا", "السلام", "أهلا", "هاي", "hi", "hello", "مرحباً"]
    if any(g in text.lower() for g in greetings):
        user = await db.get_user(user_id)
        name = user.get("full_name", "") if user else ""
        await update.message.reply_text(
            f"أهلاً {name}! {E['bot']}\n\n"
            f"كيف يمكنني مساعدتك اليوم؟\n"
            f"اكتب /help لقائمة الأوامر.",
            reply_markup=_main_keyboard()
        )
        return

    # السعر السريع (إذا كتب المستخدم رمز عملة مباشرة)
    coins = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE"]
    text_upper = text.upper().replace("USDT", "").replace("/", "")
    if text_upper in coins:
        context.args = [text_upper]
        await cmd_price(update, context)
        return

    await update.message.reply_text(
        f"{E['help']} اكتب /help لقائمة الأوامر المتاحة."
    )


# ══ /profile ═════════════════════════════════════════════════════════════════

@security_check
async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض وتحديث الملف الشخصي + الاستبيان."""
    user_id = update.effective_user.id
    from core.state_manager import state_manager as _sm_pr

    profile = _sm_pr.get_profile(user_id)
    can_update, reason = _sm_pr.can_update_profile(user_id)
    strategy = _sm_pr.get_strategy_config(user_id)
    tier_name = _sm_pr.get_tier_name(user_id)

    if not profile:
        # لم يُكمل الاستبيان بعد
        await _start_survey(update, context)
        return

    lines = [
        "👤 *ملفك الشخصي — رائد*",
        "━━━━━━━━━━━━━━━━━━",
        f"• الباقة: {tier_name}",
        f"• هدفك: {profile.get('goal', 'غير محدد')}",
        f"• مستوى المخاطرة: {profile.get('risk_level', 'متوسط')}",
        f"• مدة الاحتفاظ المفضلة: {profile.get('hold_period', 'أيام')}",
        f"• خبرة التداول: {profile.get('experience', 'متوسط')}",
        "",
        "⚙️ *الاستراتيجية المخصصة*",
        f"• عتبة الثقة: {strategy['min_confidence']:.0%}",
        f"• أقصى حجم صفقة: {strategy['max_position_pct']}%",
        f"• أقصى صفقات يومية: {strategy['max_daily_trades']}",
        "",
        reason,
    ]

    buttons = []
    if can_update:
        buttons.append([InlineKeyboardButton("✏️ تحديث الملف", callback_data="profile_update")])
    buttons.append([InlineKeyboardButton("📊 عرض المخالفات", callback_data="profile_violations")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def _start_survey(update, context):
    """بدء الاستبيان الشخصي."""
    context.user_data["survey_step"] = 1
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("💰 حفظ رأس المال",  callback_data="survey_goal_preserve"),
        InlineKeyboardButton("📈 نمو معتدل",       callback_data="survey_goal_moderate"),
        InlineKeyboardButton("🚀 نمو مرتفع",       callback_data="survey_goal_aggressive"),
    ]])
    await update.message.reply_text(
        "👤 *الاستبيان الشخصي — رائد*\n\n"
        "سؤال 1/5: ما هدفك الأساسي من التداول؟",
        parse_mode="Markdown",
        reply_markup=buttons)


async def cb_survey_goal(update, context):
    """معالجة إجابة هدف التداول."""
    query = update.callback_query
    await query.answer()
    goal_map = {
        "survey_goal_preserve":    "حفظ رأس المال",
        "survey_goal_moderate":    "نمو معتدل",
        "survey_goal_aggressive":  "نمو مرتفع",
    }
    goal = goal_map.get(query.data, "نمو معتدل")
    context.user_data["survey"] = {"goal": goal}
    context.user_data["survey_step"] = 2

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 منخفض (5%)",   callback_data="survey_risk_low"),
        InlineKeyboardButton("🟡 متوسط (10%)",  callback_data="survey_risk_medium"),
        InlineKeyboardButton("🔴 مرتفع (20%+)", callback_data="survey_risk_high"),
    ]])
    await query.edit_message_text(
        "👤 *الاستبيان الشخصي — رائد*\n\n"
        f"✅ الهدف: {goal}\n\n"
        "سؤال 2/5: ما أقصى خسارة تتحملها في صفقة واحدة؟",
        parse_mode="Markdown",
        reply_markup=buttons)


async def cb_survey_risk(update, context):
    """معالجة مستوى المخاطرة."""
    query = update.callback_query
    await query.answer()
    risk_map = {
        "survey_risk_low":    "low",
        "survey_risk_medium": "medium",
        "survey_risk_high":   "high",
    }
    risk = risk_map.get(query.data, "medium")
    context.user_data.setdefault("survey", {})["risk_level"] = risk

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚡ ساعات (Scalp)", callback_data="survey_hold_hours"),
        InlineKeyboardButton("📅 أيام (Swing)",  callback_data="survey_hold_days"),
        InlineKeyboardButton("📆 أسابيع (Long)", callback_data="survey_hold_weeks"),
    ]])
    await query.edit_message_text(
        "👤 *الاستبيان الشخصي — رائد*\n\n"
        "سؤال 3/5: ما مدة الاحتفاظ المفضلة لديك؟",
        parse_mode="Markdown",
        reply_markup=buttons)


async def cb_survey_hold(update, context):
    """معالجة مدة الاحتفاظ."""
    query = update.callback_query
    await query.answer()
    hold_map = {
        "survey_hold_hours": "ساعات",
        "survey_hold_days":  "أيام",
        "survey_hold_weeks": "أسابيع",
    }
    hold = hold_map.get(query.data, "أيام")
    context.user_data.setdefault("survey", {})["hold_period"] = hold

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌱 مبتدئ",   callback_data="survey_exp_beginner"),
        InlineKeyboardButton("📊 متوسط",   callback_data="survey_exp_medium"),
        InlineKeyboardButton("🏆 محترف",   callback_data="survey_exp_expert"),
    ]])
    await query.edit_message_text(
        "👤 *الاستبيان الشخصي — رائد*\n\n"
        "سؤال 4/5: ما مستوى خبرتك في التداول؟",
        parse_mode="Markdown",
        reply_markup=buttons)


async def cb_survey_exp(update, context):
    """معالجة مستوى الخبرة."""
    query = update.callback_query
    await query.answer()
    exp_map = {
        "survey_exp_beginner": "مبتدئ",
        "survey_exp_medium":   "متوسط",
        "survey_exp_expert":   "محترف",
    }
    exp = exp_map.get(query.data, "متوسط")
    context.user_data.setdefault("survey", {})["experience"] = exp

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تلقائي",         callback_data="survey_exec_auto"),
        InlineKeyboardButton("👁️ إشعار + موافقة", callback_data="survey_exec_manual"),
    ]])
    await query.edit_message_text(
        "👤 *الاستبيان الشخصي — رائد*\n\n"
        "سؤال 5/5: هل تريد تنفيذاً تلقائياً للصفقات أم موافقة يدوية؟",
        parse_mode="Markdown",
        reply_markup=buttons)


async def cb_survey_exec(update, context):
    """إنهاء الاستبيان وحفظه."""
    query = update.callback_query
    await query.answer()
    exec_map = {
        "survey_exec_auto":   "تلقائي",
        "survey_exec_manual": "يدوي",
    }
    exec_pref = exec_map.get(query.data, "تلقائي")
    survey = context.user_data.get("survey", {})
    survey["execution"] = exec_pref

    from core.state_manager import state_manager as _sm_sv
    user_id = query.from_user.id
    _sm_sv.save_profile(user_id, survey)

    await query.edit_message_text(
        "✅ *الاستبيان مكتمل!*\n\n"
        f"• الهدف: {survey.get('goal','—')}\n"
        f"• المخاطرة: {survey.get('risk_level','—')}\n"
        f"• مدة الاحتفاظ: {survey.get('hold_period','—')}\n"
        f"• الخبرة: {survey.get('experience','—')}\n"
        f"• التنفيذ: {exec_pref}\n\n"
        "رائد سيخصص استراتيجيته بناءً على ملفك الشخصي 🎯",
        parse_mode="Markdown")


async def cb_profile_violations(update, context):
    """عرض قائمة مخالفات الخطة."""
    query = update.callback_query
    await query.answer()
    from core.state_manager import state_manager as _sm_pv
    uid = query.from_user.id
    violations = _sm_pv.get_violations(uid)
    if not violations:
        await query.edit_message_text("✅ لا مخالفات مسجَّلة — أنت تسير وفق خطتك!")
        return
    lines = ["📋 *المخالفات المسجَّلة*\n"]
    for v in violations[-10:]:
        import datetime
        ts = datetime.datetime.fromtimestamp(v.get("ts",0)).strftime("%Y-%m-%d")
        lines.append(f"• {ts}: {v.get('description','مخالفة')}")
    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown")


# ══ Callbacks: vtrades ════════════════════════════════════════════════════════

async def cb_vclose(update, context):
    """إغلاق صفقة افتراضية كاملة أو جزئية."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    from core.state_manager import state_manager as _sm_vc
    from core.virtual_wallet import VirtualWallet as _VW_vc

    parts   = query.data.split("_")  # vclose_BTCUSDT_100
    sym     = parts[1] if len(parts) > 1 else ""
    pct     = int(parts[2]) if len(parts) > 2 else 100

    vw_data = _sm_vc.get_virtual_wallet(user_id) or {}
    vw      = _VW_vc(vw_data)

    if sym not in vw.positions:
        await query.edit_message_text(f"❌ لا يوجد مركز مفتوح على {sym}")
        return

    # السعر الحالي
    engine    = context.bot_data.get("raed_engine")
    cur_price = vw.positions[sym]["avg_price"]
    if engine:
        try:
            pd = await engine.data_layer.get_price(sym.replace("USDT",""))
            if pd: cur_price = float(pd.get("price", cur_price))
        except: pass

    # حساب الكمية
    qty    = vw.positions[sym]["quantity"]
    sell_q = qty if pct == 100 else qty * (pct / 100)

    result = vw.sell(sym, cur_price, sell_q)
    if result.get("ok"):
        _sm_vc.save_virtual_wallet(user_id, vw.to_dict())
        pnl = result.get("trade", {}).get("pnl", 0)
        sign = "+" if pnl >= 0 else ""
        # تسجيل في drift_monitor
        try:
            if engine:
                engine.drift_monitor.record_outcome(pnl > 0)
        except: pass
        _close_type = "كامل" if pct == 100 else f"{pct}%"
        await query.edit_message_text(
            f"✅ *تم الإغلاق*\n\n"
            f"• {sym} {_close_type}\n"
            f"• سعر الإغلاق: ${cur_price:,.4f}\n"
            f"• PnL: {sign}${pnl:,.2f}\n"
            f"• الرصيد: ${vw.balance:,.2f}\n\n"
            "🎮 /vtrades لعرض الصفقات",
            parse_mode="Markdown")
    else:
        await query.edit_message_text(f"❌ {result.get('msg','خطأ')}")


async def cb_goto_vtrades(update, context):
    await update.callback_query.answer()
    await cmd_vtrades(update, context)


async def cb_goto_trades(update, context):
    await update.callback_query.answer()
    context.args = []
    # /trades موجود في trading.py
    from handlers.trading import cmd_trades
    await cmd_trades(update, context)


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
    app.add_handler(CallbackQueryHandler(
        cb_autotrade_decision, pattern=r"^autotrade_(confirm|ignore)"))
    # T1/T2/T4: أوامر جديدة
    app.add_handler(CommandHandler("wallet",   cmd_wallet))
    app.add_handler(CommandHandler("vtrades",  cmd_vtrades))
    app.add_handler(CommandHandler("profile",  cmd_profile))
    # Survey callbacks
    app.add_handler(CallbackQueryHandler(cb_survey_goal,    pattern=r"^survey_goal_"))
    app.add_handler(CallbackQueryHandler(cb_survey_risk,    pattern=r"^survey_risk_"))
    app.add_handler(CallbackQueryHandler(cb_survey_hold,    pattern=r"^survey_hold_"))
    app.add_handler(CallbackQueryHandler(cb_survey_exp,     pattern=r"^survey_exp_"))
    app.add_handler(CallbackQueryHandler(cb_survey_exec,    pattern=r"^survey_exec_"))
    app.add_handler(CallbackQueryHandler(cb_profile_violations, pattern=r"^profile_violations$"))
    # vtrades callbacks
    app.add_handler(CallbackQueryHandler(cb_vclose,       pattern=r"^vclose_"))
    app.add_handler(CallbackQueryHandler(cb_goto_vtrades, pattern=r"^goto_vtrades$"))
    app.add_handler(CallbackQueryHandler(cb_goto_trades,  pattern=r"^goto_trades$"))

@require_tier("setcustom")
async def cmd_setcustom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يضبط باقة خاصة لمستخدم — للمدير فقط. /setcustom user_id coins label"""
    engine = _eng(context)
    if not engine: return
    user_id = update.effective_user.id
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "⚙️ *الاستخدام:*\n`/setcustom [user_id] [coins] [label]`\n\n"
            "مثال: `/setcustom 123456 50 مشرف`",
            parse_mode="Markdown"); return
    try:
        target = int(args[0])
        coins  = min(int(args[1]), 9999)
        label  = " ".join(args[2:]) if len(args) > 2 else "⚙️ خاصة"
        config = {"coins": coins, "label": label}
        ok = _sm.set_custom_tier(target, config, requester_id=user_id)
        await update.message.reply_text(
            f"{'✅' if ok else '❌'} باقة خاصة لـ `{target}`\n"
            f"عملات: {coins} | الاسم: {label}",
            parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")


async def cb_autotrade_decision(update, context):
    """معالجة قرار المستخدم: نفّذ أو تجاهل الصفقات الآلية."""
    query  = update.callback_query
    await query.answer()
    engine = _eng(context)
    if not engine:
        await query.edit_message_text("⚠️ النظام لم يُهيَّأ")
        return

    data = query.data
    if data == "autotrade_ignore":
        await query.edit_message_text("❌ تم تجاهل الإشارات.")
        return

    # استخراج بيانات الصفقة
    try:
        sig_str = data.replace("autotrade_confirm_", "")
        trades  = []
        for part in sig_str.split(","):
            bits = part.split("_")
            if len(bits) >= 3:
                trades.append({
                    "symbol":    bits[0],
                    "direction": bits[1],
                    "price":     float(bits[2]),
                })
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ: {e}")
        return

    lines = ["✅ *صفقات مُنفَّذة*", ""]
    from core.virtual_wallet import VirtualWallet as _VW
    from core.state_manager  import state_manager as _sm_cb
    uid = query.from_user.id

    for t in trades:
        try:
            price_d = await engine.data_layer.get_price(t["symbol"])
            price   = float((price_d or {}).get("price") or t["price"])
            _wdata  = _sm_cb.get_virtual_wallet(uid) or {
                "balance": 10000.0, "invested": 0.0,
                "profit": 0.0, "positions": {}, "history": []}
            _vw     = _VW(_wdata)
            _amt    = min(_vw.total_value * 0.10, _vw.balance)
            _result = _vw.buy(t["symbol"], price, max(_amt, 50))
            if _result.get("ok"):
                _sm_cb.save_virtual_wallet(uid, _vw.to_dict())
                dir_ar = "🟢 شراء" if t["direction"] == "long" else "🔴 بيع"
                lines.append(f"• {t['symbol']} {dir_ar} ${_amt:,.0f} ✅")
            else:
                lines.append(f"• {t['symbol']}: {_result.get('msg','')[:50]}")
        except Exception as e:
            lines.append(f"• {t['symbol']}: ❌ {str(e)[:50]}")

    await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

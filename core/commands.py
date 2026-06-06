"""
🤖 رائد التداول الذكي — معالجات أوامر تيليجرام
الواجهة العربية الكاملة
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from loguru import logger

from core.config import E, RAED_NAME, RAED_CREDIT, RAED_TEAM, PLANS, EXCHANGES, MSG
from core.database import db
from core.virtual_wallet import VirtualWallet
from security.guard import security_check, requires_plan, requires_admin


def _sig() -> str:
    return f"\n\n─────────────────\n📊 {RAED_NAME}\n{RAED_CREDIT}\n{RAED_TEAM}"


def _plan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🆓 مجاني", callback_data="plan_free"),
        InlineKeyboardButton("🥈 فضي $9", callback_data="plan_silver"),
    ],[
        InlineKeyboardButton("🥇 ذهبي $29", callback_data="plan_gold"),
        InlineKeyboardButton("💎 ماسي $99", callback_data="plan_diamond"),
    ]])


def _main_keyboard(plan: str = "free") -> InlineKeyboardMarkup:
    """لوحة المفاتيح الرئيسية حسب الباقة"""
    buttons = [
        [
            InlineKeyboardButton(f"{E['chart']} الأسعار", callback_data="cmd_price"),
            InlineKeyboardButton(f"{E['virtual']} محفظتي", callback_data="cmd_wallet"),
        ],
        [
            InlineKeyboardButton(f"{E['report']} تقرير", callback_data="cmd_report"),
            InlineKeyboardButton(f"{E['bell']} تنبيهاتي", callback_data="cmd_alerts"),
        ],
    ]
    if plan in ("silver", "gold", "diamond"):
        buttons.append([
            InlineKeyboardButton(f"{E['bank']} تداول", callback_data="cmd_trade"),
            InlineKeyboardButton(f"{E['settings']} الإعدادات", callback_data="cmd_settings"),
        ])
    if plan in ("gold", "diamond"):
        buttons.append([
            InlineKeyboardButton(f"{E['rocket']} تداول آلي", callback_data="cmd_auto"),
            InlineKeyboardButton(f"{E['brain']} الاستراتيجيات", callback_data="cmd_strategy"),
        ])
    buttons.append([
        InlineKeyboardButton(f"{E['help']} المساعدة", callback_data="cmd_help"),
        InlineKeyboardButton(f"{E['star']} الترقية", callback_data="cmd_upgrade"),
    ])
    return InlineKeyboardMarkup(buttons)


# ══ /start ═══════════════════════════════════════════════════════════════════

@security_check
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user  = update.effective_user
    user     = await db.get_or_create_user(
        tg_user.id, tg_user.username, tg_user.full_name
    )
    await db.add_to_memory(tg_user.id, "/start")
    plan     = user.get("plan", "free")
    plan_info = PLANS[plan]
    is_new   = len(user.get("stats", {}).get("last_commands", [])) <= 1

    if is_new:
        msg = (
            f"أهلاً وسهلاً يا {tg_user.first_name}! {E['saudi']}\n\n"
            f"أنا {E['bot']} رائد، مساعدك الذكي في عالم التداول!\n\n"
            f"{'═' * 28}\n"
            f"{E['chart']} أتابع الأسواق على مدار الساعة\n"
            f"{E['virtual']} محفظة افتراضية $10,000 للتدريب\n"
            f"{E['bell']} تنبيهات فورية عند تحقق أهدافك\n"
            f"{E['lock']} أمان تام لمفاتيح API الخاصة بك\n"
            f"{E['brain']} أتذكر تفضيلاتك دائماً\n"
            f"{'═' * 28}\n\n"
            f"باقتك الحالية: {plan_info['name']}\n\n"
            f"اختر ما تريد من القائمة أدناه:"
        )
    else:
        msg = (
            f"مرحباً مجدداً يا {tg_user.first_name}! {E['bot']}\n\n"
            f"باقتك: {plan_info['name']}\n"
            f"ماذا تريد اليوم؟"
        )

    await update.message.reply_text(msg + _sig(), reply_markup=_main_keyboard(plan))


# ══ /help ════════════════════════════════════════════════════════════════════

@security_check
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await db.get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.full_name,
    )
    await db.add_to_memory(update.effective_user.id, "/help")
    plan = user.get("plan", "free")

    msg = (
        f"{E['help']} دليل أوامر رائد\n\n"
        f"{'─' * 28}\n"
        f"الأوامر الأساسية (جميع الباقات):\n"
        f"  /start — بدء الاستخدام\n"
        f"  /price [عملة] — السعر اللحظي\n"
        f"  /wallet — المحفظة الافتراضية\n"
        f"  /virtual buy/sell — تداول وهمي\n"
        f"  /report — تقرير الأداء\n"
        f"  /help — هذه القائمة\n"
        f"  /about — معلومات رائد\n\n"
    )

    if plan in ("silver", "gold", "diamond"):
        msg += (
            f"{'─' * 28}\n"
            f"أوامر الباقة المتقدمة:\n"
            f"  /alert set [عملة] [سعر] — ضبط تنبيه\n"
            f"  /alerts — عرض تنبيهاتي\n"
            f"  /trade — تنفيذ صفقة حقيقية\n"
            f"  /addexchange — ربط بورصة\n"
            f"  /portfolio — المحفظة المتقدمة\n\n"
        )

    if plan in ("gold", "diamond"):
        msg += (
            f"{'─' * 28}\n"
            f"أوامر الأتمتة:\n"
            f"  /strategy — الاستراتيجيات\n"
            f"  /auto on/off — التداول الآلي\n"
            f"  /risk — إعدادات المخاطر\n\n"
        )

    if plan == "diamond":
        msg += (
            f"{'─' * 28}\n"
            f"أوامر ماسي الحصرية:\n"
            f"  /vip — لوحة VIP\n"
            f"  /institution — تقارير مؤسسية\n\n"
        )

    msg += (
        f"{'─' * 28}\n"
        f"{E['warn']} للترقية: /upgrade\n"
        f"{E['shield']} للأمان: /security"
    )

    await update.message.reply_text(msg + _sig())


# ══ /price ═══════════════════════════════════════════════════════════════════

@security_check
async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.add_to_memory(update.effective_user.id, "/price")
    args = context.args

    if not args:
        # عرض قائمة العملات الشائعة
        msg = (
            f"{E['chart']} الأسعار اللحظية\n\n"
            f"استخدام: /price [رمز العملة]\n\n"
            f"مثال:\n"
            f"  /price BTC\n"
            f"  /price ETH\n"
            f"  /price BNB\n\n"
            f"العملات الشائعة:\n"
            f"BTC | ETH | BNB | SOL | XRP\n"
            f"ADA | DOGE | AVAX | DOT | LINK"
        )
        await update.message.reply_text(msg + _sig())
        return

    symbol = args[0].upper().replace("USDT", "").replace("/", "") + "USDT"
    await db.remember_favorite_coin(update.effective_user.id, symbol)

    # سعر وهمي للاختبار (سيُستبدل بـ API حقيقي)
    mock_prices = {
        "BTCUSDT": 67420.50, "ETHUSDT": 3521.30, "BNBUSDT": 598.40,
        "SOLUSDT": 172.80, "XRPUSDT": 0.5821, "ADAUSDT": 0.4521,
        "DOGEUSDT": 0.1623, "AVAXUSDT": 38.92, "DOTUSDT": 7.43,
        "LINKUSDT": 14.87,
    }

    price = mock_prices.get(symbol)
    if not price:
        await update.message.reply_text(
            f"{E['error']} لم أجد العملة: {symbol}\n"
            f"تأكد من الرمز وحاول مرة أخرى.\n"
            f"مثال: /price BTC" + _sig()
        )
        return

    change_24h = 2.34  # وهمي
    change_emoji = E["up"] if change_24h >= 0 else E["down"]
    sign = "+" if change_24h >= 0 else ""

    msg = (
        f"{E['chart']} {symbol.replace('USDT', '')}/USDT\n\n"
        f"💲 السعر الحالي: ${price:,.4f}\n"
        f"{change_emoji} التغيير 24 ساعة: {sign}{change_24h}%\n\n"
        f"{E['virtual']} تداول وهمي: /virtual buy {symbol.replace('USDT','')} 100\n"
        f"{E['bell']} ضبط تنبيه: /alert set {symbol.replace('USDT','')} {price}"
    )

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{E['ok']} شراء وهمي", callback_data=f"vbuy_{symbol}"),
        InlineKeyboardButton(f"{E['bell']} تنبيه", callback_data=f"alert_{symbol}"),
    ]])
    await update.message.reply_text(msg + _sig(), reply_markup=buttons)


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

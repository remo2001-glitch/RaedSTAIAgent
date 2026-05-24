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
    user_id = update.effective_user.id
    await db.add_to_memory(user_id, "/wallet")

    wallet_data = await db.get_virtual_wallet(user_id)
    if not wallet_data:
        user = await db.get_or_create_user(user_id, "", "")
        wallet_data = user.get("virtual_wallet", {})

    wallet = VirtualWallet(wallet_data)
    report = wallet.report()

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{E['ok']} شراء وهمي", callback_data="vbuy_prompt"),
        InlineKeyboardButton(f"{E['money']} بيع وهمي", callback_data="vsell_prompt"),
    ],[
        InlineKeyboardButton(f"{E['trash']} إعادة ضبط", callback_data="vreset_confirm"),
        InlineKeyboardButton(f"{E['report']} التاريخ", callback_data="vhistory"),
    ]])
    await update.message.reply_text(report + _sig(), reply_markup=buttons)


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

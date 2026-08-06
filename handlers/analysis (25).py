"""
📡 رائد — handlers/analysis.py v2
أوامر: /news /onchain /regime /backtest /signal /liquidity /events /drift
       /analyze /quicksignal /upgrade /chart

الإصلاحات:
- import asyncio في الأعلى (ليس داخل الدوال)
- تحقق حقيقي من صلاحية الباقة في /analyze و /chart
- رسائل خطأ عربية كاملة
- RSI threshold مُصحَّح: 30/70 بدلاً من 35/65
- حماية من AttributeError في walls.buy_walls
- _clean_md مُحسَّن لا يُفسد أسماء العملات
- cmd_liquidity محمي من None في walls
- تحقق من price قبل حساب مستويات الدخول/الخروج
"""

import asyncio
import logging
from core.middleware import require_tier
from core.coins_list import is_symbol_allowed, get_tier_message
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from core.state_manager import state_manager as _sm
from core.middleware    import require_tier
from core.user_manager import user_manager as _um
from core.pair_resolver import resolve_symbol, build_pair_addon_lines, build_usdt_addendum
from telegram.constants import ParseMode

# X1: خريطة مرادفات الرموز الشاملة
SYMBOL_ALIASES = {
    "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
    "FACEBOOK": "META", "FB": "META",
    "MICROSOFT": "MSFT", "AMAZON": "AMZN",
    "APPLE": "AAPL", "TESLA": "TSLA",
    "NVIDIA": "NVDA", "COINBASE": "COIN",
    "SPACEX": "SPCX", "MICROSTRATEGY": "MSTR",
    "OPENAI": "OPENAI", "CHATGPT": "OPENAI",
    "GOLD": "XAU", "XAUUSD": "XAU",
    "BITCOIN": "BTC", "ETHEREUM": "ETH",
    "SOLANA": "SOL", "DOGECOIN": "DOGE",
    "BINANCE": "BNB", "RIPPLE": "XRP",
    "CARDANO": "ADA", "AVALANCHE": "AVAX",
    "TRON": "TRX", "CHAINLINK": "LINK",
}


def normalize_symbol_alias(raw: str) -> str:
    """X1: تطبيع الرموز عبر خريطة المرادفات + fuzzy match."""
    if not raw:
        return raw
    s = raw.upper().strip()
    if s in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[s]
    try:
        import difflib
        match = difflib.get_close_matches(s, list(SYMBOL_ALIASES.keys()), n=1, cutoff=0.8)
        if match:
            return SYMBOL_ALIASES[match[0]]
    except Exception:
        pass
    return s


# OKX_LIST (#2574/#2577/#2579): قائمة الأسهم المُرمَّزة المدرجة في OKX
# NET هو Cloudflare على OKX (CLOUDFLARE-USDT) — لكن NET أيضاً عملة مشفرة
_OKX_TOKENIZED_STOCKS = {
    "AAPL", "TSLA", "AMZN", "GOOGL", "GOOG", "MSFT", "META", "NVDA",
    "AMD", "INTC", "NFLX", "BABA", "JNJ", "V", "MA", "BRK", "JPM",
    "WMT", "DIS", "PYPL", "UBER", "LYFT", "SNAP", "TWTR", "SPOT",
    "COIN", "HOOD", "MSTR", "SQ", "PLTR", "NIO", "XPEV", "LI",
    "GME", "AMC", "BB", "NOK", "SPCX",
}

# SYM_WARN (#2570/#2572): رموز متضاربة — عملة مشفرة وسهم بنفس الرمز
_AMBIGUOUS_SYMBOLS = {
    "NET":  "Cloudflare (سهم) — قد يتعارض مع عملة NET المشفرة",
    "LINK": "Chainlink (عملة) — قد يتعارض مع رمز سهم",
    "BAT":  "Basic Attention Token — قد يتعارض",
    "KEY":  "عملة مشفرة — قد يتعارض مع سهم",
    "ONE":  "Harmony — قد يتعارض مع سهم",
}

logger = logging.getLogger(__name__)

# NYSE_TOKENS: الأصول المُرمَّزة المرتبطة بسوق NYSE/NASDAQ
_NYSE_TOKENS = {
    "XSPY", "SPY", "XSPCX", "SPCX", "XQQQ", "QQQ",
    "XXLE", "XLE", "XAAPL", "AAPL", "XGOOGL", "GOOGL",
    "XAMD", "AMD", "XMETA", "META", "XNVDA", "NVDA",
    "XTSLA", "TSLA", "XMSFT", "MSFT", "XAVGO", "AVGO",
}
# KSE_TOKENS: مرتبط بسوق كوريا (KSE)
_KSE_TOKENS = {"XSKHY", "SKHY"}


def _get_market_hours_warning(symbol: str, user_tz_offset: int = 3) -> str:
    """
    NYSE_hours_fix: تنبيه خارج ساعات التداول الرسمية بتوقيت المستخدم.
    user_tz_offset: فارق UTC بالساعات (KSA=3 افتراضياً)
    يعود بنص التنبيه أو "" إذا السوق مفتوح.
    """
    from datetime import datetime, timezone, timedelta
    _sym = symbol.upper()
    _now_utc = datetime.now(timezone.utc)
    _weekday = _now_utc.weekday()  # 0=Mon, 6=Sun
    _hour_utc = _now_utc.hour
    _min_utc = _now_utc.minute

    # حساب الوقت المحلي للمستخدم
    _local_dt = _now_utc + timedelta(hours=user_tz_offset)
    _local_time = _local_dt.strftime("%I:%M %p")
    _tz_name = f"UTC+{user_tz_offset}" if user_tz_offset >= 0 else f"UTC{user_tz_offset}"
    if user_tz_offset == 3: _tz_name = "KSA"
    elif user_tz_offset == 4: _tz_name = "UAE"
    elif user_tz_offset == 2: _tz_name = "EET"
    elif user_tz_offset == 0: _tz_name = "GMT"

    if _sym in _KSE_TOKENS:
        # KSE: 00:00-06:30 UTC (09:00-15:30 KST)
        _kse_open = (_weekday <= 4) and (
            (_hour_utc == 0) or
            (1 <= _hour_utc <= 5) or
            (_hour_utc == 6 and _min_utc < 30)
        )
        if not _kse_open:
            # ساعات KSE بتوقيت المستخدم
            _kse_open_local = datetime(2000,1,1,9,0) + timedelta(hours=user_tz_offset-9)
            _kse_close_local = datetime(2000,1,1,15,30) + timedelta(hours=user_tz_offset-9)
            return (
                f"⏰ *تنبيه:* {_sym} خارج ساعات سوق كوريا (KSE)\n"
                f"• الوقت لديك الآن: {_local_time} ({_tz_name})\n"
                f"• ساعات التداول الرسمية: "
                f"{_kse_open_local.strftime('%I:%M %p')}-{_kse_close_local.strftime('%I:%M %p')} ({_tz_name})\n"
                f"• التداول متاح على OKX لكن: سيولة منخفضة جداً + slippage مرتفع"
            )
        return ""

    if _sym in _NYSE_TOKENS:
        # NYSE/NASDAQ: 13:30-20:00 UTC
        _nyse_open = (_weekday <= 4) and (
            (_hour_utc == 13 and _min_utc >= 30) or
            (14 <= _hour_utc <= 19) or
            (_hour_utc == 20 and _min_utc == 0)
        )
        if not _nyse_open:
            # ساعات NYSE بتوقيت المستخدم
            _nyse_open_h = 13 + user_tz_offset
            _nyse_close_h = 20 + user_tz_offset
            # تطبيع
            _nyse_open_dt = datetime(2000,1,1,13,30) + timedelta(hours=user_tz_offset)
            _nyse_close_dt = datetime(2000,1,1,20,0) + timedelta(hours=user_tz_offset)
            _day_warn = " (يوم عمل)" if _weekday > 4 else ""
            return (
                f"⏰ *تنبيه:* {_sym} خارج ساعات سوق NYSE/NASDAQ{_day_warn}\n"
                f"• الوقت لديك الآن: {_local_time} ({_tz_name})\n"
                f"• ساعات التداول الرسمية: "
                f"{_nyse_open_dt.strftime('%I:%M %p')}-{_nyse_close_dt.strftime('%I:%M %p')} ({_tz_name})\n"
                f"• التداول متاح على OKX 24/7 لكن: سيولة أقل + slippage أعلى + أسعار قد تختلف عند الافتتاح"
            )
        return ""

    return ""


def _rsi_label(rsi: float) -> str:
    """تسمية RSI بمناطق احترافية."""
    if rsi >= 70:   return f"🔴 ذروة شراء ({rsi:.0f})"
    elif rsi >= 60: return f"🟠 قريب ذروة شراء ({rsi:.0f})"
    elif rsi >= 45: return f"⚪ محايد ({rsi:.0f})"
    elif rsi >= 35: return f"🟡 قريب ذروة بيع ({rsi:.0f})"
    elif rsi >= 25: return f"🟠 ذروة بيع ({rsi:.0f})"
    else:           return f"🔴 ذروة بيع شديدة ({rsi:.0f})"


def _market_contradiction(rsi: float, fear_greed: int, regime_desc: str) -> str:
    """تحليل تناقض المؤشرات."""
    is_bearish = "هابط" in str(regime_desc)
    oversold   = rsi < 35
    high_fear  = fear_greed < 30

    if high_fear and oversold and is_bearish:
        return "⏳ ذعر + ذروة بيع + هابط = انتظر تأكيد قبل الدخول"
    if high_fear and oversold:
        return "🟢 نمط تجميع: خوف شديد + ذروة بيع = فرصة محتملة"
    if fear_greed > 70 and rsi > 65:
        return "🔴 طمع + ذروة شراء = خطر انعكاس"
    return ""


def _fmt_price(price: float, quote: str = "USDT") -> str:
    """تنسيق السعر حسب حجمه ووحدة التسعير (تطوير #188).
    quote="USDT" (افتراضي): السلوك الأصلي تماماً — "$X"."""
    if quote == "USDT":
        if price <= 0:      return "$0"
        elif price >= 1000: return f"${price:,.2f}"
        elif price >= 1:    return f"${price:,.4f}"
        elif price >= 0.001:return f"${price:.6f}"
        elif price >= 1e-6: return f"${price:.8f}"
        else:               return f"${price:.10f}"
    # أزواج BTC/ETH المباشرة (تطوير #188) — بدون "$"، مع لاحقة الوحدة
    if price <= 0:      return f"0 {quote}"
    elif price >= 1:    return f"{price:,.4f} {quote}"
    elif price >= 0.001:return f"{price:.6f} {quote}"
    elif price >= 1e-6: return f"{price:.8f} {quote}"
    else:               return f"{price:.10f} {quote}"


def _price_decimals(value: float) -> int:
    """عدد المنازل العشرية حسب فئة _fmt_price لقيمة واحدة (إصلاح #195)."""
    v = abs(value)
    if v <= 0:       return 2
    elif v >= 1000:  return 2
    elif v >= 1:     return 4
    elif v >= 0.001: return 6
    elif v >= 1e-6:  return 8
    else:            return 10


def _fmt_price_pair(a: float, b: float, quote: str = "USDT") -> tuple:
    """إصلاح #195: تنسيق زوج قيم مرتبطة (دعم/مقاومة) بنفس عدد
    المنازل العشرية — يُستخدَم عدد منازل أصغرهما (الأكثر دقة)
    لكليهما، لتفادي تفاوت بصري عند عبور حد 0.001 (مثل
    0.00099812 [8 منازل] مقابل 0.001160 [6 منازل] → كلاهما 8)."""
    dec = max(_price_decimals(a), _price_decimals(b))
    if quote == "USDT":
        return f"${a:,.{dec}f}", f"${b:,.{dec}f}"
    return f"{a:,.{dec}f} {quote}", f"{b:,.{dec}f} {quote}"


# ════════════════════════════════════════════════════════════════
# #226/#227/#231/#232/#233 — helper موحَّد للرسائل
# يدعم كلا المسارَين: أمر مباشر (update.message) أو callback
# ════════════════════════════════════════════════════════════════
def _get_message(update, context=None):
    """يُعيد كائن Message الصحيح بصرف النظر عن نوع update.
    - أمر مباشر: update.message
    - callback (من _ask_market_type): context.user_data["_cb_message"]
    """
    if context is not None:
        cb_msg = context.user_data.get("_cb_message")
        if cb_msg is not None:
            return cb_msg
    if update.message is not None:
        return update.message
    if update.callback_query is not None:
        return update.callback_query.message
    return None


# ════════════════════════════════════════════════════════════════
# #221 — helper: سؤال نوع السوق (Spot / Futures) عند التحليل
# ════════════════════════════════════════════════════════════════
_GOLD_TIERS = ("gold", "diamond", "admin")

# ── عتبات الثقة حسب الباقة (Tier Confidence Thresholds) ──────────
# المبدأ: عتبة أقل = مصادر أقل = ضمانات أكثر
# المبدأ: أسهم مُرمَّزة تحتاج عتبة أعلى لمخاطرها الخاصة
_TIER_CONF = {
    #         wait  entry  max_pos  risk%  rsi_min  rsi_max
    "free":    (30,   45,    3,     0.5,    35,      65),
    "silver":  (35,   50,   10,     1.0,    25,      75),
    "gold":    (40,   55,   20,     1.5,    None,    None),
    "diamond": (40,   55,   35,     2.0,    None,    None),
    "admin":   (40,   55,   35,     2.0,    None,    None),
}
# عتبات الأسهم المُرمَّزة (أعلى من العملات العادية)
_STOCK_TIER_CONF = {
    "free":    None,   # محظور
    "silver":  None,   # محظور
    "gold":    (40, 60, 10, 1.0),   # wait, entry, max_pos, risk%
    "diamond": (40, 55, 20, 1.5),
    "admin":   (40, 55, 20, 1.5),
}

async def _ask_market_type(update, context, cmd: str, symbol: str, tier: str) -> bool:
    """تطوير #221: يعرض سؤال Spot/Futures لكل المستخدمين.
    - ذهبي+: يختار فعلاً (Spot أو Futures).
    - أقل من ذهبي: يرى السؤال ← إذا اختار Futures → رسالة ترقية (تحفيز).
    يُعيد True إذا أُرسِل السؤال (الأمر يجب أن يُعيد return وينتظر callback).
    يُعيد False إذا اختار المستخدم Spot تلقائياً (لا سؤال، تابع مباشرة)."""
    # بناء callback_data يحمل الأمر + الرمز
    def _cb(mtype):
        return f"mkttype_{cmd}_{mtype}_{symbol}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ سوق Spot", callback_data=_cb("spot")),
         InlineKeyboardButton("📈 سوق Futures", callback_data=_cb("futures"))],
    ])
    tier_note = "" if tier in _GOLD_TIERS else "\n_(Futures متاح للذهبي وأعلى — /upgrade)_"
    msg = _get_message(update, context)
    if msg:
        await msg.reply_text(
            f"📊 تحليل *{symbol}* — اختر نوع السوق:{tier_note}",
            parse_mode="Markdown", reply_markup=kb)
    return True


async def callback_market_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تطوير #221: معالجة اختيار Spot/Futures قبل التحليل.
    صيغة: mkttype_{cmd}_{mtype}_{symbol}
    cmd: signal | analyze | quicksignal
    mtype: spot | futures"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) < 4:
        await query.edit_message_text("❌ بيانات غير صالحة"); return

    cmd    = parts[1]   # signal | analyze | quicksignal
    mtype  = parts[2]   # spot | futures
    symbol = parts[3].upper()

    user_id = update.effective_user.id
    tier    = _sm.get_tier(user_id)

    if mtype == "futures" and tier not in _GOLD_TIERS:
        await query.edit_message_text(
            f"⬆️ *تحليل Futures — للذهبي وأعلى*\n\n"
            f"للترقية والحصول على:\n"
            f"• تحليل سوق Futures المنفصل\n"
            f"• بيانات Funding Rate / OI / Liquidations\n"
            f"• تخطيط أسبوعي/شهري Futures (ماسي+)\n\n"
            f"⬆️ /upgrade",
            parse_mode="Markdown")
        return

    # تخزين اختيار نوع السوق في user_data ليستخدمه الأمر
    context.user_data["_mkttype"] = mtype
    context.user_data["_mkttype_symbol"] = symbol
    # إصلاح #226/#227/#231/#232/#233: حفظ query.message حتى تستطيع
    # الأوامر استخدام _get_message() بدلاً من update.message (=None في callbacks)
    context.user_data["_cb_message"] = query.message

    label = "⚡ Spot" if mtype == "spot" else "📈 Futures"
    await query.edit_message_text(
        f"{label} — جاري تحليل *{symbol}*...",
        parse_mode="Markdown")

    # استدعاء الأمر المناسب مع symbol محدد
    context.args = [symbol]
    engine = context.bot_data.get("raed_engine")
    if not engine:
        await query.message.reply_text("⚠️ النظام لم يُهيَّأ بعد"); return

    try:
        if cmd == "signal":
            await cmd_signal(update, context)
        elif cmd == "analyze":
            await cmd_analyze(update, context)
        elif cmd == "quicksignal":
            await cmd_quicksignal(update, context)
    except Exception as e:
        logger.error(f"callback_market_type→{cmd}: {e}")
        await query.message.reply_text(f"❌ خطأ في التحليل. حاول لاحقاً")




def _calc_fibonacci(candles: list, lookback: int = 60, price_cap_mult: float = 3.0) -> dict:
    """
    إصلاح #326: Fibonacci dynamic يضمن أن السعر بين swing_low و swing_high.
    إصلاح M1 (#1787/#1801): cap swing_high بـ price_now × 3 لمنع القيم التاريخية المشوّهة.
    عند انهيار حاد (OPENAI/ANTHROPIC): swing_high تاريخي = $1,820 غير مقبول.
    """
    if not candles or len(candles) < 20:
        return {}
    try:
        price_now = float(candles[-1].get("close", 0))
        swing_high = swing_low = 0

        # M1: نبحث في نوافذ محدودة أولاً (تجنب البيانات التاريخية المشوّهة)
        for lb in [21, 30, 45, min(lookback, 90)]:
            recent = candles[-min(lb, len(candles)):]
            highs  = [float(c.get("high",  c.get("close", 0))) for c in recent]
            lows   = [float(c.get("low",   c.get("close", 0))) for c in recent]
            sh, sl = max(highs), min(lows)
            # M1: cap swing_high بـ price_now × price_cap_mult
            sh = min(sh, price_now * price_cap_mult)
            if sl < price_now < sh and sh > sl:
                swing_high, swing_low = sh, sl
                break

        # إذا لم نجد swing مناسباً — نبني حول السعر الحالي
        if swing_high == 0 or swing_low == 0 or swing_high <= swing_low:
            atr_est    = price_now * 0.05   # تقدير ATR 5%
            swing_high = price_now * 1.15
            swing_low  = price_now * 0.85

        diff = swing_high - swing_low
        if diff <= 0:
            return {}
        levels = {
            "0.0":   round(swing_low,  8),
            "0.236": round(swing_low  + diff * 0.236, 8),
            "0.382": round(swing_low  + diff * 0.382, 8),
            "0.500": round(swing_low  + diff * 0.500, 8),
            "0.618": round(swing_low  + diff * 0.618, 8),
            "0.786": round(swing_low  + diff * 0.786, 8),
            "1.0":   round(swing_high, 8),
            "1.272": round(swing_high + diff * 0.272, 8),
            "1.618": round(swing_high + diff * 0.618, 8),
        }
        price             = float(candles[-1].get("close", 0))
        below             = {k: v for k, v in levels.items() if v < price and float(k) <= 1.0}
        above             = {k: v for k, v in levels.items() if v > price and float(k) <= 1.0}
        nearest_support   = max(below.values()) if below else swing_low
        nearest_resistance= min(above.values()) if above else swing_high
        return {
            "levels": levels,
            "swing_high": swing_high, "swing_low": swing_low,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "price": price,
        }
    except Exception:
        return {}


def _fmt_fib_lines(fib: dict, price: float) -> list:
    """يُعيد قائمة سطور Fibonacci للدمج مع التقرير."""
    if not fib or not fib.get("levels"):
        return []
    lines = ["", "📐 *مستويات Fibonacci*"]
    ordered = sorted(
        [(float(k), v, k) for k, v in fib["levels"].items() if float(k) <= 1.0],
        reverse=True
    )
    # إيجاد المستوى الأقرب فقط — مستوى واحد
    closest_lbl = None
    closest_dst = float("inf")
    for _, val, label in ordered:
        d = abs(val - price) / max(price, 1e-9) * 100
        if d < closest_dst:
            closest_dst = d
            closest_lbl = label

    for _, val, label in ordered:
        icon    = "🟢" if val < price else "🔴"
        # "أنت هنا" فقط للمستوى الأقرب وبشرط < 2%
        is_here = (label == closest_lbl and closest_dst < 2.0)
        mark    = " ◀ أنت هنا" if is_here else ""
        lines.append(f"  {icon} {label:>5} — {_fmt_price(val)}{mark}")
    ns = fib.get("nearest_support", 0)
    nr = fib.get("nearest_resistance", 0)
    if ns > 0: lines.append(f"• أقرب دعم فيبو:    {_fmt_price(ns)}")
    if nr > 0: lines.append(f"• أقرب مقاومة فيبو: {_fmt_price(nr)}")
    return lines


def _calc_adx(candles: list, period: int = 14) -> float:
    """
    حساب ADX — إصلاح #243.
    Simple average يُعطي 87-94 بسبب H/L المحسوبة من CoinGecko.
    الحل: كشف artifact + ATR-based trend strength بديل.
    """
    if len(candles) < period + 2:
        return 0.0
    try:
        highs  = [float(c.get("high",  0)) for c in candles]
        lows   = [float(c.get("low",   0)) for c in candles]
        closes = [float(c.get("close", 0)) for c in candles]
        pdms, ndms, trs = [], [], []
        for i in range(1, len(candles)):
            h, l, pc = highs[i], lows[i], closes[i-1]
            h_diff = h - highs[i-1]
            l_diff = lows[i-1] - l
            pdms.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
            ndms.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))

        # كشف artifact: H/L محسوبة تُعطي +DM أو -DM = 0 دائماً
        total      = len(pdms) or 1
        pdm_nz     = sum(1 for x in pdms if x > 0)
        ndm_nz     = sum(1 for x in ndms if x > 0)
        if pdm_nz / total < 0.05 or ndm_nz / total < 0.05:
            # بيانات أحادية — trend strength من EMA
            p      = closes[-1] if closes[-1] > 0 else 1
            avg_tr = sum(trs[-period:]) / min(period, len(trs)) if trs else 0
            atr_p  = avg_tr / p * 100
            ema_f  = sum(closes[-period:])      / period if len(closes) >= period    else closes[-1]
            ema_s  = sum(closes[-period*2:])    / (period*2) if len(closes) >= period*2 else ema_f
            trend_s = abs(ema_f - ema_s) / max(ema_s, 1) * 100
            return round(min(15 + trend_s * 8 + atr_p * 1.5, 65.0), 1)

        # Wilder's smoothing الصحيح
        def _wilder(data):
            val = sum(data[:period])
            res = [val]
            for v in data[period:]:
                val = val - val / period + v
                res.append(val)
            return res

        atr_s = _wilder(trs);  pdm_s = _wilder(pdms);  ndm_s = _wilder(ndms)
        dx_list = []
        for i in range(len(atr_s)):
            av = atr_s[i]
            if av <= 0: continue
            di_p = pdm_s[i] / av * 100
            di_m = ndm_s[i] / av * 100
            d    = di_p + di_m
            if d > 0:
                dx_list.append(abs(di_p - di_m) / d * 100)
        if not dx_list:
            return 0.0
        adx = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx = (adx * (period - 1) + dx) / period
        return round(min(adx, 75.0), 1)
    except Exception:
        return 0.0


def _build_professional_block(
    symbol: str, price: float, signal, regime,
    candles: list, rsi: float, atr_pct: float, fib: dict,
    tech_extra: dict = None,
    tier: str = "silver",        # باقة المستخدم
    is_stock: bool = False,       # هل أصل مُرمَّز؟
) -> str:
    """
    بناء بلوك الإشارة الاحترافية — ملاحظة #33
    يُعرض في /signal و /analyze فقط
    tech_extra: معلومات إضافية (مثل is_perp_asset لإخفاء TVL/Whale)
    """
    _extra = tech_extra or {}
    conf      = signal.confidence
    # إصلاح #948: rsi كـ int مبكراً — مصدر واحد للحقيقة
    rsi = int(round(rsi))  # ضمان int دائماً
    direction = signal.direction
    tech      = {**(getattr(signal, "technicals", {}) or {}), **_extra}  # دمج tech_extra
    # إصلاح #809/#61(ثانوي): _vol_ratio من technicals['vol_ratio'] مباشرة —
    # getattr(signal,'vol_ratio',...) كان يُعيد دائماً 1.0 الافتراضي لأن
    # SignalResult لا يملك حقل vol_ratio على المستوى الأعلى، فكان السطر
    # السابق (#260 لاحقاً) يفشل في رصد vol_ratio<0.8 قبل إعادة التعيين
    # اللاحقة في الكود — الآن مصدر واحد للحقيقة من البداية
    try:
        _vol_ratio = float(tech.get("vol_ratio", 1.0) or 1.0)
    except Exception:
        _vol_ratio = 1.0
    adx       = _calc_adx(candles) or float(tech.get("adx", 0) or 0)
    macd_hist = float(tech.get("macd_hist", 0) or 0)
    is_bear   = "هابط" in regime.description_ar
    closes    = [float(c.get("close", 0)) for c in candles if c.get("close")]
    ema50_val = sum(closes[-50:]) / 50 if len(closes) >= 50 else price * 1.05
    atr_dec   = atr_pct / 100
    # FIN2: ATR الفعلي بدون cap للمنطق المالي
    _atr_raw_pct = _calc_atr_raw(candles) if candles else atr_pct
    _atr_raw_dec = _atr_raw_pct / 100
    # هل البيانات مشوهة؟ (ATR المعروض > 20% = علامة تشوه)
    _fin_corrupted = (atr_pct >= 20.0)

    # القرار — يعكس السيناريو الفعلي
    _scenario = tech.get("scenario", "")
    if conf >= 0.65 and direction == "long" and _scenario == "counter_trend_bounce":
        decision = "⚡ COUNTER-TREND BOUNCE — ارتداد مؤقت"
    elif conf >= 0.65 and direction == "long" and _scenario == "trend_reversal":
        decision = "🔄 REVERSAL — انعكاس اتجاه"
    elif conf >= 0.65 and direction == "long":
        decision = "✅ BUY — شراء"
    elif conf >= 0.65 and direction == "short":
        decision = "✅ SHORT — بيع"
    else:
        decision = "⚪ ANTICIPATE — انتظار"

    # الأسباب
    reasons = []
    # T2_fix: عتبة الدخول من الباقة مباشرة
    _t_entry_pct = _TIER_CONF.get(tier, _TIER_CONF["silver"])[1]
    # للأسهم المُرمَّزة: عتبة خاصة
    if is_stock:
        _sc_t = _STOCK_TIER_CONF.get(tier)
        if _sc_t:
            _t_entry_pct = _sc_t[1]
        else:
            _t_entry_pct = 100  # محظور
    _threshold = _t_entry_pct / 100.0
    # conf_reason_fix: حفظ conf الأصلي للعرض الصحيح بعد الرفع
    _conf_for_reason = conf  # سيُحدَّث لاحقاً إذا رُفعت الثقة
    if conf < _threshold:
        reasons.append(f"• الثقة {conf:.0%} أقل من الحد {_threshold:.0%}")
    elif _scenario == "counter_trend_bounce" and _vol_ratio < 0.8:
        reasons.append(
            f"• الثقة {conf:.0%} تجاوزت {_threshold:.0%} — لكن الحجم {_vol_ratio:.1f}x "
            f"أقل من 0.8x المطلوب لتأكيد الارتداد")
    # إصلاح #86/#130: ADX فقط عند الخطورة القصوى
    if adx > 45:
        reasons.append(f"• ADX = {adx:.0f} → اتجاه قوي جداً — خطر الدخول مرتفع")
    if macd_hist < 0:
        reasons.append("• MACD سالب (sellers مسيطرون)")
    elif macd_hist > 0:
        reasons.append("• MACD موجب (زخم شراء)")
    if rsi < 30:
        reasons.append(f"• RSI = {int(rsi)} → ذروة بيع (فرصة انتعاش)")
    elif rsi > 70:
        reasons.append(f"• RSI = {int(rsi)} → ذروة شراء (خطر تصحيح)")
    else:
        reasons.append(f"• RSI = {rsi:.0f}")
    ns = fib.get("nearest_support", 0)
    nr = fib.get("nearest_resistance", 0)
    if ns > 0:
        dist = abs(price - ns) / max(price, 1e-9) * 100
        reasons.append(f"• الدعم {_fmt_price(ns)} على بُعد {dist:.1f}%")

    # شروط الدخول (للانتظار فقط)
    entry_conds = []
    if conf < 0.65 or direction == "neutral":
        # M#117: شرط RSI بناءً على القيمة الحالية
        # إصلاح #470/#481: الشرط الأول يستخدم أقرب مستوى حقيقي
        _near_res_c = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _ema20_raw  = sum([float(c.get("close",0)) for c in candles[-20:]]) / 20 if len(candles) >= 20 else price * 1.03
        # DD2 (#1901/#1909): عند بيانات مشوهة → cap ema20 بـ price × 1.3
        _ema20_val  = min(_ema20_raw, price * 1.3) if _ema20_raw > price * 1.3 else _ema20_raw
        # DD2: توسيع نطاق القبول من 15% → 50% لاستيعاب Fibonacci المُقيَّد
        _close_target = _near_res_c if (_near_res_c and price < _near_res_c < price * 1.50) else _ema20_val

        if rsi < 40:
            _rsi_cond = f"1. RSI يتجاوز 30 صعوداً + إغلاق فوق {_fmt_price(_close_target)}"
        elif rsi < 55:
            _rsi_cond = f"1. RSI يتجاوز 55 + إغلاق فوق {_fmt_price(_close_target)}"
        else:
            _rsi_cond = f"1. انتظر تصحيح RSI تحت 60 ثم ارتداد"
        # إصلاح #378: شرط دخول يستخدم مقاومة فيبو القريبة بدلاً من EMA50 البعيد
        _fib_res    = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _target_lbl = (f"مقاومة فيبو ({_fmt_price(_fib_res)})"
                       if _fib_res and _fib_res > price
                       else f"EMA50 ({_fmt_price(ema50_val)})")
        # إصلاح #495: شرط 3 مختلف عن شرط 1
        _fib_res_cond = fib.get("nearest_resistance", 0) if isinstance(fib, dict) else 0
        _fib_sup_cond = fib.get("nearest_support",    0) if isinstance(fib, dict) else 0

        if _fib_res_cond and _fib_res_cond > price:
            # شرط 3 = Reclaim الدعم (مستوى أدنى من المقاومة)
            _sup_display = _fmt_price(_fib_sup_cond) if _fib_sup_cond else _fmt_price(price * 0.98)
            _cond3 = f"3. ثبات واستقرار فوق {_sup_display} (الدعم القريب)"  # T29_fix
        else:
            _cond3 = "3. ظهور شمعة ارتداد قوية (Bullish Engulfing أو Hammer)"
        entry_conds = [
            _rsi_cond,
            "2. الثقة الإجمالية ≥ 60%",
            _cond3,
        ]
        if ns > 0:
            # إصلاح التكرار: Demand Zone = مستوى أعمق من الدعم القريب
            _demand_level = ns * 0.98  # 2% أعمق من الدعم
            entry_conds.append(f"4. وصول Demand Zone {_fmt_price(_demand_level)} (دعم أعمق)")
        if adx > 40:
            entry_conds.append("5. MACD إيجابي أو تقاطع صاعد")

    # إصلاح #325: R/R ديناميكي — ذروة البيع تُعطي هدفاً أوسع
    # منطق مالي: RSI=13 تاريخياً يسبق ارتداداً 10-20%
    # فالهدف 4×ATR منطقي (ليس 1.8×ATR كالمعتاد)
    # FIN2b: استخدام ATR الفعلي للمنطق المالي (بدلاً من ATR المُقيَّد)
    _eff_atr = _atr_raw_dec if _fin_corrupted else atr_dec

    if direction == "short" and conf >= 0.65:
        pro_entry = price * (1 + _eff_atr * 0.2)
        pro_tp    = price * (1 - _eff_atr * 2.0)
        pro_sl    = price * (1 + _eff_atr * 1.2)
        pro_dir   = "Short"
    else:
        pro_entry = ns if ns > 0 and ns < price * 0.99 else price * (1 - _eff_atr * 0.4)
        # FIN2b: ضبط الهدف حسب RSI مع ATR واقعي
        if rsi <= 15:
            _tp_mult, _sl_mult = 4.0, 0.8   # قاع شديد → هدف كبير، وقف ضيق
        elif rsi <= 25:
            _tp_mult, _sl_mult = 3.0, 1.0
        elif rsi <= 35:
            _tp_mult, _sl_mult = 2.5, 1.0
        else:
            _tp_mult, _sl_mult = 1.8, 1.2   # افتراضي
        pro_tp  = price * (1 + _eff_atr * _tp_mult)
        pro_sl  = pro_entry * (1 - _eff_atr * _sl_mult)
        pro_dir = "Long"

    # SL_Fib_fix: استخدام أقرب مستوى Fibonacci كـ SL إذا كان أفضل (أقرب وأمثل)
    try:
        if fib and isinstance(fib, dict):
            _fib_levels = [
                fib.get("f0", 0), fib.get("f236", 0),
                fib.get("f382", 0), fib.get("f500", 0),
                fib.get("f618", 0),
            ]
            # أقرب مستوى فيبو تحت السعر (للـ Long) كـ SL محتمل
            _fib_sl_candidates = [
                f for f in _fib_levels
                if f > 0 and f < price * 0.99  # تحت السعر
                and f > pro_sl * 0.95           # ليس أبعد من ATR SL بـ 5%+
            ]
            if _fib_sl_candidates:
                _fib_sl_best = max(_fib_sl_candidates)  # الأقرب للسعر
                # استخدم Fibonacci SL إذا كان أفضل (أقل خسارة) من ATR SL
                if _fib_sl_best > pro_sl:
                    pro_sl = _fib_sl_best
    except Exception:
        pass

    rr = abs(pro_tp - pro_entry) / max(abs(pro_sl - pro_entry), 1e-9)

    # إضافة تحذير السيناريو
    _scenario_warn = signal.technicals.get("scenario_warn", "") if hasattr(signal, "technicals") else ""
    _scenario_ar   = signal.technicals.get("scenario_ar",   "") if hasattr(signal, "technicals") else ""
    # إصلاح #95 (توحيد القاعدة): SL% من السعر الحالي
    sl_pct  = abs(price - pro_sl) / max(price, 1e-9) * 100
    # T6_fix: cap SL للأصول المُرمَّزة X-prefix (سيولة منخفضة)
    # is_stock parameter أو فحص القاموس لتحديد الأصل المُرمَّز
    _is_x_asset = (
        is_stock or  # مُمرَّر من cmd_signal/cmd_analyze
        symbol.upper().startswith("X") or  # XSPCX
        symbol.upper() in {"SPCX","AMZN","AAPL","GOOGL","META","AMD",
                           "NFLX","SPY","ORCL","AVGO","MSFT","COIN","NVDA"}
    )
    if _is_x_asset and sl_pct > 10.0:
        # إعادة حساب pro_sl بحد أقصى 10%
        pro_sl = price * 0.90
        sl_pct = 10.0
    elif not _is_x_asset and sl_pct > 15.0:
        # حد أقصى 15% للعملات العادية
        pro_sl = price * 0.85
        sl_pct = 15.0
    tp_pct  = abs(pro_tp - pro_entry) / max(pro_entry, 1e-9) * 100
    hold    = 3 if adx > 40 else 5

    # إضافة تحذير السيناريو في decision
    # إصلاح #414: سطر واحد يجمع السيناريو والتحذير
    scenario_block = []
    if _scenario_warn:
        scenario_block.append(f"📊 *السيناريو:* {_scenario_warn}")
    elif _scenario_ar:
        scenario_block.append(f"📊 *السيناريو:* {_scenario_ar}")

    parts = [f"*{decision}*", ""]
    if scenario_block:
        parts.extend(scenario_block)
        parts.append("")
    if reasons:
        parts.append("*✅ الأسباب:*")
        parts.extend(reasons)
        parts.append("")
    if entry_conds:
        parts.append("*⏳ متى تدخل؟*")
        parts.extend(entry_conds)
        parts.append("")
    # حساب الحجم بناءً على السيناريو — التمييز الثلاثي
    _scenario = tech.get("scenario", "")
    if conf >= 0.65 and not is_bear:
        # سوق صاعد — دخول كامل
        vol_pct    = 50
        vol_reason = "ثقة جيدة + سوق محايد/صاعد"
    elif conf >= 0.65 and is_bear and _scenario == "counter_trend_bounce":
        # ارتداد مؤقت عكس الاتجاه — حجم مقيّد
        vol_pct    = 12
        vol_reason = "⚡ counter-trend scalp — 12% MAX (اتجاه هابط)"
    elif conf >= 0.65 and is_bear and _scenario == "trend_reversal":
        # انعكاس حقيقي مؤكد
        vol_pct    = 30
        vol_reason = "🔄 انعكاس اتجاه مؤكد — دخول تدريجي"
    elif conf >= 0.65 and is_bear:
        # سوق هابط بدون سيناريو محدد — حذر
        vol_pct    = 20
        vol_reason = "سوق هابط — حجم محدود للحماية"
    elif conf >= 0.50:
        vol_pct    = 15
        vol_reason = "ثقة متوسطة — تقليل للحماية"
    else:
        vol_pct    = 10
        vol_reason = "ثقة منخفضة — حد أدنى"

    # حجم فعلي من المحفظة الافتراضية
    portfolio_est = 10000  # سيُحسب من المحفظة الحقيقية

    # (#438) خيار المحترف القديم حُذف — الهيكل الجديد يُغني عنه
    # ══════════════════════════════════════════════════════════
    # الهيكل الاحترافي الكامل (المرحلة 1)
    # ══════════════════════════════════════════════════════════

    # ── المؤشرات الأساسية ──────────────────────────────────
    _mp       = getattr(regime, "market_phase", "") or ""
    _mp_ar    = _get_market_phase_ar(_mp) if _mp else ""
    _rsi_div  = tech.get("rsi_div",   "none")
    _vol_prof = tech.get("vol_profile", "normal")
    # إصلاح #788: حجم ضعيف < 0.8x يُصنَّف دائماً no_demand
    if _vol_ratio < 0.8 and _vol_prof not in ("climax_selling", "climax_buying"):
        _vol_prof = "no_demand"
    _bb_pos_raw = tech.get("bb_pos", None)
    # إصلاح #787/#850: RSI يُعدِّل BB عند الإجماع
    if _bb_pos_raw is None:
        _bb_pos_v = 0.5  # default
    else:
        _bb_pos_v = float(_bb_pos_raw)
    # RSI في ذروة بيع/شراء يُعدِّل BB إذا كان متعارضاً
    # إصلاح #1022: توسيع النطاق — RSI<25 يعني ذروة بيع → BB يجب أن يكون منخفض
    if rsi < 25 and _bb_pos_v > 0.3:
        _bb_pos_v = min(_bb_pos_v, 0.15)
    elif rsi < 30 and _bb_pos_v > 0.5:
        _bb_pos_v = min(_bb_pos_v, 0.30)
    elif rsi > 75 and _bb_pos_v < 0.7:
        _bb_pos_v = max(_bb_pos_v, 0.85)
    elif rsi > 70 and _bb_pos_v < 0.5:
        _bb_pos_v = max(_bb_pos_v, 0.65)
    _scenario = tech.get("scenario",   "")
    _conf_flags = tech.get("conf_flags", [])
    _atr_val  = tech.get("atr_value",  0)

    # ── Derivatives Data ────────────────────────────────────
    _oi    = tech.get("oi_data",    {}) or {}
    _fund  = tech.get("fund_data",  {}) or {}
    _whale = tech.get("whale_data", {}) or {}

    # Funding Rate signal
    _fund_pct = float(_fund.get("rate_pct", 0) or 0)
    # إصلاح #518: signal يعكس الحالة الحقيقية
    if _fund_pct < -0.02:
        _fund_sig = "🟢 سالب جداً (ضغط على Shorts — فرصة Longs)"
    elif _fund_pct < -0.005:
        _fund_sig = "🟢 سالب (فرصة Long)"
    elif _fund_pct > 0.02:
        _fund_sig = "🔴 مرتفع جداً (ضغط على Longs)"
    elif _fund_pct > 0.005:
        _fund_sig = "🟡 إيجابي (محايد)"
    else:
        _fund_sig = _fund.get("signal", "⚪ محايد")
    # Open Interest signal
    _oi_chg   = float(_oi.get("oi_change_pct", 0) or 0)
    _oi_sig   = _oi.get("signal", "")
    # Whale signal
    _whale_sig = _whale.get("signal", "")
    _whale_ratio = float(_whale.get("ratio", 0) or 0)

    # Funding Rate يضيف Confirmation Flag
    if _fund_pct < -0.01:
        _conf_flags = list(_conf_flags) + ["Funding Rate سالب (فرصة Longs) ✓"]
    if _oi_chg < -5:
        _conf_flags = list(_conf_flags) + ["OI انخفض (تصفية Shorts) ✓"]
    # T27_fix: Whale > 1.3 = صاعد ✅ | < 0.7 = هابط ⚠️
    if _whale_ratio > 0:
        if _whale_ratio >= 1.3:
            _conf_flags = list(_conf_flags) + [f"Whale Ratio {_whale_ratio:.2f} — أغلبية Long ✓"]
        elif _whale_ratio <= 0.7:
            pass  # هابط — لا نُضيفه كـ confirmation
    # إصلاح #619/#651: Volume Spike يُضاف كـ Confirmation Flag
    if _vol_ratio >= 1.5:
        _conf_flags = list(_conf_flags) + [f"Volume Spike {_vol_ratio:.1f}x ✓"]
    # إصلاح #540: RSI extreme + BB
    if rsi < 20:
        _conf_flags = list(_conf_flags) + [f"RSI ذروة بيع ({rsi:.0f}) ✓"]
    elif rsi > 80:
        _conf_flags = list(_conf_flags) + [f"RSI ذروة شراء ({rsi:.0f}) ✓"]
    if _bb_pos_v < 0.15:
        _conf_flags = list(_conf_flags) + ["BB تحت الحد السفلي ✓"]
    elif _bb_pos_v > 0.85:
        _conf_flags = list(_conf_flags) + ["BB فوق الحد العلوي ✓"]

    _flags_found = len(_conf_flags)
    _confirmed   = _flags_found >= 2

    # conf_boost_pro: تطبيق الرفع بعد بناء reasons لتحديث نص الثقة
    _boost_pro = _flags_found * 3
    _rsi_div_pro = tech.get("rsi_divergence", "none") if isinstance(tech, dict) else "none"
    if rsi > 80 and direction == "long": _boost_pro = max(0, _boost_pro - 3)
    if rsi < 20 and direction == "short": _boost_pro = max(0, _boost_pro - 3)
    if _rsi_div_pro == "bearish" and direction == "long": _boost_pro = 0
    if _rsi_div_pro == "bullish" and direction == "short": _boost_pro = 0
    if _boost_pro > 0:
        _conf_before = conf
        conf = min(conf + _boost_pro / 100, 0.85)
        # conf_reason_fix_pro: تحديث نص سبب الثقة بعد الرفع
        # الحل: نُحدَّث أي سبب يحتوي "أقل من الحد" بالثقة الجديدة
        # بدون شرط النسبة المئوية (يُسبب تعارض تقريب Python)
        _new_reasons_pro = []
        for _r in reasons:
            if "أقل من الحد" in _r:
                _new_reasons_pro.append(
                    f"• الثقة {conf:.0%} أقل من الحد {_threshold:.0%}"
                    if conf < _threshold else
                    f"• الثقة {conf:.0%} (مرفوعة من التأكيدات +{_boost_pro}%)"
                )
            else:
                _new_reasons_pro.append(_r)
        reasons = _new_reasons_pro

    # conf_boost_fix: مُدمَج مع conf_boost_pro أعلاه لتجنب double boost
    _boost_pct = _boost_pro  # للتوافق مع الكود اللاحق
    _conf_raw = conf  # الثقة بعد الرفع

    # ── Confidence Score مفصّل ──────────────────────────────
    _tech_score    = round(tech.get("score", 0.5) * 100)
    _oc_score      = round(getattr(signal, "onchain_score", 0.5) * 100)
    _sent_score    = round(getattr(signal, "news_score",    0.5) * 100)
    _macro_score   = round(getattr(signal, "macro_score",   0.5) * 100)
    _conf_score    = round(conf * 100)

    # ── القرار بناءً على Confidence + Tier (نظام الباقات) ───────
    # tier2 يُعرَّف لاحقاً — نستخدم _sm مباشرة
    _tier_sig     = tier        # مُمرَّر من cmd_signal/cmd_analyze
    _is_stock_sig = is_stock    # مُمرَّر من cmd_signal/cmd_analyze

    # عتبات الباقة الحالية
    _tc = _TIER_CONF.get(_tier_sig, _TIER_CONF["silver"])
    _t_wait, _t_entry, _t_max_pos, _t_risk, _t_rsi_min, _t_rsi_max = _tc

    # للأسهم المُرمَّزة: تحقق من صلاحية الباقة
    if _is_stock_sig:
        _sc = _STOCK_TIER_CONF.get(_tier_sig)
        if _sc is None:
            # محظور للمجاني والفضي
            _decision_label = "[BLOCKED] — الأسهم المُرمَّزة للذهبي+"
            _pos_size_rule  = "0% — ترقّ لباقة ذهبي للوصول"
            _t_entry = 999  # يمنع الدخول
        else:
            # عتبة خاصة بالأسهم
            _t_wait, _t_entry, _t_max_pos, _t_risk = _sc

    # RSI filter للمجاني والفضي
    _rsi_blocked = False
    if _t_rsi_min is not None and _t_rsi_max is not None:
        if rsi < _t_rsi_min or rsi > _t_rsi_max:
            _rsi_blocked = True

    # SL للحساب
    _sl_base = abs(price - (pro_sl if pro_sl > 0 else price * (1 - _eff_atr * 1.2))) / max(price, 1e-9) * 100

    # ── مصفوفة القرار ──
    if _rsi_blocked:
        _decision_label = f"[WAIT] — RSI خارج نطاق الباقة ({rsi:.0f})"
        _pos_size_rule  = "0% — RSI يجب أن يكون بين {_t_rsi_min}% و{_t_rsi_max}%"
        if hasattr(regime, 'action'):
            try: object.__setattr__(regime, 'action', 'avoid')
            except: pass
    elif _conf_score < _t_wait:
        _decision_label = "[WAIT] — لا صفقة نشطة"
        _pos_size_rule  = "0% — انتظر مؤشرات أقوى"
        if hasattr(regime, 'action') and regime.action == "trade_normal":
            try:
                object.__setattr__(regime, 'action', 'avoid')
                if hasattr(regime, 'metrics') and isinstance(regime.metrics, dict):
                    regime.metrics['action_basis'] = f" (الثقة {_conf_score}%<{_t_wait}%)"
            except Exception:
                pass
    elif _conf_score < _t_entry:
        _decision_label = f"[LOW] — حجم {max(1,_t_max_pos//4)}–{_t_max_pos//2}% فقط"
        _pos_low  = min(_t_max_pos // 2, round(_t_risk * 0.5 / max(_sl_base / 100, 0.01) * 100, 1))
        _pos_size_rule = f"{max(1, min(_t_max_pos//2, round(_pos_low)))}% — ثقة منخفضة"
        if hasattr(regime, 'action') and regime.action == "trade_normal":
            try:
                object.__setattr__(regime, 'action', 'reduce_size')
                if hasattr(regime, 'metrics') and isinstance(regime.metrics, dict):
                    regime.metrics['action_basis'] = f" (الثقة {_conf_score}%<{_t_entry}%)"
            except Exception:
                pass
    elif _conf_score < 75:
        # #3237_fix: حجم NORMAL = نصف max بحد أقصى 20% للماسي
        _normal_cap = min(_t_max_pos, 20)  # cap عند 20% في NORMAL
        _decision_label = f"[NORMAL] — حجم {_normal_cap//2}–{_normal_cap}%"
        _pos_norm  = min(float(_normal_cap), round(_t_risk / max(_sl_base / 100, 0.01) * 100, 1))
        _pos_size_rule = f"{max(_normal_cap//2, min(_normal_cap, round(_pos_norm)))}% — ثقة متوسطة"
    else:
        _decision_label = f"[HIGH] — حجم {_t_max_pos}%"
        _pos_high  = min(float(_t_max_pos), round(_t_risk * 1.3 / max(_sl_base / 100, 0.01) * 100, 1))
        _pos_size_rule = f"{min(_t_max_pos, round(_pos_high))}% — ثقة عالية"

    # تحذير خاص للمجاني عند الأسهم المُرمَّزة
    if _is_stock_sig and _STOCK_TIER_CONF.get(_tier_sig) is None:
        pass  # _decision_label مُعيَّن أعلاه

    # تحذير إشارة تقنية فقط للمجاني (غير أسهم)
    _free_warning = ""
    if _tier_sig == "free" and not _is_stock_sig and not _rsi_blocked and _conf_score >= _t_entry:
        _free_warning = "\n\n⚠️ *إشارة تقنية فقط* — تحقق من الأخبار قبل الدخول"

    # إصلاح #730/#736: WAIT عند volume ضعيف في counter-trend scalp
    if _scenario == "counter_trend_bounce" and _vol_ratio < 0.8:
        _decision_label = "[WAIT] — حجم ضعيف للـ scalp"
        _pos_size_rule  = "0% — انتظر تأكيد الحجم ≥ 0.8x"

    # إصلاح #103: عند "🟢 شراء [HIGH/NORMAL]" في ارتداد مؤقت (الحجم مؤكَّد)،
    # "الإجراء: ⏳ انتظر — RSI ذروة بيع" يتعارض ظاهرياً مع التوصية أعلاه —
    # الارتداد الموصى به *هو* الانعكاس المُنتظر، فنُعدِّل النص ليعكس ذلك
    if (_scenario == "counter_trend_bounce" and direction == "long"
            and not _decision_label.startswith("[WAIT]")
            and hasattr(regime, 'action') and regime.action == "wait_reversal"):
        try: object.__setattr__(regime, 'action', 'bounce_entry_confirmed')
        except: pass

    # تحديث vol_pct من _decision_label (حسب الباقة)
    if _conf_score < _t_wait or _rsi_blocked or (
            _is_stock_sig and _STOCK_TIER_CONF.get(_tier_sig) is None):
        vol_pct = 0
    elif _conf_score < _t_entry:
        vol_pct = max(1, _t_max_pos // 4)
    elif _conf_score < 75:
        vol_pct = _t_max_pos // 2
    else:
        vol_pct = _t_max_pos

    # ── Entry Aggressive + Conservative ──────────────────────
    nr_fib = fib.get("nearest_resistance", price * 1.05) if fib else price * 1.05
    f236   = fib.get("0.236", price * 1.06) if fib else price * 1.06
    f382   = fib.get("0.382", price * 1.10) if fib else price * 1.10
    # إصلاح #439: Aggressive = عند الدعم (أدنى) | Conservative = بعد تأكيد (أعلى)
    # إصلاح #652: Entry Aggressive دائماً < Entry Conservative
    # Aggressive = أدنى سعر نستهدف (عند الدعم)
    # Conservative = بعد تأكيد الارتداد (أقرب للسعر الحالي)
    # إصلاح #133: نطاق أوسع (price*1.01 بدل price) يمنع قفزة entry_agg
    # بنسبة ~8% عند تذبذب السعر حول ns بفارق <1% (تقلب طبيعي بين استدعاءين)
    if ns > 0 and price * 0.94 < ns < price * 1.01:
        entry_agg = min(ns, price)  # عند دعم فيبو (أو أقرب إليه)
    else:
        entry_agg = price * (1 - atr_dec * 0.8)  # أدنى من السعر الحالي
    # Conservative: بين entry_agg والسعر الحالي
    _cons_offset = (price - entry_agg) * 0.4  # 40% من المسافة
    entry_cons = entry_agg + _cons_offset
    entry_cons = min(entry_cons, price * 0.998)  # لا يتجاوز السعر الحالي
    # ضمان: Aggressive < Conservative
    if entry_agg >= entry_cons:
        entry_cons = entry_agg * 1.005

    # ── TP متدرج حسب نوع الصفقة ──────────────────────────────
    if _scenario == "counter_trend_bounce":
        # إصلاح #622/#729: TP من الحساب فقط — لا Fibonacci في counter-trend
        _tp1_pct = min(0.06, atr_dec * 1.2)   # max 6% للـ scalp
        _tp2_pct = min(0.09, atr_dec * 2.0)   # max 9%
        tp1_v = price * (1 + _tp1_pct)
        tp2_v = price * (1 + _tp2_pct)
        # ضمان: TP2 > TP1 بفارق لا يقل عن 2%
        tp2_v = max(tp2_v, tp1_v * 1.02)
        tp3_v = None
        _time_exit = "3 أيام"
        _trade_dur = "ساعات — 3 أيام (Scalp)"
    elif _scenario == "trend_reversal":
        tp1_v = nr_fib; tp2_v = f236; tp3_v = f382
        _time_exit = "14 يوم"
        _trade_dur = "1–3 أسابيع (Swing)"
    else:
        # FIN5: mean_reversion وbullish — TP بـ ATR الفعلي
        _tp1_mult = 1.2 if _scenario in ("bullish_continuation",) else 0.8
        _tp2_mult = 2.0 if _scenario in ("bullish_continuation",) else 1.5
        # FIN5: استخدام _atr_raw_dec للأصول الطبيعية لأهداف أكثر دقة
        _eff_tp_atr = _atr_raw_dec if not _fin_corrupted else atr_dec
        tp1_v = price * (1 + min(_eff_tp_atr * _tp1_mult, 0.15))  # max 15%
        tp2_v = price * (1 + min(_eff_tp_atr * _tp2_mult, 0.25))  # max 25%
        tp3_v = None
        _time_exit = "5 أيام"
        _trade_dur = "2–5 أيام"

    # FIN5b: R/R من pro_sl الفعلي المُحسَّن
    _sl_price = pro_sl if (pro_sl > 0 and pro_sl < price) else price * (1 - _atr_raw_dec * 1.0)
    _risk   = max(price - _sl_price, 1e-9)
    _reward = max(tp1_v - price, 1e-9)
    rr_real = round(min(_reward / _risk, 5.0), 1)
    # إصلاح #19: فرض R/R ≥ 1:1 على مستوى النظام (يطابق منطق risk_engine)
    # بدلاً من عرض R/R<1 وترك القرار للمستخدم — نرفع TP1/TP2 تناسبياً
    _rr_adjusted_note = ""
    if rr_real < 1.0:
        _old_tp1 = tp1_v
        _new_tp1 = price + _risk  # يضمن reward == risk → R/R = 1.0
        if tp2_v and _old_tp1 > price:
            _tp2_ratio = (tp2_v - price) / max(_old_tp1 - price, 1e-9)
            tp2_v = price + (_new_tp1 - price) * _tp2_ratio
        tp1_v   = _new_tp1
        _reward = max(tp1_v - price, 1e-9)
        rr_real = round(min(_reward / _risk, 5.0), 1)
        _rr_adjusted_note = " (مُعدَّل تلقائياً لضمان 1:1)"

    # Worst-Case
    # إصلاح #95: ضمان أن مستويات Worst-Case أعمق من (أو تساوي) SL المعروض،
    # ووصف wc_loss يعكس المستوى الأعمق (wc_bd2) لا pro_sl
    if pro_sl > 0 and pro_sl < price:
        wc_bd1 = min(price * 0.95, pro_sl)
        wc_bd2 = min(price * 0.90, pro_sl * 0.95)
    else:
        wc_bd1 = price * 0.95
        wc_bd2 = price * 0.90
    wc_loss = abs(price - wc_bd2) / max(price, 1e-9) * 100

    sl_type_ar    = _get_sl_type(_scenario, rsi)
    # FIN3/FIN3b: Trailing Stop عند ارتداد من قاع أو دخول قوي
    _use_trailing = (
        (rsi < 15) or                                  # قاع تاريخي → trailing دائماً
        (rsi < 30 and not _fin_corrupted) or           # قاع عادي (بيانات نظيفة)
        (conf >= 0.65 and direction == "long" and rsi < 60)  # دخول قوي
    )
    exit_strategy = (
        "TP1 (50%) ← TP2 (30%) ← TP3 (20%) مع Trailing SL"
        if tp3_v else
        "TP1 (50%) ← TP2 (30%) مع Trailing Stop (يتحرك مع السعر)"
        if _use_trailing else
        "TP1 (60%) ← TP2 (40%) مع Hard SL"
    )

    # ── بناء الأقسام ─────────────────────────────────────────

    # 1. Confidence Score — إصلاح #8: لا نُكرر التفصيل (موجود في "مصادر الإشارة" أعلاه)
    # تطوير #209: الرافعة المقترحة من النظام
    _lev = getattr(signal, "suggested_leverage", 1)
    _lev_note = {
        1: "🛡️ 1x — حماية قصوى",
        2: "⚡ 2x — محافظ",
        3: "⚡ 3x — معتدل",
        5: "🔥 5x — ثقة عالية، سوق صاعد",
    }.get(_lev, f"{_lev}x")
    parts.extend([
        "",
        f"*🎯 Confidence Score: {_conf_score}%*",
        f"• القرار: *{_decision_label}*",
        f"• الرافعة المقترحة: {_lev_note}",
    ])

    # 2. SMC Block
    smc_lines = ["", "*📊 تحليل الهيكلة (SMC)*"]
    if _mp_ar:
        smc_lines.append(f"• Market Phase: {_mp_ar}")
    smc_lines.append(
        "• RSI Divergence: 🟢 Bullish Divergence ✓" if _rsi_div == "bullish" else
        "• RSI Divergence: 🔴 Bearish Divergence ✓" if _rsi_div == "bearish" else
        "• RSI Divergence: ⚪ لا divergence"
    )
    smc_lines.append(f"• Volume Profile: {_get_vol_profile_ar(_vol_prof, _vol_ratio)}")
    smc_lines.append(f"• Bollinger Bands: {_get_bb_status_ar(_bb_pos_v)}")
    if _atr_val:
        # EE5 (#1990/#2027): format ذكي للـ ATR — للأسعار الصغيرة (< $1) نستخدم منازل عشرية
        _atr_fmt = (f"${_atr_val:.6f}" if _atr_val < 0.01
                    else f"${_atr_val:.4f}" if _atr_val < 0.1
                    else f"${_atr_val:.2f}" if _atr_val < 10
                    else f"${_atr_val:,.0f}")
        smc_lines.append(f"• ATR (تقلب): {_atr_fmt} يومياً")
    parts.extend(smc_lines)

    # 3. Derivatives (إذا متاحة)
    deriv_lines = []
    if _fund_pct != 0:
        deriv_lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fund_sig}")
    if _oi_chg != 0:
        deriv_lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
    # إصلاح #241-A: Whale/TVL غير ذي صلة للأصول المُرمَّزة غير الرقمية
    _is_perp_asset = tech.get("is_perp_asset", False)
    if _whale_sig and not _is_perp_asset:
        _wr_txt = f" ({_whale_ratio:.2f})" if _whale_ratio > 0 else ""
        deriv_lines.append(f"• Whale Activity{_wr_txt}: {_whale_sig}")
    # TVL من On-chain data
    _onchain = tech.get("onchain_data", {}) or {}
    _tvl = float(_onchain.get("tvl") or 0)
    if _tvl > 0 and not _is_perp_asset:
        _tvl_chg = float(_onchain.get("tvl_change_1d", 0) or 0)
        deriv_lines.append(f"• TVL الكلي: ${_tvl/1e9:.1f}B ({_tvl_chg:+.1f}% 24h)")
    # OKX_Agent_Skills_fix: CVD من enrichment
    _cvd_sig = _onchain.get("cvd_signal", "")
    _cvd_pct = float(_onchain.get("cvd_pct", 0) or 0)
    if _cvd_sig:
        deriv_lines.append(f"• CVD (100 صفقة): {_cvd_sig} ({_cvd_pct:+.1f}%)")
    if deriv_lines:
        parts.extend(["", "*🔗 Derivatives & On-Chain*"])
        parts.extend(deriv_lines)

    # 4. Entry + TP (مع مراعاة #440: لا TP عند WAIT)
    # إصلاح #785: TP% من السعر الحالي للمستخدم وليس من entry
    tp1_pct = abs(tp1_v - price) / max(price, 1e-9) * 100
        # إصلاح #849: tp2_pct من السعر الحالي
    tp2_pct = abs(tp2_v - price) / max(price, 1e-9) * 100 if tp2_v else 0
    # إصلاح #8: تسمية دقيقة — "عند الدعم" فقط إذا entry_agg ≈ مستوى الدعم المعروض
    _agg_label = "عند الدعم" if (ns > 0 and abs(entry_agg - ns) / max(ns, 1e-9) < 0.005) else "سحب فني (Pullback)"
    entry_lines = [
        "",
        "*📍 مناطق الدخول والخروج*",
        f"• Entry 1 (Aggressive): {_fmt_price(entry_agg)} — {_agg_label}",
        f"• Entry 2 (Conservative): {_fmt_price(entry_cons)} — بعد تأكيد الارتداد",
    ]
    if _conf_score >= 40:  # لا أهداف عند WAIT
        entry_lines.extend([
            f"• TP1: {_fmt_price(tp1_v)} (+{tp1_pct:.1f}%)",
            f"• TP2: {_fmt_price(tp2_v)} (+{tp2_pct:.1f}%)",
        ])
        if tp3_v:
            tp3_pct = abs(tp3_v - entry_agg) / max(entry_agg, 1e-9) * 100
            entry_lines.append(f"• TP3 (اختياري): {_fmt_price(tp3_v)} (+{tp3_pct:.1f}%)")
    else:
        entry_lines.append("• الأهداف: متاحة بعد تأكيد 2/4 مؤشرات")
    # T28_fix v2: R/R يُعرض فقط مع أهداف مؤكدة (≥2 confirmations)
    # _confirmed = _flags_found >= 2 (مُعرَّف أعلاه)
    _has_real_targets = _confirmed and tp1_v > 0 and tp1_v != price
    _rr_line = (f"• R/R الواقعي: 1:{rr_real}{_rr_adjusted_note}"
                if _has_real_targets
                else "• R/R: غير محسوب — انتظر تأكيد 2/4 مؤشرات")
    entry_lines.extend([
        f"• وقف الخسارة: {_fmt_price(pro_sl)} ({sl_pct:.1f}%-)",
        _rr_line,
        f"• الحجم: {_pos_size_rule}",
    ])
    parts.extend(entry_lines)

    # 5. إدارة المخاطر + Time Exit + Worst-Case
    parts.extend([
        "",
        "*🛡️ إدارة المخاطر*",
        f"• نوع الوقف: {sl_type_ar}",
        f"• استراتيجية الخروج: {exit_strategy}",
        f"• المدة المتوقعة: {_trade_dur}",
        f"• ⏰ Time Exit: إذا لا حركة بعد {_time_exit} → أغلق الموضع",
        f"• ⚠️ Worst-Case: كسر {_fmt_price(wc_bd1)} → {_fmt_price(wc_bd2)} (خسارة ~{wc_loss:.1f}%)",
    ])

    # 6. إصلاح #204/#206: قسمان منفصلان بتسميات واضحة
    # 6-A. تأكيدات الدخول الحاسمة (تؤثر على حجم الصفقة عبر _confirmed/_flags_found)
    parts.extend(["", "*🔑 تأكيدات الدخول (تُحدِّد حجم الصفقة — الحد الأدنى: 2 من 4)*"])
    if _confirmed:
        parts.append(f"✅ {_flags_found}/4 مؤكدة — الإشارة *نشطة*")
    else:
        parts.append(f"⚠️ {_flags_found}/4 — انتظر مؤشر إضافي")
    for flag in _conf_flags:
        parts.append(f"  ✓ {flag}")
    if not _conf_flags:
        parts.append("  • لا تأكيدات بعد — يمكن الدخول بحجم مصغَّر فقط")

    # 6-B. Checklist الاستعداد (مؤشرات داعمة + تحذيرات)
    # T26_fix: Divergence = تحذير واضح لا مجرد check
    _div_line = ""
    if _rsi_div == "bullish":
        _div_line = "☑ RSI Divergence 🟢 (داعم للصعود)"
    elif _rsi_div == "bearish":
        _div_line = "⚠️ RSI Divergence 🔴 (تحذير هبوطي — لا تعدّه داعماً)"
    else:
        _div_line = "□ RSI Divergence"

    # T27_fix: Whale > 1.0 = Long صاعد، < 0.5 = Short هابط
    _whale_line = ""
    if _whale_ratio >= 1.3:
        _whale_line = f"☑ Whale Ratio {_whale_ratio:.2f} 🟢 (أغلبية Long — صاعد)"
    elif _whale_ratio > 0 and _whale_ratio <= 0.7:
        _whale_line = f"⚠️ Whale Ratio {_whale_ratio:.2f} 🔴 (أغلبية Short — هابط)"
    else:
        _whale_line = f"□ Whale Ratio {_whale_ratio:.2f} ⚪ (محايد)"

    # T29_fix: Reclaim فقط إذا السعر كان تحت المستوى وعاد فوقه
    _reclaim_line = ""
    if ns > 0:
        _above = price >= ns * 0.995
        _reclaim_line = (f"☑ السعر فوق الدعم {_fmt_price(ns)}" if _above
                         else f"□ إغلاق فوق الدعم {_fmt_price(ns)}")
    else:
        _reclaim_line = "□ دعم غير محدد"

    parts.extend([
        "",
        "*📋 Checklist الاستعداد (مؤشرات داعمة — لا تؤثر على حجم الصفقة)*",
        _div_line,
        f"{'☑' if _vol_ratio >= 1.5 else '□'} Volume Spike ≥1.5x (حالياً {_vol_ratio:.1f}x)",
        _reclaim_line,
        f"{'☑' if tech.get('macd_hist', 0) > 0 else '□'} MACD إيجابي",
        _whale_line,
        f"{'☑' if _fund_pct < -0.01 else '□'} Funding Rate مناسب",
    ])

    return "\n".join(parts), _free_warning

DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def _eng(context):
    return context.bot_data.get("raed_engine")



def _strip_header_duplicates(text: str) -> str:
    """يحذف السطور المكررة (السعر/RSI/السوق) من نص التحليل."""
    if not text: return text
    lines = text.strip().split("\n")
    skip_keys = ("السعر:", "سعر:", "rsi", "fear", "السوق:", "الاتجاه:", "📊 تحليل", "💰 ال", "🌍")
    clean, skip_count = [], 0
    for line in lines:
        if skip_count < 8 and any(k in line.strip().lower() for k in skip_keys):
            skip_count += 1
            continue
        clean.append(line)
    result = "\n".join(clean).strip()
    return result if len(result) > 30 else text

def _clean_md(text: str) -> str:
    """
    يُنظّف النص من رموز Markdown v1 الخطرة.
    لا يُعدِّل الأرقام العشرية أو أسماء العملات.
    """
    if not text:
        return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        parts = line.split("*")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # خارج bold — نُنظّف _ فقط إذا لم تكن داخل رقم أو رمز عملة
                # نستبدل _ المحاطة بمسافات فقط (ليست جزءاً من كلمة)
                import re
                part = re.sub(r'(?<!\w)_(?!\w)', ' ', part)
                part = part.replace("`", "'")
            result.append(part)
        clean.append("*".join(result))
    return "\n".join(clean)


def _calc_atr(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < period + 1:
        return 3.0
    try:
        trs = []
        for i in range(1, len(candles)):
            h = float(candles[i].get("high", 0))
            l = float(candles[i].get("low", 0))
            c = float(candles[i-1].get("close", 0))
            if h > 0 and l > 0 and c > 0:
                trs.append(max(h - l, abs(h - c), abs(l - c)))
        if not trs:
            return 3.0
        atr   = sum(trs[-period:]) / period
        price = float(candles[-1].get("close", 0))
        # BB1: cap ATR عند 30% لمنع قيم مشوهة من انهيار حاد
        return min((atr / price * 100) if price > 0 else 3.0, 30.0)
    except (ValueError, TypeError, ZeroDivisionError):
        return 3.0


def _calc_atr_raw(candles: list, period: int = 14) -> float:
    """FIN1/FIN2c: ATR الفعلي للمنطق المالي — مع outlier removal.
    يستخدم آخر 10 شمعات فقط مع استبعاد القيم الشاذة (انهيار حاد).
    لا يستخدم للعرض — للحساب المالي فقط (TP/SL/Position Sizing).
    """
    if not candles or len(candles) < 2:
        return 3.0
    try:
        price = float(candles[-1].get("close", 0))
        if price <= 0:
            return 3.0

        # FIN2c: استخدام آخر 10 شمعات فقط (تجنب بيانات الانهيار التاريخية)
        _recent = candles[-min(10, len(candles)):]
        trs = []
        for i in range(1, len(_recent)):
            h  = float(_recent[i].get("high",  _recent[i].get("close", 0)))
            l  = float(_recent[i].get("low",   _recent[i].get("close", 0)))
            c  = float(_recent[i-1].get("close", _recent[i].get("close", 0)))
            if h > 0 and l > 0 and c > 0:
                trs.append(max(h - l, abs(h - c), abs(l - c)))
        if not trs:
            return 3.0

        # FIN2c: outlier removal — استبعاد TR > median × 4 (شمعة انهيار)
        trs_sorted = sorted(trs)
        _median = trs_sorted[len(trs_sorted) // 2]
        trs_clean = [tr for tr in trs if tr <= _median * 4.0]
        if not trs_clean:
            trs_clean = trs[-3:]  # fallback: آخر 3 شمعات

        atr = sum(trs_clean) / len(trs_clean)
        atr_pct = (atr / price * 100) if price > 0 else 3.0
        # FIN2c fallback: إذا ATR_clean >= 15% رغم التنظيف → بيانات لا تزال مشوهة
        # نستخدم 7% كـ ATR آمن افتراضي (متوسط تقلب يومي واقعي لمعظم الأصول)
        if atr_pct >= 15.0:
            return 7.0  # ATR آمن افتراضي
        return min(atr_pct, 14.0)  # cap طبيعي
    except (ValueError, TypeError, ZeroDivisionError):
        return 3.0


def _get_market_phase_ar(phase: str) -> str:
    """ترجمة Market Phase للعربية."""
    return {
        "Accumulation":   "🔵 تراكم (Accumulation)",
        "Distribution":   "🟠 توزيع (Distribution)",
        "Markup":         "🟢 صعود (Markup)",
        "Markdown":       "🔴 هبوط (Markdown)",
        "Consolidation":  "🟡 تعزيز (Consolidation)",
    }.get(phase, "⚪ غير محدد")


def _get_vol_profile_ar(vol_profile: str, vol_ratio: float) -> str:
    """تفسير Volume Profile."""
    if vol_profile == "climax_selling":
        return f"⚠️ Climax Selling ({vol_ratio:.1f}x) — ضغط بيعي استثنائي قد يشير لقاع"
    elif vol_profile == "climax_buying":
        return f"⚡ Climax Buying ({vol_ratio:.1f}x) — ضغط شرائي قوي"
    elif vol_profile == "above_average":
        return f"📈 حجم فوق المتوسط ({vol_ratio:.1f}x)"
    elif vol_profile == "no_demand" or vol_ratio < 0.8:
        return f"📉 حجم ضعيف ({vol_ratio:.1f}x) — غياب طلب"
    return f"⚪ حجم عادي ({vol_ratio:.1f}x)"


def _get_bb_status_ar(bb_pos: float) -> str:
    """موقع Bollinger Bands."""
    if bb_pos < 0.1:   return "📉 تحت الحد السفلي (Oversold شديد)"
    elif bb_pos < 0.25: return "⬇️ قرب الحد السفلي"
    elif bb_pos > 0.9:  return "📈 فوق الحد العلوي (Overbought شديد)"
    elif bb_pos > 0.75: return "⬆️ قرب الحد العلوي"
    return "⚪ منتصف النطاق"


def _get_sl_type(scenario: str, rsi: float) -> str:
    """نوع وقف الخسارة حسب السيناريو."""
    if scenario == "counter_trend_bounce":
        return "Hard Stop — وقف صارم لا يتحرك"
    elif scenario == "trend_reversal":
        return "Trailing Stop — يتحرك مع الربح"
    return "Hard Stop"


def _build_scenarios_context(
    price: float,
    atr_pct: float,
    fib: dict,
    rsi: float,
    is_bear: bool,
    threshold: float = 0.65,   # T2_fix: عتبة الباقة
) -> str:
    """
    يبني السيناريوهات الثلاثة من بيانات حقيقية:
    - مستويات الدعم/المقاومة من Fibonacci
    - ATR لحساب الحركة المتوقعة
    يُمرَّر لـ Groq كـ context — Groq يُفسر فقط
    """
    atr = price * atr_pct / 100
    ns  = fib.get("nearest_support",    0) or price * 0.97
    nr  = fib.get("nearest_resistance", 0) or price * 1.05
    f382 = fib.get("0.382", price * 1.06)
    f618 = fib.get("0.618", price * 1.10)
    f786 = fib.get("0.786", price * 1.14)

    # دعوم ومقاومات واقعية
    # إصلاح #94: لا نُقرِّب لمنزلتين — يُصفِّر عملات السعر <$0.01 (مثل SUPRA)
    # _fmt_price يتعامل مع الدقة المناسبة لكل نطاق سعري تلقائياً
    sup1 = ns
    sup2 = ns - atr * 1.5
    sup3 = ns - atr * 3.0
    res1 = nr
    res2 = f382 if f382 > price else nr * 1.04
    res3 = f618 if f618 > price else nr * 1.08

    rsi_note = ""
    if rsi < 20:
        rsi_note = f"RSI={rsi:.0f} (تشبع بيعي تاريخي — يرفع احتمال الارتداد قبل استئناف الاتجاه)."
    elif rsi < 30:
        rsi_note = f"RSI={rsi:.0f} (تشبع بيعي — احتمال ارتداد قصير)."
    elif rsi > 70:
        rsi_note = f"RSI={rsi:.0f} (تشبع شرائي — احتمال تصحيح)."

    if is_bear:
        scenarios = (
            "السيناريوهات المحتملة: "
            + f"أ) هابط: كسر {_fmt_price(sup1)} يفتح {_fmt_price(sup2)} ثم {_fmt_price(sup3)}. "
            + f"ب) ارتداد: ثبات فوق {_fmt_price(sup1)} يستهدف {_fmt_price(res1)} ثم {_fmt_price(res2)}. "
            + f"ج) انعكاس: استعادة {_fmt_price(res2)} تستهدف {_fmt_price(res3)}. "
            + f"للشورت: رفض من {_fmt_price(res1)}-{_fmt_price(res2)} مع ضعف الزخم. "
            + f"للـ scalp long: ثبات فوق {_fmt_price(sup1)} ووقف تحت {_fmt_price(sup2)}. "
            + f"للمحافظ: انتظار إغلاق فوق {_fmt_price(res1)} أو كسر {_fmt_price(sup2)}."
        )
    else:
        scenarios = (
            "السيناريوهات المحتملة: "
            + f"أ) صاعد: ثبات فوق {_fmt_price(ns)} يستهدف {_fmt_price(res1)} ثم {_fmt_price(res2)}. "
            + f"ب) تصحيح: كسر {_fmt_price(ns)} يفتح {_fmt_price(sup2)}. "
            + f"ج) انعكاس هابط: كسر {_fmt_price(sup2)} يعيد النظر في الاتجاه. "
            + f"للـ long: دخول عند {_fmt_price(ns)} ووقف {_fmt_price(sup2)}. "
            + f"للمحافظ: انتظار إغلاق فوق {_fmt_price(res1)}."
        )

    return (rsi_note + "\n" + scenarios).strip() if rsi_note else scenarios.strip()


def _fmt_volume(vol: float) -> str:
    """تنسيق الحجم بوحدة مناسبة: B/M$."""
    if vol <= 0:
        return "N/A"
    # إصلاح #515: vol > 1T يعني contracts وليس USD → نقسم على 1000
    if vol >= 1e13:
        vol = vol / 1000
    if vol >= 1e9:
        return f"{vol/1e9:.1f}B$"
    elif vol >= 1e6:
        return f"{vol/1e6:.1f}M$"
    elif vol >= 1e3:
        return f"{vol/1e3:.1f}K$"
    else:
        return f"{vol:,.0f}$"


def _calc_bb_pos(closes: list, period: int = 20) -> float:
    """موقع السعر في Bollinger Bands: 0=أدنى، 1=أعلى."""
    if len(closes) < period:
        return 0.5
    window = closes[-period:]
    avg = sum(window) / period
    std = (sum((x-avg)**2 for x in window)/period)**0.5
    if std == 0:
        return 0.5
    upper = avg + 2*std
    lower = avg - 2*std
    price = closes[-1]
    return round(max(0, min(1, (price - lower) / (upper - lower))), 3)


def _calc_rsi_from_closes(closes: list, period: int = 14) -> float:
    """حساب RSI من قائمة أسعار إغلاق مباشرة (MTF_fix)."""
    if len(closes) < period + 1:
        return 0.0
    try:
        px = [float(p) for p in closes[-(period+1):] if p and float(p) > 0]
        if len(px) < period + 1: return 0.0
        gs = [max(0.0, px[i]-px[i-1]) for i in range(1, len(px))]
        ls = [max(0.0, px[i-1]-px[i]) for i in range(1, len(px))]
        ag = sum(gs) / period
        al = sum(ls) / period
        if al == 0: return 100.0 if ag > 0 else 50.0
        return round(100.0 - (100.0 / (1.0 + ag / al)), 1)
    except Exception:
        return 0.0


def _calc_rsi(candles: list, period: int = 14) -> float:
    """حساب RSI دقيق."""
    if len(candles) < period + 1:
        return 50.0
    try:
        px  = [float(c.get("close", c.get("price", 0))) for c in candles[-(period+1):]]
        if any(p <= 0 for p in px):
            return 50.0
        gs  = [max(0.0, px[i] - px[i-1]) for i in range(1, len(px))]
        ls  = [max(0.0, px[i-1] - px[i]) for i in range(1, len(px))]
        ag  = sum(gs) / period
        al  = sum(ls) / period
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + ag / al))
    except Exception:
        return 50.0


# ════════════════════════════════════════════════════════════════
# /news
# ════════════════════════════════════════════════════════════════
@require_tier("news")
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args     = context.args or []
    symbols  = [a.upper() for a in args] or ["BTC", "ETH", "BNB"]
    sym_str  = ", ".join(symbols)
    msg = await update.message.reply_text(
        f"📰 جاري جلب وتحليل الأخبار لـ {sym_str}...\n"
        "⏳ قد يستغرق 10-20 ثانية — يُرجى الانتظار"
    )

    try:
        # إصلاح #924: timeout صارم + fallback
        import asyncio as _aio_n
        try:
            items = await _aio_n.wait_for(
                engine.data_layer.get_news(
                    currencies=",".join(symbols), limit=20),
                timeout=20.0)
            items = items or []
        except (_aio_n.TimeoutError, Exception) as _ne:
            logger.warning(f"news fetch failed: {_ne}")
            items = []

        try:
            analysis = await _aio_n.wait_for(
                engine.news_engine.analyze(items, symbols),
                timeout=15.0)
            if not analysis or not isinstance(analysis, dict):
                analysis = engine.news_engine._neutral_analysis()
        except Exception as e:
            logger.warning(f"news analyze: {e}")
            analysis = engine.news_engine._neutral_analysis()

        try:
            if items:
                engine.event_risk.ingest_news_events(items)
        except Exception:
            pass

        text = engine.news_engine.format_ar(items, analysis)
        text = _clean_md(text) if text else ""
        if not text:
            text = "📰 لا توجد أخبار متاحة حالياً. حاول لاحقاً."

        # إصلاح #16: تحذير عند تناقض مشاعر الأخبار مع حالة السوق الفعلية
        try:
            _fear_n  = await _aio_n.wait_for(engine.data_layer.get_fear_greed(), timeout=8.0)
            _fear_v  = int((_fear_n or {}).get("value") or 50)
            _btc_c   = await _aio_n.wait_for(engine.data_layer.get_ohlcv("BTC", "1d", 60), timeout=10.0)
            _btc_c   = _btc_c if isinstance(_btc_c, list) else []
            if len(_btc_c) >= 30:
                _regime_n = engine.regime_detector.detect(_btc_c, fear_greed=_fear_v)
                _sent_n   = float(analysis.get("sentiment_score", 0) or 0)
                if _sent_n > 0.3 and "هابط" in _regime_n.description_ar:
                    text += (f"\n\n⚠️ *تنبيه:* مشاعر الأخبار إيجابية لكن السوق "
                             f"{_regime_n.description_ar} (Fear & Greed: {_fear_v}) "
                             f"— لا تعتمد على الأخبار وحدها لاتخاذ القرار")
        except Exception as _ce:
            logger.debug(f"news contradiction check: {_ce}")

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"cmd_news: {e}", exc_info=True)
        try:
            await msg.edit_text("📰 تعذَّر جلب الأخبار حالياً. حاول مجدداً لاحقاً.")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
# /onchain
# ════════════════════════════════════════════════════════════════
@require_tier("onchain")
async def cmd_onchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await update.message.reply_text("🔗 جاري جلب بيانات On-Chain...")
    try:
        data, fear, funding, oi, whale, btc_adv = await asyncio.gather(
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_funding_rate("BTC"),
            engine.data_layer.get_open_interest("BTC"),
            engine.data_layer.get_whale_ratio("BTC"),
            engine.data_layer.get_btc_onchain_advanced(),
            return_exceptions=True
        )
        btc_adv = btc_adv if isinstance(btc_adv, dict) else {"available": False}
        funding = funding if isinstance(funding, dict) else {}
        oi      = oi      if isinstance(oi,      dict) else {}
        whale   = whale   if isinstance(whale,   dict) else {}
        data  = data  if isinstance(data, dict) else {"tvl": 0, "protocols": []}
        fear  = fear  if isinstance(fear, dict) else {"value": 50, "label_ar": "محايد"}
        top_p = (data.get("protocols") or [])[:5]
        tvl        = float(data.get("tvl") or 0)
        tvl_change = float(data.get("tvl_change_1d") or 0)
        fear_val   = int(fear.get("value") or 50)

        # تفسير Fear & Greed
        if fear_val <= 20:
            fear_emoji = "😱"
        elif fear_val <= 40:
            fear_emoji = "😨"
        elif fear_val <= 60:
            fear_emoji = "😐"
        elif fear_val <= 80:
            fear_emoji = "😊"
        else:
            fear_emoji = "🤑"

        lines = [
            "🔗 *تحليل On-Chain — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"📊 إجمالي TVL: ${tvl/1e9:.2f}B",
            f"{fear_emoji} Fear & Greed: {fear_val} — {fear.get('label_ar', 'محايد')}",
            f"📈 تغيير TVL 24h: {tvl_change:+.2f}%",
        ]

        # بيانات شبكة Bitcoin
        btc_hashrate = data.get("btc_hashrate", 0)
        btc_tx       = data.get("btc_tx_count_24h", 0)
        if btc_hashrate > 0 or btc_tx > 0:
            lines += ["", "⛏️ *شبكة Bitcoin*"]
            if btc_hashrate > 0:
                lines.append(f"• Hashrate: {btc_hashrate/1e9:.1f} EH/s")
            if btc_tx > 0:
                lines.append(f"• معاملات 24h: {btc_tx:,}")

        if top_p:
            lines += ["", "🏆 *أكبر البروتوكولات*"]
            for i, p in enumerate(top_p, 1):
                tvl_b = float(p.get("tvl") or 0) / 1e9
                name  = str(p.get("name", "")).replace("_", " ")
                lines.append(f"{i}. {name} — ${tvl_b:.2f}B")
        else:
            lines += ["", "⚠️ بيانات البروتوكولات غير متاحة حالياً — يُرجى المحاولة لاحقاً"]

        # إصلاح #13: قسم المشتقات BTC (Funding/OI/Whale) — بيانات كانت تُجلَب ولا تُعرض
        _fund_pct  = float((funding or {}).get("rate_pct", 0) or 0)
        _oi_chg    = float((oi or {}).get("oi_change_pct", 0) or 0)
        _oi_sig    = (oi or {}).get("signal", "")
        _whale_sig = (whale or {}).get("signal", "")
        _whale_r   = float((whale or {}).get("ratio", 0) or 0)

        if _fund_pct or _oi_chg or _whale_sig:
            lines += ["", "📐 *مشتقات BTC*"]
            if _fund_pct:
                if _fund_pct < -0.02:
                    _fsig = "🟢 سالب جداً — ضغط Shorts (فرصة Long)"
                elif _fund_pct < -0.005:
                    _fsig = "🟢 سالب — فرصة Long"
                elif _fund_pct > 0.02:
                    _fsig = "🔴 مرتفع جداً — ضغط Longs"
                elif _fund_pct > 0.005:
                    _fsig = "🟡 إيجابي — محايد"
                else:
                    _fsig = "⚪ محايد"
                lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fsig}")
            if _oi_chg:
                lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
            if _whale_sig:
                _wr_txt = f" ({_whale_r:.2f})" if _whale_r > 0 else ""
                lines.append(f"• Whale Ratio{_wr_txt}: {_whale_sig}")

        # تطوير جديد: مؤشرات BTC on-chain متقدمة من BGeometrics
        # (MVRV Z-Score, SOPR, Exchange Netflow, Puell Multiple)
        if btc_adv.get("available"):
            lines += ["", "📊 *مؤشرات BTC المتقدمة (BGeometrics)*"]
            if "mvrv_zscore" in btc_adv:
                lines.append(f"• MVRV Z-Score: {btc_adv['mvrv_zscore']} — {btc_adv['mvrv_signal']}")
            if "sopr" in btc_adv:
                lines.append(f"• SOPR: {btc_adv['sopr']} — {btc_adv['sopr_signal']}")
            if "exchange_netflow_btc" in btc_adv:
                lines.append(f"• Exchange Netflow: {btc_adv['exchange_netflow_btc']:+,.1f} BTC — {btc_adv['netflow_signal']}")
            if "puell_multiple" in btc_adv:
                lines.append(f"• Puell Multiple: {btc_adv['puell_multiple']} — {btc_adv['puell_signal']}")

        # تفسير وتوصية إجمالية مختصرة بناءً على البيانات المجمَّعة
        _reco_parts = []
        # EE3 (#2044): تفسير Fear & Greed بناءً على القيمة الفعلية (AA2)
        if fear_val < 20:
            _reco_parts.append("Fear شديد → فرصة تجميع تدريجي عند التأكيد")
        elif fear_val < 40:
            _reco_parts.append(f"خوف ({fear_val}) → ترقّب فرصة تجميع")
        elif fear_val >= 80:
            _reco_parts.append("Greed شديد → حذر من انعكاس وشيك")
        elif fear_val >= 60:
            _reco_parts.append("Greed مرتفع → حذر من الانعكاس")
        if _fund_pct < -0.01:
            _reco_parts.append("Funding سالب يدعم سيناريو الارتداد")
        if _whale_r and _whale_r < 0.6:
            _reco_parts.append("الحيتان تتراكم")
        if tvl_change < -3:
            _reco_parts.append("خروج سيولة من DeFi")
        if btc_adv.get("mvrv_zscore") is not None:
            if btc_adv["mvrv_zscore"] < 0:
                _reco_parts.append("MVRV Z-Score في منطقة قاع تاريخية")
            elif btc_adv["mvrv_zscore"] > 7:
                _reco_parts.append("MVRV Z-Score في منطقة قمة تاريخية — حذر")
        if btc_adv.get("exchange_netflow_btc") is not None and btc_adv["exchange_netflow_btc"] < 0:
            _reco_parts.append("تدفق صافٍ خارج البورصات (Hodling)")
        if _reco_parts:
            lines += ["", f"💡 *التفسير*: {' · '.join(_reco_parts)}"]

        # T17_fix v2: CoinGecko بديل Yahoo Finance (محظور على Railway)
        try:
            import urllib.request as _ur, json as _jj, ssl as _ssl
            _ctx_t17 = _ssl.create_default_context()
            _ctx_t17.check_hostname = False
            _ctx_t17.verify_mode = _ssl.CERT_NONE
            _macro_lines = []
            # CoinGecko global market (مجاني بلا API key)
            try:
                _url_cg = "https://api.coingecko.com/api/v3/global"
                _req_cg = _ur.Request(_url_cg, headers={"User-Agent":"Mozilla/5.0"})
                _resp_cg = _ur.urlopen(_req_cg, context=_ctx_t17, timeout=6)
                _d_cg = _jj.loads(_resp_cg.read()).get("data", {})
                _btc_dom = _d_cg.get("market_cap_percentage", {}).get("btc", 0)
                _mcap_chg = _d_cg.get("market_cap_change_percentage_24h_usd", 0)
                _total_mcap = _d_cg.get("total_market_cap", {}).get("usd", 0)
                if _btc_dom:
                    _macro_lines.append(
                        f"• هيمنة BTC: {_btc_dom:.1f}% "
                        f"({'📈' if _btc_dom > 50 else '📉'})"
                    )
                if _mcap_chg:
                    _macro_lines.append(
                        f"• إجمالي السوق: ${_total_mcap/1e9:.0f}B "
                        f"({_mcap_chg:+.1f}% 24h) "
                        f"{'📈' if _mcap_chg > 0 else '📉'}"
                    )
            except Exception:
                pass
            # DeFi dominance كسياق إضافي
            try:
                _url_defi = "https://api.coingecko.com/api/v3/global/decentralized_finance_defi"
                _req_defi = _ur.Request(_url_defi, headers={"User-Agent":"Mozilla/5.0"})
                _resp_defi = _ur.urlopen(_req_defi, context=_ctx_t17, timeout=6)
                _d_defi = _jj.loads(_resp_defi.read()).get("data", {})
                _defi_dom = float(_d_defi.get("defi_dominance", 0) or 0)
                _defi_mcap = float(_d_defi.get("defi_market_cap", 0) or 0)
                if _defi_dom:
                    _macro_lines.append(
                        f"• هيمنة DeFi: {_defi_dom:.1f}% "
                        f"(${_defi_mcap/1e9:.0f}B)"
                    )
            except Exception:
                pass
            if _macro_lines:
                lines += ["", "🌍 *السياق الكلي*"] + _macro_lines
        except Exception:
            pass

        # T17_fix: سيناريوهات بناءً على البيانات المجمَّعة
        _bull_conditions = []
        _bear_conditions = []
        if fear_val < 30: _bull_conditions.append("Fear & Greed منخفض (فرصة تراكم)")
        if _fund_pct < -0.01: _bull_conditions.append("Funding سالب (ضغط Shorts)")
        if tvl_change > 1: _bull_conditions.append("TVL يرتفع")
        if fear_val > 70: _bear_conditions.append("Greed مرتفع (خطر الانعكاس)")
        if _fund_pct > 0.02: _bear_conditions.append("Funding مرتفع (Long مزدحم)")
        if tvl_change < -3: _bear_conditions.append("خروج سيولة DeFi")

        if _bull_conditions or _bear_conditions:
            lines += ["", "📋 *السيناريوهات*"]
            if _bull_conditions:
                lines.append(f"🟢 صاعد: {' + '.join(_bull_conditions[:2])}")
            if _bear_conditions:
                lines.append(f"🔴 هابط: {' + '.join(_bear_conditions[:2])}")
            if not _bull_conditions and not _bear_conditions:
                lines.append("⚪ محايد: انتظار محفز واضح")

        _src = "📡 المصدر: DeFiLlama + OKX"
        if btc_adv.get("available"):
            _src += " + BGeometrics"
        lines += ["", f"{_src} | 🤖 رائد"]
        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_onchain: {e}")
        await msg.edit_text("❌ خطأ في جلب بيانات On-Chain. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /regime
# ════════════════════════════════════════════════════════════════
@require_tier("regime")
async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await update.message.reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    symbol = args[0].upper()
    msg = await update.message.reply_text(
        f"📊 جاري تحليل حالة السوق لـ {symbol}...")

    try:
        candles, fear, btc_dom = await asyncio.gather(
            engine.data_layer.get_ohlcv(symbol, "1d", 250),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_btc_dominance(),
            return_exceptions=True
        )
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}

        if len(candles) < 30:
            await msg.edit_text(
                (
                    f"⚠️ *{symbol}* غير مدرج في OKX حالياً\n"
                    f"• تحقق من قائمة الأصول في OKX\n"
                    f"• أو جرّب رمزاً مختلفاً"
                    if symbol.upper() not in _OKX_TOKENIZED_STOCKS
                    else f"⚠️ بيانات {symbol} غير متوفرة مؤقتاً — أعد المحاولة بعد دقيقة"
                ))
            return

        fear_val_r = int(fear.get("value") or 50)
        result = engine.regime_detector.detect(
            candles, btc_dominance=btc_dom, fear_greed=fear_val_r)
        text = _clean_md(engine.regime_detector.format_ar(result))

        # F8: توصيات أوضح بناءً على regime
        _regime_action = getattr(result, "action", "")
        _regime_desc   = getattr(result, "description_ar", "")
        _regime_tips = {
            "trade_normal":    "✅ السوق مناسب للتداول — يمكن الدخول بحجم طبيعي",
            "reduce_size":     "⚠️ قلل حجم الصفقات إلى 50% — السوق غير مستقر",
            "wait":            "⏳ انتظر — لا توجد إشارة واضحة الآن",
            "avoid":           "🚫 تجنب الدخول — ADX مرتفع جداً أو تقلب شديد",
            "bounce_entry_confirmed": "🎯 فرصة ارتداد محتملة — راقب التأكيدات",
        }
        _tip = _regime_tips.get(_regime_action, "")
        if _tip:
            text += f"\n\n💡 *توصية رائد:* {_tip}"

        # T20_fix v2: ADX من result.metrics (المكان الصحيح)
        try:
            _metrics_r = getattr(result, "metrics", {}) or {}
            _adx_r = float(_metrics_r.get("adx", 0) or 0)
            _atr_r = float(_metrics_r.get("atr_pct", 0) or 0)
            _vol_r = float(_metrics_r.get("volume_ratio", 1) or 1)
            _warns = []
            if _adx_r > 0 and _adx_r < 20:
                _warns.append(f"⚠️ ADX={_adx_r:.0f} < 20 → لا اتجاه واضح (Whipsaw محتمل)")
            elif _adx_r >= 40:
                _warns.append(f"⚠️ ADX={_adx_r:.0f} ≥ 40 → تقلب شديد، قلل الحجم")
            if _vol_r < 0.8:
                _warns.append(f"⚠️ Volume {_vol_r:.1f}x → ضغط بيع خفي أو انتظار")
            if _atr_r > 4:
                _warns.append(f"⚠️ ATR={_atr_r:.1f}% مرتفع → تقلب شديد")
            if _warns:
                text += "\n\n⚠️ *تحذيرات النظام*\n" + "\n".join(_warns)
        except Exception:
            pass

        text += f"\n\n📡 *المصدر:* OKX + BGeometrics | 🤖 رائد التداول الذكي"
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_regime: {e}")
        await msg.edit_text("❌ خطأ في تحليل السوق\n• تحقق من الاتصال بالإنترنت\n• حاول مرة أخرى بعد دقيقة")


# ════════════════════════════════════════════════════════════════
# /signal
# ════════════════════════════════════════════════════════════════
@require_tier("signal")
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine  = _eng(context)
    user_id = update.effective_user.id if update.effective_user else 0
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args    = context.args or ["BTC"]
    raw_arg = args[0].upper()
    # TK_name_fix: حفظ الرمز الأصلي للعرض (XSPCX وليس SPCX)
    _display_symbol = raw_arg  # يُستخدم في العرض للمستخدم

    # SYM_WARN (#2570/#2572): تحذير تضارب الرموز
    if raw_arg in _AMBIGUOUS_SYMBOLS:
        await _get_message(update, context).reply_text(
            f"⚠️ *تنبيه — رمز متضارب:* `{raw_arg}`\n"
            f"{_AMBIGUOUS_SYMBOLS[raw_arg]}\n\n"
            f"جاري التحليل بناءً على البيانات المتاحة في OKX...",
            parse_mode="Markdown"
        )

    # تطوير #221: سؤال نوع السوق (Spot/Futures)
    # إصلاح #237-A+#248: الأصول المُرمَّزة → Futures تلقائياً
    # ملاحظة: tier2 يُعرَّف لاحقاً — نستخدم user_id مباشرة هنا
    _mkttype = context.user_data.pop("_mkttype", None)
    if _mkttype is None:
        _tier_early = _sm.get_tier(user_id)
        if _tier_early in ("diamond","admin"):
            try:
                _raw_symbol = (context.args or ["BTC"])[0].upper()
                # TK_ROOT_fix: X-prefix (XSPCX,XAMZN..) = Spot مُرمَّز → لا نُجبر Futures
                _is_x_prefix = _raw_symbol.startswith("X") and len(_raw_symbol) > 2
                if not _is_x_prefix and await engine.data_layer.is_tokenized_stock(_raw_symbol):
                    _mkttype = "futures"  # أصل مُرمَّز بدون X → Futures تلقائياً
                # T13_fix: السلع (CL/NL/GC..) → Futures تلقائياً بدون سؤال
                elif engine.data_layer.is_commodity_symbol(_raw_symbol):
                    _mkttype = "futures"
            except Exception:
                pass
        # T13_fix: تحقق إضافي للسلع خارج نطاق diamond
        if _mkttype is None:
            try:
                from core.data_layer import is_commodity_symbol as _is_comm
                if _is_comm((context.args or ["BTC"])[0].upper()):
                    _mkttype = "futures"
            except Exception:
                pass
        if _mkttype is None:
            sent = await _ask_market_type(update, context, "signal", raw_arg, _sm.get_tier(user_id))
            if sent:
                return

    _use_futures = (_mkttype == "futures")
    _mkt_arg_sig = "futures" if _use_futures else "spot"  # إصلاح #258

    # TK2/TK1b: التحقق من توفر الأصل في Spot
    if not _use_futures:
        # TK2_fix: الأصول التي تبدأ بـ X (XSPCX, XAMZN...) أصول OKX Spot مؤكدة
        # /quicksignal يُثبت أنها متاحة — تجاوز check_spot مباشرة
        if raw_arg.upper().startswith("X") and len(raw_arg) > 2:
            from core.data_layer import resolve_stock_symbol as _rss
            _stock_res = _rss(raw_arg, "spot")
            _resolve_sym = _stock_res.get("base", raw_arg[1:])  # XSPCX → SPCX
        else:
            try:
                _spot_check = await engine.data_layer.check_spot_available(raw_arg)
                if not _spot_check.get("available", True):
                    await _get_message(update, context).reply_text(
                        _spot_check.get("message", f"⚠️ {raw_arg} غير متاح في Spot"),
                        parse_mode="Markdown"
                    )
                    return
                _spot_sym_actual = _spot_check.get("spot_symbol", raw_arg)
                if _spot_sym_actual != raw_arg.upper():
                    from core.data_layer import resolve_stock_symbol as _rss
                    _stock_res = _rss(_spot_sym_actual, "spot")
                    _resolve_sym = _stock_res.get("base", raw_arg)
                    raw_arg = _spot_sym_actual
                else:
                    _resolve_sym = raw_arg
            except Exception:
                _resolve_sym = raw_arg

    # تطوير #188 (Phase 2) + إصلاح #248: tier2 يُعرَّف هنا (موحَّد)
    user_id2   = update.effective_user.id
    tier2      = _sm.get_tier(user_id2)
    # TK1b_fix: استخدام الرمز الأساسي لـ resolve_symbol
    _sym_for_resolve = locals().get("_resolve_sym", raw_arg)
    resolution = await resolve_symbol(_sym_for_resolve, tier2, engine.data_layer)
    symbol     = resolution.base

    # TK_Spot_fix: للأصول X-prefix في Spot → استخدام XSPCX لجلب البيانات
    # XSKHY_fix: إذا X-prefix وليس في القائمة المعروفة → symbol = raw_arg كاملاً
    if raw_arg.upper().startswith("X") and len(raw_arg) > 2:
        _spot_data_symbol = raw_arg.upper()  # XSPCX/XSKHY للـ OKX API
        # إذا resolve أعطانا base مختلف → أعِد symbol للأصلي
        if symbol != raw_arg.upper() and not _use_futures:
            symbol = raw_arg.upper()
    else:
        _spot_data_symbol = symbol  # الرمز العادي

    # تطوير #209: اكتشاف تلقائي للأصول المُرمَّزة (ماسي+ فقط)
    # إصلاح #248: _is_perp_sig هو المصدر الوحيد (لا استدعاء مكرر)
    _is_perp_sig = False
    if tier2 in ("diamond", "admin"):
        try:
            _is_perp_sig = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            _is_perp_sig = False
    # إذا اكتُشف كأصل مُرمَّز ولم يُحدَّد النوع مسبقاً → Futures تلقائياً
    if _is_perp_sig and _mkttype is None:
        _mkttype = "futures"
        _use_futures = True

    # إصلاح #1020: عملات كبيرة مُعتمَدة دائماً للذهبي+
    _ALWAYS_ALLOWED = {
        "XLM","STELLAR","ICP","FIL","VET","EOS","XTZ","ALGO",
        "HBAR","EGLD","ONE","ZIL","ICX","WAVES","NEO","QTUM",
        "KAVA","BAND","RSR","NMR","RLC","ANKR","SKL","CKB",
    }
    # TK_tier_fix: قواعد الوصول للأسهم المُرمَّزة
    # Futures: جميع الباقات | Spot: ذهبي+ فقط
    if _is_perp_sig and _use_futures:
        pass  # الأسهم المُرمَّزة Futures → جميع الباقات بدون قيود
    elif _is_perp_sig and not _use_futures:
        # Spot للأسهم المُرمَّزة → ذهبي+ فقط
        if tier2 not in ("gold", "diamond", "admin"):
            await _get_message(update, context).reply_text(
                f"🔒 *تحليل {_display_symbol} الفوري — ذهبي وأعلى*\n\n"
                f"التداول الفوري للأسهم المُرمَّزة يتطلب باقة ذهبي أو أعلى.\n"
                f"⬆️ للترقية: /upgrade",
                parse_mode="Markdown"); return
    # إصلاح #1683: diamond/admin يصل لأي عملة بدون قيود
    elif tier2 in ("diamond", "admin"):
        pass  # ماسي+ = وصول كامل لجميع العملات
    elif tier2 in ("gold",) and symbol.upper() in _ALWAYS_ALLOWED:
        pass  # ذهبي: القائمة الموسّعة مسموحة
    elif not is_symbol_allowed(symbol, tier2):
        await _get_message(update, context).reply_text(
            (
                f"⛔ *{symbol}* غير متاحة لباقتك الحالية\n\n"
                f"باقتك: {_sm.get_tier_name(user_id)}\n"
                f"هذه العملة تتطلب باقة أعلى\n\n"
                f"⬆️ للترقية: /upgrade\n"
                f"📋 لعرض عملاتك المتاحة: /premium"
            ), parse_mode="Markdown"); return

    msg = await _get_message(update, context).reply_text(
        f"📡 جاري تحليل {_display_symbol} عبر 5 مصادر...\n"
        "⏳ قد يستغرق 20-30 ثانية — يُرجى الانتظار"
    )

    try:
        # TK_Spot_fix: استخدام XSPCX لجلب البيانات في Spot للأصول المُرمَّزة
        _data_sym_sig = _spot_data_symbol if not _use_futures else symbol
        _ohlcv_fn = engine.data_layer.get_ohlcv_perp(symbol, 365) if _is_perp_sig else engine.data_layer.get_ohlcv(_data_sym_sig, "1d", 365, mkttype=_mkt_arg_sig)
        candles, onchain, fear, news_raw, btc_dom, _sig_4h = await asyncio.gather(
            _ohlcv_fn,
            engine.data_layer.get_onchain(),
            engine.data_layer.get_fear_greed(),
            engine.data_layer.get_news(currencies=symbol),
            engine.data_layer.get_btc_dominance(),
            engine.data_layer.get_ohlcv_4h(symbol, 50),
            return_exceptions=True
        )
        candles  = candles  if isinstance(candles, list) else []
        onchain  = onchain  if isinstance(onchain, dict) else {}
        fear     = fear     if isinstance(fear, dict)    else {"value": 50}
        news_raw = news_raw if isinstance(news_raw, list) else []
        _sig_4h  = _sig_4h  if isinstance(_sig_4h, list)  else []

        try:
            news_an = await engine.news_engine.analyze(news_raw, [symbol])
            news_an = news_an if isinstance(news_an, dict) else {}
        except Exception:
            news_an = {}

        # X3_fix: خفض الحد لـ X-prefix assets (بيانات محدودة)
        _is_x_sig = raw_arg.upper().startswith("X") and len(raw_arg) > 2
        _min_candles_sig = 15 if _is_x_sig else 50
        if len(candles) < _min_candles_sig:
            # XSKHY_name_fix: عرض الاسم الكامل في رسالة الخطأ
            _err_sym = _display_symbol if _display_symbol else symbol
            # XSKHY_market_hours_fix v2: XSKHY/SKHY → رسالة السوق المغلق
            _is_market_closed_sym = symbol.upper() in {"XSKHY", "SKHY"}
            if _is_market_closed_sym:
                await msg.edit_text(
                    f"🕐 *{_err_sym}* — السوق مغلق حالياً\n"
                    f"• ساعات التداول: 00:00-06:30 UTC (09:00-15:30 KST)\n"
                    f"• أعد المحاولة خلال ساعات التداول",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            await msg.edit_text(
                (
                    f"⚠️ *{_err_sym}* غير مدرج في OKX حالياً\n"
                    f"• تحقق من قائمة الأصول في OKX\n"
                    f"• أو جرّب رمزاً مختلفاً"
                    if symbol.upper() not in _OKX_TOKENIZED_STOCKS
                    else f"⚠️ بيانات {_err_sym} غير متوفرة مؤقتاً — أعد المحاولة بعد دقيقة"
                ))
            return

        fear_val = int(fear.get("value") or 50)
        regime   = engine.regime_detector.detect(candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=symbol)

        sentiment = 0.0
        if news_an:
            raw_sent = news_an.get("sentiment_score")
            if raw_sent is not None:
                try:
                    sentiment = float(raw_sent)
                except (ValueError, TypeError):
                    sentiment = 0.0

        # إصلاح #34: إضافة whale_ratio/funding الخاصين بالعملة لـ onchain_data
        # (يستخدمها _onchain_signal بدل TVL العالمي الثابت)
        onchain = await engine.data_layer.get_signal_enrichment(symbol, onchain)

        signal = engine.signal_layer.generate(
            symbol=symbol, candles=candles, onchain_data=onchain,
            news_sentiment=sentiment,
            backtest_win_rate=0.55,
            macro_data={"fear_greed": fear_val},
            regime=regime,
        )
        # TK_name_fix: تعديل symbol المعروض للمستخدم (XSPCX بدلاً من SPCX)
        if hasattr(signal, "symbol") and _display_symbol != symbol:
            signal.symbol = _display_symbol
        strategy, params = engine.strategy_router.select(regime, signal)
        atr_pct = _calc_atr(candles)
        price   = float(candles[-1]["close"]) if candles else 0.0
        # تمرير سقف السيناريو لـ risk_engine
        _scenario_max = float(signal.technicals.get("max_size_pct", 0.35) or 0.35)
        risk    = engine.risk_engine.assess(
            symbol=symbol, direction=signal.direction,
            confidence=signal.confidence, price=price,
            atr_pct=atr_pct, regime=regime.regime.value,
            scenario_max_pct=_scenario_max,
        )

        # إصلاح #478: rsi من candles (الحقيقي) وليس من signal.technicals
        rsi = _calc_rsi(candles)
        # تحديث rsi في technicals لضمان التطابق في العرض
        if hasattr(signal, "technicals") and isinstance(signal.technicals, dict):
            signal.technicals["rsi"] = round(rsi, 1)
            # CVD_fix: تمرير onchain (بما فيه CVD) لـ technicals
            if onchain and isinstance(onchain, dict):
                signal.technicals["onchain_data"] = onchain

        # BB1b (#1550/#1553/#1574/#1575): فحص جودة البيانات — انهيار حاد
        _pve50_chk = float(regime.metrics.get("price_vs_ema50", 0) or 0)
        # FF3b/EMA_spot_fix: كشف فساد EMA50 من حساب مستقل في /signal
        _ema50_sig_corrupted = False
        _e50_sig_raw = 0.0
        if candles and len(candles) >= 50:
            try:
                _cls_sig = [float(c.get("close", 0)) for c in candles if c.get("close")]
                _e50_sig_raw = sum(_cls_sig[-50:]) / 50 if len(_cls_sig) >= 50 else price
                # T4_fix: EMA50 > 30% فوق السعر = فساد (بيانات Futures مختلطة)
                _ema50_sig_corrupted = (_e50_sig_raw > price * 1.30 or _e50_sig_raw > price * 3.0)
            except Exception: pass
        elif candles and len(candles) >= 10:
            # EMA_spot_fix: بيانات محدودة (X-prefix Spot جديد)
            # نحسب EMA من جميع الشمعات المتاحة
            try:
                _cls_sig = [float(c.get("close", 0)) for c in candles if c.get("close")]
                if len(_cls_sig) >= 10:
                    # EMA حقيقية من الشمعات المتاحة
                    _e50_sig_raw = _cls_sig[-1]  # نبدأ بالأخيرة
                    k = 2 / (len(_cls_sig) + 1)
                    for _c in reversed(_cls_sig[:-1]):
                        _e50_sig_raw = _c * k + _e50_sig_raw * (1 - k)
                    _pve50_approx = (price - _e50_sig_raw) / max(_e50_sig_raw, 0.0001) * 100
                    # استخدم دائماً هذه القيمة بغض النظر عن الفرق
                    _ema50_sig_corrupted = True
                    logger.info(f"EMA_spot_fix: {symbol} EMA_approx={_e50_sig_raw:.2f} ({_pve50_approx:+.1f}%)")
            except Exception: pass
        elif not candles or len(candles) < 10:
            # لا بيانات كافية → احسب من السعر الحالي (neutral)
            _e50_sig_raw = price
            _ema50_sig_corrupted = False
        _data_corrupted = (atr_pct > 25 or rsi < 5 or _pve50_chk < -50 or _ema50_sig_corrupted)
        if _data_corrupted:
            if hasattr(signal, "suggested_leverage"):
                signal.suggested_leverage = 1
            signal.confidence = min(signal.confidence, 0.49)
            # M1b: إعادة حساب Fibonacci بـ cap أشد عند بيانات مشوهة (price × 1.5)
            fib = _calc_fibonacci(candles, lookback=30, price_cap_mult=1.5)
            # إصلاح #479: BB من 4H إذا متاح
            if len(_sig_4h) >= 20:
                _c4h = [float(c.get("close",0)) for c in _sig_4h]
                signal.technicals["bb_pos"] = _calc_bb_pos(_c4h)
        # MTF_fix: Multi-timeframe تأكيد 4H + 1D
        _mtf_confirm = ""
        _mtf_warns = []
        try:
            if len(_sig_4h) >= 20:
                _c4h_cls = [float(c.get("close",0)) for c in _sig_4h if c.get("close")]
                _c4h_rsi = _calc_rsi_from_closes(_c4h_cls) if len(_c4h_cls) >= 14 else 0
                _c4h_ema20 = sum(_c4h_cls[-20:]) / 20 if len(_c4h_cls) >= 20 else 0
                _c4h_trend = "🟢 صاعد" if _c4h_cls[-1] > _c4h_ema20 else "🔴 هابط"
                # تعارض 4H مع الإشارة
                if signal.direction == "long" and _c4h_cls[-1] < _c4h_ema20:
                    _mtf_warns.append(f"⚠️ 4H هابط — تعارض مع إشارة الشراء")
                elif signal.direction == "short" and _c4h_cls[-1] > _c4h_ema20:
                    _mtf_warns.append(f"⚠️ 4H صاعد — تعارض مع إشارة البيع")
                else:
                    _mtf_confirm = f"✅ 4H يؤكد الاتجاه ({_c4h_trend})"
                if _c4h_rsi > 0:
                    if _c4h_rsi > 70:
                        _mtf_warns.append(f"⚠️ RSI 4H={_c4h_rsi:.0f} ذروة شراء")
                    elif _c4h_rsi < 30:
                        _mtf_warns.append(f"⚠️ RSI 4H={_c4h_rsi:.0f} ذروة بيع")
        except Exception:
            pass

        # MTF: عرض التأكيد أو التعارض
        warning = ""
        if _mtf_confirm:
            warning = f"\n\n{_mtf_confirm}"
        if _mtf_warns:
            warning += ("\n\n" if not warning else "\n") + "\n".join(_mtf_warns)
        if signal.direction == "short" and rsi < 30:
            warning += "\n\n⚠️ *تنبيه:* RSI في ذروة البيع مع إشارة بيع — خطر انعكاس مرتفع"
        elif signal.direction == "long" and rsi > 70:
            warning = "\n\n⚠️ *تنبيه:* RSI في ذروة الشراء مع إشارة شراء — تحقق من التوقيت"

        # Fibonacci + Professional Block
        fib        = _calc_fibonacci(candles)
        # إصلاح #241-A: تمرير is_perp_asset لإخفاء TVL/Whale للأصول غير الرقمية
        _sig_tech_extra = {"is_perp_asset": _is_perp_sig} if _is_perp_sig else {}
        pro_block, _free_warning  = _build_professional_block(
            symbol, price, signal, regime, candles, rsi, atr_pct, fib,
            tech_extra=_sig_tech_extra,
            tier=tier2,
            is_stock=_is_perp_sig)
        fib_lines  = _fmt_fib_lines(fib, price)

        # حذف تقييم المخاطر عند وجود Professional Block (M#51)
        _risk_text = _clean_md(engine.risk_engine.format_assessment_ar(risk, symbol))
        # إظهار تقييم المخاطر فقط عند الموافقة (لا عند الرفض مع وجود pro block)
        show_risk  = risk.decision.value == "approve" or not pro_block
        # إصلاح #477: تقليل التكرار — pro_block يحتوي معظم المعلومات
        # FA: BTC Correlation — تأثير BTC على الأصول الأخرى
        _btc_corr_txt = ""
        if symbol not in ("BTC", "BITCOIN") and atr_pct < 20:
            try:
                _btc_d = await engine.data_layer.get_price("BTC")
                _btc_chg = float((_btc_d or {}).get("change_24h", 0) or 0)
                _price_chg = float((await engine.data_layer.get_price(symbol) or {}).get("change_24h", 0) or 0)
                _corr_diff = _price_chg - _btc_chg
                # FA_missing_fix: إظهار FA دائماً (بدون شرط حجم التغير)
                # FA_name_fix: استخدام _display_symbol للعرض
                _sym_disp = f" {_display_symbol}" if "_display_symbol" in dir() else f" {symbol}"
                if _corr_diff > 2:
                    _btc_corr_txt = f"\n• 🟢{_sym_disp} يتفوق على BTC بـ {abs(_corr_diff):.1f}% — قوة نسبية"
                elif _corr_diff < -2:
                    _btc_corr_txt = f"\n• 🔴{_sym_disp} أضعف من BTC بـ {abs(_corr_diff):.1f}% — ضعف نسبي"
                else:
                    _btc_corr_txt = f"\n• ⚪{_sym_disp} يتحرك مع السوق (BTC: {_btc_chg:+.1f}%)"
            except Exception:
                pass

        # EMA_sig (#2260/#2320): استبدال EMA50 بالقيمة الخام للأصول المنهارة
        _regime_fmt = engine.regime_detector.format_ar(regime)
        if _ema50_sig_corrupted and "_e50_sig_raw" in dir() and _e50_sig_raw > 0:
            import re as _re_ema
            _pve50_raw_sig = (price - _e50_sig_raw) / max(_e50_sig_raw, 0.0001) * 100
            _regime_fmt = _re_ema.sub(
                r"السعر vs EMA50: [+\-]?\d+\.?\d*%",
                f"السعر vs EMA50: {_pve50_raw_sig:+.1f}%",
                _regime_fmt
            )
        parts = [
            _clean_md(engine.signal_layer.format_ar(signal)),
            # regime وstrategy مدمجان في pro_block — نُضيف حالة السوق فقط
            _clean_md(_regime_fmt),
        ]
        if show_risk:
            parts.append(_risk_text)
        parts.append(_clean_md(pro_block))
        if fib_lines:
            parts.append(_clean_md("\n".join(fib_lines)))
        # إصلاح #219/#220: إشارة Futures فقط إذا مؤهل + futures_enabled في التداول الآلي
        # وتوضيح نوع الصفقة (شراء Long / بيع Short) بدقة
        try:
            user_id_sig = user_id
            tt_sig      = getattr(signal, "trade_type", "spot")
            tier_sig    = _sm.get_tier(user_id_sig)
            # إصلاح #219: شرط futures_enabled — لا تظهر إلا إن فعَّل المستخدم Futures
            fut_enabled = _sm.get_futures_enabled(user_id_sig)
            if (tt_sig in ("futures_long", "futures_short")
                    and tier_sig in ("gold","diamond","admin")
                    and fut_enabled):
                fut_atr  = _calc_atr(candles) / 100 if candles else 0.03
                fut_dir  = "long" if tt_sig == "futures_long" else "short"
                # إصلاح #220: توضيح نوع الصفقة بدقة (Long=شراء / Short=بيع)
                fut_dir_ar = "📈 شراء (Long)" if fut_dir=="long" else "📉 بيع (Short)"
                if fut_dir == "long":
                    fut_tp = price * (1 + fut_atr * 2)
                    fut_sl = price * (1 - fut_atr * 1.2)
                else:
                    fut_tp = price * (1 - fut_atr * 2)
                    fut_sl = price * (1 + fut_atr * 1.2)
                fut_txt = engine.risk_engine.format_futures_signal_ar(
                    symbol, fut_dir, price, fut_tp, fut_sl, leverage=1)
                parts.append(_clean_md(
                    f"🔮 *توصية Futures: {fut_dir_ar}*\n" + fut_txt))
        except Exception as _fe:
            logger.debug(f"futures display: {_fe}")

        # BB1c: تحذير بيانات مشوهة عند انهيار حاد
        if _data_corrupted:
            # P1 (#2260): EMA50 الخام (قبل cap) في تحذير /signal
            _pve50_sig_display = (
                (price - _e50_sig_raw) / max(_e50_sig_raw, 0.0001) * 100
                if "_e50_sig_raw" in dir() and _e50_sig_raw > 0
                else _pve50_chk
            )
            warning = (
                "\n\n⚠️ *تحذير مالي: بيانات غير موثوقة*\n"
                f"• السعر انهار بشكل حاد — المؤشرات التقنية مشوهة\n"
                f"• ATR={atr_pct:.1f}% | RSI={rsi:.0f} | EMA50={_pve50_sig_display:.0f}%\n"
                "• *لا تعتمد على هذا التحليل للتداول الفعلي*"
            )
        # NYSE_hours_fix: تنبيه خارج ساعات التداول بتوقيت المستخدم
        try:
            _sm_sig = context.bot_data.get("subscription_manager")
            _tz_sig = int((_sm_sig.get_user_data(user_id) or {}).get("tz_offset", 3)) if _sm_sig else 3
        except Exception:
            _tz_sig = 3
        _nyse_warn_sig = _get_market_hours_warning(symbol, _tz_sig)
        if _nyse_warn_sig:
            warning = f"\n\n{_nyse_warn_sig}" + (warning or "")

        # FA: إضافة BTC correlation إذا وُجد
        if _btc_corr_txt:
            parts.append(_clean_md(_btc_corr_txt.strip()))
        full_text = "\n\n".join(parts) + warning
        # تطوير #188 (Phase 2): إلحاق فقرة الزوج الإضافية إن وُجدت
        _pair_addon = await build_pair_addon_lines(resolution, engine.data_layer)
        if _pair_addon:
            full_text += "\n" + "\n".join(_pair_addon)
        # تطوير #209: ملاحظة Perp للأصول المُرمَّزة
        if _is_perp_sig:
            # TK_label_fix: اعرض نوع السوق الصحيح
            _mkt_label_display = "Spot" if not _use_futures else "Perpetual"
            full_text += f"\n\n📌 *{_display_symbol}* — أصل مُرمَّز ({_mkt_label_display}) على OKX"
            # T25b_fix: تحذير Synthetic في /signal
            _is_x_sig = _display_symbol.upper().startswith("X") and len(_display_symbol) > 2
            if _is_x_sig:
                full_text += (
                    f"\n⚠️ *تحذير:* {_display_symbol} أصل اصطناعي (Synthetic) — "
                    "السيولة محدودة + لا حماية مستثمرين."
                )
                # X3_fix: تحذير بيانات محدودة إذا كان len(candles) < 50
                if len(candles) < 50:
                    full_text += (
                        f"\n⚠️ *ملاحظة:* بيانات محدودة ({len(candles)} يوم) — "
                        "التحليل تقديري وليس نهائياً."
                    )
        # إصلاح #236: ربط /signal ↔ /chart للتكامل التحليلي
        # CHART_SIG_fix: استخدام _display_symbol (XSPY وليس SPY)
        full_text += f"\n📊 للتحليل البصري: /chart {_display_symbol}"
        # تحذير المجاني
        if _free_warning:
            full_text += _free_warning
        # T3+T10_fix: تعريف _tier_sig و_is_stock_sig في نطاق cmd_signal
        _tier_sig     = tier2  # الباقة من cmd_signal
        _is_stock_sig = _is_perp_sig  # الأصل مُرمَّز؟
        # تنبيه الأسهم المحظورة
        if _is_stock_sig and _STOCK_TIER_CONF.get(_tier_sig) is None:
            full_text += (f"\n\n🔒 *الأسهم المُرمَّزة — ذهبي وأعلى*"
                         f"\nقم بالترقية للوصول: /upgrade")
        await msg.edit_text(full_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_signal: {e}")
        await msg.edit_text(f"❌ خطأ في تحليل {symbol}. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /backtest
# ════════════════════════════════════════════════════════════════
@require_tier("backtest")
async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args     = context.args or []
    symbol   = args[0].upper() if args else "BTC"
    valid    = ["trend_following", "mean_reversion", "breakout", "hybrid"]

    _auto_strategy_note = ""
    if len(args) > 1:
        strategy = args[1].lower()
        if strategy not in valid:
            await _get_message(update, context).reply_text(
                "⚠️ الاستراتيجيات المتاحة:\n"
                "• trend_following (الاتجاه — افتراضي)\n"
                "• mean_reversion (الارتداد)\n"
                "• breakout (الاختراق)\n"
                "• hybrid (مدمج EMA+RSI)\n\n"
                "مثال: /backtest BTC trend_following")
            return
    else:
        # إصلاح #17: اختيار الاستراتيجية تلقائياً حسب حالة السوق الحالية
        strategy = "trend_following"  # افتراضي احتياطي
        try:
            from core.regime_detector import Regime
            _rc, _rf = await asyncio.gather(
                engine.data_layer.get_ohlcv(symbol, "1d", 200),
                engine.data_layer.get_fear_greed(),
                return_exceptions=True
            )
            _rc = _rc if isinstance(_rc, list) else []
            _rf_val = int((_rf or {}).get("value") or 50) if isinstance(_rf, dict) else 50
            if len(_rc) >= 30:
                _regime_b = engine.regime_detector.detect(_rc, fear_greed=_rf_val)
                _reg_map = {
                    Regime.BULL_TREND:    "trend_following",
                    Regime.BEAR_TREND:    "mean_reversion",
                    Regime.SIDEWAYS:      "mean_reversion",
                    Regime.ACCUMULATION:  "hybrid",
                    Regime.DISTRIBUTION:  "mean_reversion",
                }
                strategy = _reg_map.get(_regime_b.regime, "trend_following")
                _auto_strategy_note = (f"\n💡 استراتيجية تلقائية بناءً على حالة السوق "
                                        f"({_regime_b.description_ar})")
        except Exception as _re:
            logger.debug(f"backtest auto-strategy: {_re}")

    strategy_ar = {
        "trend_following": "اتباع الاتجاه",
        "mean_reversion":  "الارتداد للمتوسط",
        "breakout":        "الاختراق",
        "hybrid":          "مدمج EMA+RSI",
    }
    msg = await _get_message(update, context).reply_text(
        f"⏳ جاري Backtest لـ {symbol} — {strategy_ar[strategy]}\n"
        f"🔬 3 سنوات بيانات حقيقية — قد يستغرق 30-60 ثانية"
        f"{_auto_strategy_note}"
    )

    try:
        price_data = await engine.data_layer.get_historical_prices(symbol, days=1095)
        price_data = price_data if isinstance(price_data, list) else []

        # BT1_fix: Yahoo Finance fallback للأسهم المُرمَّزة
        if len(price_data) < 90:
            try:
                from core.data_layer import resolve_stock_symbol as _rss_bt
                _stk_bt = _rss_bt(symbol)
                if _stk_bt.get("is_stock") and _stk_bt.get("yahoo"):
                    _yahoo_bt = await engine.data_layer._ohlcv_yahoo(
                        _stk_bt["yahoo"], days=1095)
                    if _yahoo_bt and len(_yahoo_bt) >= 90:
                        price_data = _yahoo_bt
                        logger.info(f"BT1_fix: {symbol} ← Yahoo({_stk_bt['yahoo']}) {len(price_data)} يوم")
            except Exception as _bt_e:
                logger.debug(f"BT1_fix Yahoo: {_bt_e}")

        if len(price_data) < 90:
            # T12_fix: رسالة مخصصة لأزواج BTC
            _is_btc_pair_bt = (symbol.endswith("BTC") or symbol.endswith("ETH")) and symbol not in ("BTC","ETH")
            if _is_btc_pair_bt:
                # T12b_fix: استخراج الرمز الأساسي بشكل صحيح
                _base_bt = symbol
                for _suffix in ["BTC", "ETH", "USDT"]:
                    if _base_bt.endswith(_suffix) and _base_bt != _suffix:
                        _base_bt = _base_bt[:-len(_suffix)]
                        break
                _base_bt = _base_bt or symbol  # حماية من الفراغ
                await msg.edit_text(
                    f"⚠️ /backtest لأزواج BTC/ETH غير مدعوم\n"
                    f"البيانات التاريخية لـ {symbol} غير متاحة\n\n"
                    f"💡 جرب بدلاً من ذلك:\n"
                    f"• /backtest {_base_bt} (بوحدة USDT)\n"
                    f"• /backtest BTC")
            else:
                await msg.edit_text(
                    f"⚠️ بيانات {symbol} التاريخية غير كافية\n"
                    f"({len(price_data)} يوم متاح — الحد الأدنى 90 يوماً)\n"
                    f"أعد المحاولة بعد دقيقتين")
            return

        result = await engine.backtest_engine.run(symbol, price_data, strategy)
        if result.win_rate > 0:
            engine.drift_monitor.update_baseline(result.win_rate / 100)

        import re as _re_bt
        def _fix_md_stars(t):
            lines = t.split("\n")
            return "\n".join(
                ln.replace("*","") if ln.count("*") % 2 != 0 else ln
                for ln in lines
            )
        text = _fix_md_stars(_clean_md(engine.backtest_engine.format_ar(result)))

        # T21_fix: إضافة Sortino + Benchmark + قرار واضح
        try:
            _wr  = getattr(result, "win_rate", 0)
            _ret = getattr(result, "total_return", 0)
            _dd  = getattr(result, "max_drawdown", 0)
            _sr  = getattr(result, "sharpe_ratio", 0)
            _n   = getattr(result, "total_trades", 0)

            # حساب Sortino تقريبي
            _sortino_note = ""
            if _sr > 0:
                _sortino_approx = _sr * 1.3  # تقدير: Sortino ~ Sharpe × 1.3
                _sortino_note = f"\n• Sortino (تقديري): {_sortino_approx:.2f}"

            # Benchmark: Buy & Hold
            _bh_note = ""
            if len(price_data) > 10:
                _bh_ret = (float(price_data[-1].get("close",1)) /
                           float(price_data[0].get("close",1)) - 1) * 100
                _vs_bh = _ret - _bh_ret
                _bh_emoji = "🟢" if _vs_bh > 0 else "🔴"
                _bh_note = f"\n• vs Buy & Hold: {_bh_emoji} {_vs_bh:+.1f}%"

            # T21b_fix: قرار يراعي Benchmark أيضاً
            _vs_bh_val = _ret - _bh_ret if len(price_data) > 10 else 0
            if _n < 15 or _sr < 0:
                _decision = "🔴 *لا تتداول* — بيانات غير كافية أو أداء سلبي"
            elif _sr < 0.5 or _wr < 45:
                _decision = "🟡 *اختبار صغير* — استراتيجية ضعيفة، جرب بـ 2-5% فقط"
            elif _vs_bh_val < -10:
                # الاستراتيجية أسوأ من Buy & Hold بـ 10%+ → لا تتداول
                _decision = "🟡 *تداول بحذر* — الاستراتيجية أضعف من Buy & Hold"
            elif _sr >= 1.5 and _wr >= 55 and _vs_bh_val >= -5:
                _decision = "🟢 *تداول كامل* — استراتيجية قوية، راقب دورياً"
            else:
                _decision = "🟡 *تداول بحذر* — أداء مقبول، قلل الحجم في السوق الهابط"

            if _sortino_note or _bh_note or _decision:
                text += f"\n\n💡 *T21 — مؤشرات إضافية*"
                if _sortino_note: text += _sortino_note
                if _bh_note:      text += _bh_note
                text += f"\n\n🎯 *القرار:* {_decision}"
        except Exception as _bt21_e:
            logger.debug(f"T21_backtest: {_bt21_e}")

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"cmd_backtest: {e}")
        await msg.edit_text("❌ خطأ في Backtest. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /risk — تقييم مخاطر المحفظة
# ════════════════════════════════════════════════════════════════
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """F5: تقييم مخاطر المحفظة بناءً على السوق الحالي."""
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await _get_message(update, context).reply_text("🛡️ جاري تقييم مخاطر السوق...")
    try:
        # جلب بيانات السوق
        fear_data  = await engine.data_layer.get_fear_greed()
        btc_data   = await engine.data_layer.get_price("BTC")
        fear_val   = int((fear_data or {}).get("value", 50))
        btc_price  = float((btc_data or {}).get("price", 0))
        btc_change = float((btc_data or {}).get("change_24h", 0))

        # حساب مستوى المخاطر الكلي
        _risk_score = 50  # ابتداءً محايد
        _risk_factors = []

        if fear_val < 20:
            _risk_score -= 20
            _risk_factors.append("• 🔴 Fear & Greed منخفض جداً → خطر استمرار الهبوط")
        elif fear_val < 35:
            _risk_score -= 10
            _risk_factors.append("• 🟠 Fear & Greed في منطقة خوف")
        elif fear_val > 75:
            _risk_score -= 15
            _risk_factors.append("• 🟠 Greed مرتفع → خطر تصحيح")

        if btc_change < -5:
            _risk_score -= 15
            _risk_factors.append(f"• 🔴 BTC انخفض {btc_change:.1f}% → ضغط بيعي")
        elif btc_change > 5:
            _risk_score += 10
            _risk_factors.append(f"• 🟢 BTC ارتفع {btc_change:.1f}% → زخم إيجابي")

        # تصنيف المخاطر
        _risk_score = max(0, min(100, _risk_score))
        _risk_level = (
            "🔴 مرتفع جداً — قلل التعرض إلى 20% أو أقل"   if _risk_score < 25 else
            "🟠 مرتفع — تداول بحجم مصغَّر (30-50%)"          if _risk_score < 45 else
            "🟡 متوسط — حجم طبيعي مع وقف خسارة مُحكم"       if _risk_score < 65 else
            "🟢 منخفض — السوق مناسب للتداول الطبيعي"
        )
        _bars = int(_risk_score / 10)
        _bar  = "█" * _bars + "░" * (10 - _bars)

        text = (
            "🛡️ *تقييم مخاطر السوق — رائد*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📊 *مستوى الأمان:* {_bar} {_risk_score}%\n"
            f"⚠️ *تقييم المخاطر:* {_risk_level}\n\n"
            "📈 *عوامل المخاطر الحالية:*\n"
            + ("\n".join(_risk_factors) if _risk_factors else "• ✅ لا مخاطر بارزة") +
            f"\n\n💰 *BTC:* ${btc_price:,.0f} ({btc_change:+.1f}%)"
            f"\n😱 *Fear & Greed:* {fear_val}"
            "\n\n⚠️ هذا التقييم استرشادي — القرار للمستخدم"
        )
        await msg.edit_text(_clean_md(text), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"cmd_risk: {e}")
        await msg.edit_text("❌ خطأ في تقييم المخاطر. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /liquidity
# ════════════════════════════════════════════════════════════════
@require_tier("liquidity")
async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or ["BTC"]
    # تطبيع symbol على مستوى النظام: BTCUSDT → BTC
    raw_sym = args[0].upper().strip().replace("/", "").replace("-", "")
    for _sfx in ("USDT","BUSD","USDC"):
        if raw_sym.endswith(_sfx) and len(raw_sym) > len(_sfx):
            raw_sym = raw_sym[:-len(_sfx)]
            break
    symbol = raw_sym
    msg = await _get_message(update, context).reply_text(f"🔬 جاري تحليل السيولة لـ {symbol}...")

    try:
        profile, walls, funding, oi, whale = await asyncio.gather(
            engine.microstructure.analyze(symbol, order_size_usd=1000),
            engine.microstructure.detect_walls(symbol),
            engine.data_layer.get_funding_rate(symbol),
            engine.data_layer.get_open_interest(symbol),
            engine.data_layer.get_whale_ratio(symbol),
            return_exceptions=True
        )

        if not profile or isinstance(profile, Exception):
            await msg.edit_text(f"⚠️ بيانات السيولة لـ {symbol} غير متاحة حالياً")
            return

        # تمرير walls لـ format_ar مباشرة
        walls_safe = walls if not isinstance(walls, Exception) else None
        text = _clean_md(engine.microstructure.format_ar(profile, walls=walls_safe))

        # إصلاح #14: قسم المشتقات (Funding/OI/Whale) — كان مفقوداً تماماً
        funding = funding if isinstance(funding, dict) else {}
        oi      = oi      if isinstance(oi,      dict) else {}
        whale   = whale   if isinstance(whale,   dict) else {}
        _fund_pct  = float(funding.get("rate_pct", 0) or 0)
        _oi_chg    = float(oi.get("oi_change_pct", 0) or 0)
        _oi_sig    = oi.get("signal", "")
        _whale_sig = whale.get("signal", "")
        _whale_r   = float(whale.get("ratio", 0) or 0)

        deriv_lines = []
        if _fund_pct:
            if _fund_pct < -0.02:
                _fsig = "🟢 سالب جداً — ضغط Shorts (فرصة Long)"
            elif _fund_pct < -0.005:
                _fsig = "🟢 سالب — فرصة Long"
            elif _fund_pct > 0.02:
                _fsig = "🔴 مرتفع جداً — ضغط Longs"
            elif _fund_pct > 0.005:
                _fsig = "🟡 إيجابي — محايد"
            else:
                _fsig = "⚪ محايد"
            deriv_lines.append(f"• Funding Rate: {_fund_pct:+.4f}% {_fsig}")
        if _oi_chg:
            deriv_lines.append(f"• Open Interest: {_oi_chg:+.1f}% {_oi_sig}")
        if _whale_sig:
            _wr_txt = f" ({_whale_r:.2f})" if _whale_r > 0 else ""
            deriv_lines.append(f"• Whale Ratio (Long/Short){_wr_txt}: {_whale_sig}")

        if deriv_lines:
            text += "\n\n📐 *مشتقات*\n" + "\n".join(deriv_lines)

        # F3: Order Flow Score + توصية محسّنة
        _imb = getattr(profile, "imbalance", 0.5)
        _ofs = int(_imb * 100)
        _ofs_bars = int(_ofs / 10)
        _ofs_bar  = "█" * _ofs_bars + "░" * (10 - _ofs_bars)
        _ofs_label = (
            "🟢 تدفق شرائي قوي" if _ofs > 65 else
            "🔴 تدفق بيعي قوي"  if _ofs < 35 else
            "⚪ تدفق محايد"
        )

        _reco = []
        if _imb < 0.40:
            _reco.append("ضغط بيع قوي في الـ Order Book — توخَّ الحذر من شراء فوري")
        elif _imb > 0.60:
            _reco.append("ضغط شراء قوي — دعم محتمل قريب")
        if _fund_pct < -0.01:
            _reco.append("Funding سالب يدعم سيناريو ارتداد Long")
        elif _fund_pct > 0.02:
            _reco.append("Funding مرتفع — خطر تصفية Longs مفرطة")

        text += (
            f"\n\n📊 *Order Flow Score*"
            f"\n{_ofs_bar} {_ofs}% — {_ofs_label}"
        )
        if _reco:
            text += f"\n\n💡 *التفسير*: {' · '.join(_reco)}"

        # T18_fix v2: Slippage متعدد + قرار صحيح
        try:
            # T18a_fix: استخدام mid_price (وليس price) + bid_depth_usd
            _bid_d = getattr(profile, "bid_depth_usd", 0) or 0
            _ask_d = getattr(profile, "ask_depth_usd", 0) or 0
            _spread_pct = getattr(profile, "spread_pct", 0) or 0
            _price_liq = getattr(profile, "mid_price", 0) or getattr(profile, "bid_price", 0) or 1

            if _bid_d > 0:
                _slippage_1k  = round(_spread_pct / 2, 4)
                _slippage_10k = round(_spread_pct / 2 + (10000 / max(_bid_d, 1)) * 100, 3)
                _slippage_50k = round(_spread_pct / 2 + (50000 / max(_bid_d, 1)) * 100, 3)
                text += (
                    f"\n\n📐 *Slippage المتوقع*"
                    f"\n• $1K: ~{_slippage_1k:.4f}%"
                    f"\n• $10K: ~{min(_slippage_10k, 5.0):.3f}%"
                    f"\n• $50K: ~{min(_slippage_50k, 10.0):.3f}%"
                )

            # Volume Profile تقريبي من candles
            try:
                _liq_candles = await asyncio.wait_for(
                    engine.data_layer.get_ohlcv(symbol, "1h", 24),
                    timeout=5.0)
                if isinstance(_liq_candles, list) and len(_liq_candles) >= 10:
                    _vols = [float(c.get("volume",0)) for c in _liq_candles if c.get("volume")]
                    _closes = [float(c.get("close",0)) for c in _liq_candles if c.get("close")]
                    if _vols and _closes:
                        # POC: السعر الأكثر تداولاً
                        _poc_idx = _vols.index(max(_vols))
                        _poc = _closes[_poc_idx] if _poc_idx < len(_closes) else 0
                        _vah = max(_closes)  # Value Area High
                        _val = min(_closes)  # Value Area Low
                        if _poc > 0:
                            text += (
                                f"\n\n📊 *Volume Profile (24h)*"
                                f"\n• POC (أكثر تداولاً): ${_poc:,.4f}"
                                f"\n• VAH (أعلى نطاق): ${_vah:,.4f}"
                                f"\n• VAL (أدنى نطاق): ${_val:,.4f}"
                            )
            except Exception:
                pass

            # T18b_fix v2: قرار يراعي is_tradeable + liquidity_score
            _liq_score_raw = getattr(profile, "liquidity_score", 0) or 0
            _liq_score = _liq_score_raw * 100  # 0.81 → 81
            _is_tradeable = getattr(profile, "is_tradeable", True)
            _liq_decision = []

            if not _is_tradeable:
                # microstructure قرر عدم التداول → لا تناقض مع T18b
                _liq_decision.append("⛔ غير موصى بالتداول — سيولة غير كافية")
            elif _liq_score >= 60:
                _liq_decision.append("✅ سيولة كافية للتداول")
            elif _liq_score >= 40:
                _liq_decision.append("🟡 سيولة متوسطة — قلل الحجم")
            else:
                _liq_decision.append("🔴 سيولة منخفضة — خطر Slippage")

            if _is_tradeable:
                if _ofs > 60 and _liq_score >= 50:
                    _liq_decision.append("دعم: ارتداد محتمل")
                elif _ofs < 40:
                    _liq_decision.append("لا تدخل ضد الضغط البيعي")

            if _liq_decision:
                text += f"\n\n🎯 *القرار:* {' · '.join(_liq_decision)}"

        except Exception as _t18e:
            logger.debug(f"T18_fix: {_t18e}")

        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_liquidity: {e}")
        await msg.edit_text("❌ خطأ في تحليل السيولة. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /events
# ════════════════════════════════════════════════════════════════
@require_tier("events")
async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    try:
        state   = engine.event_risk.assess()
        # #716: توحيد النافذة الزمنية — نفس ما يستخدمه assess()
        _ev_window = 168  # أسبوع كامل
        text_ev = engine.event_risk.format_upcoming_ar(hours=_ev_window)
        import re as _re
        text_ev = _re.sub(r'(بعد\s*)(\d+)(ساعة)', r'بعد \2 ساعة', text_ev)
        lines   = [
            "📅 *فلتر مخاطر الأحداث — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            state.message_ar,
            "",
            text_ev,
        ]
        await _get_message(update, context).reply_text(
            _clean_md("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_events: {e}")
        await _get_message(update, context).reply_text("❌ خطأ في جلب الأحداث. حاول لاحقاً")




# ════════════════════════════════════════════════════════════════
# /market_outlook — T38: رؤية المؤسسات الكبرى
# ════════════════════════════════════════════════════════════════
@require_tier("market_outlook")
async def cmd_outlook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """T38: تقرير رؤية المؤسسات — BlackRock + Vanguard + Morningstar"""
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await _get_message(update, context).reply_text("🔍 جاري جلب رؤية المؤسسات...")

    try:
        # T38_fix: جلب ملخصات المؤسسات عبر Groq
        _groq_key = engine.config.get("GROQ_API_KEY", "")
        _outlook_parts = []

        if _groq_key:
            import urllib.request, urllib.error, json as _json_out

            _prompt_out = """أنت محلل مالي خبير. قدم ملخصاً موجزاً (3-4 جمل) لرؤية كل من:
1. BlackRock: توقعات الأسواق والأصول الرقمية للربع الحالي
2. Vanguard: التخصيص طويل الأجل وتوقعات العائد
3. Morningstar: تحليل مستقل للصناديق والأصول

اكتب كل ملخص بالعربية فقط، موجزاً ومفيداً للمتداول.
تنسيق الإجابة:
🏦 BlackRock: [ملخص]
📊 Vanguard: [ملخص]
🔍 Morningstar: [ملخص]"""

            _body_out = _json_out.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "أجب بالعربية فقط. لا تستخدم كلمات إنجليزية في الجمل العربية."},
                    {"role": "user", "content": _prompt_out}
                ],
                "max_tokens": 500,
                "temperature": 0.3
            }).encode()

            _req_out = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=_body_out,
                headers={"Authorization": f"Bearer {_groq_key}", "Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(_req_out, timeout=20) as _r_out:
                    _d_out = _json_out.loads(_r_out.read())
                    _outlook_text = _d_out["choices"][0]["message"]["content"].strip()
                    _outlook_parts.append(_outlook_text)
            except Exception as _oe:
                logger.warning(f"market_outlook Groq: {_oe}")

        # بناء النص النهائي
        _parts_out = [
            "🌍 *رؤية المؤسسات الكبرى — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            "",
        ]

        if _outlook_parts:
            _parts_out.extend(_outlook_parts)
        else:
            _parts_out += [
                "🏦 *BlackRock:* يُركز على تنويع المحافظ مع ميل للأصول الحقيقية في ظل التضخم.",
                "📊 *Vanguard:* يوصي بالاستثمار طويل الأجل في مؤشرات متنوعة مع تقليل التكاليف.",
                "🔍 *Morningstar:* يُحذر من التقييمات المرتفعة في الأسهم الأمريكية وينصح بالتنويع الجغرافي.",
            ]

        _parts_out += [
            "",
            "━━━━━━━━━━━━━━━━━━",
            "💡 *كيف يُستخدم في التداول؟*",
            "• BlackRock → تخصيص استراتيجي بعيد المدى",
            "• Vanguard → معيار العائد المتوقع للمحفظة",
            "• Morningstar → تحليل مستقل للمخاطر",
            "",
            "⚠️ رأي استرشادي — القرار النهائي للمستخدم",
            "🤖 رائد التداول الذكي",
        ]

        await msg.edit_text(
            _clean_md("\n".join(_parts_out)),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"cmd_market_outlook: {e}")
        await msg.edit_text("❌ خطأ في جلب رؤية المؤسسات. حاول لاحقاً.")

# ════════════════════════════════════════════════════════════════
# /drift
# ════════════════════════════════════════════════════════════════
@require_tier("drift")
async def cmd_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return
    try:
        state = engine.drift_monitor.assess()
        text  = _clean_md(engine.drift_monitor.format_ar(state))
        await _get_message(update, context).reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_drift: {e}")
        await _get_message(update, context).reply_text("❌ خطأ في تحليل النموذج. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /analyze — ذهبي+
# ════════════════════════════════════════════════════════════════
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق من صلاحية الباقة
    if not _sm.can_use_command(user_id, "analyze"):
        await _get_message(update, context).reply_text(
            "🔒 *التحليل العميق — ذهبي وماسي فقط*\n\n"
            "هذا الأمر يتطلب باقة ذهبي أو أعلى.\n"
            "للترقية: /upgrade",
            parse_mode="Markdown"
        )
        return

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args   = context.args or []
    raw_arg = args[0].upper()
    # TK_name_fix: حفظ الرمز الأصلي للعرض
    _display_symbol_an = raw_arg

    # تطوير #221: سؤال نوع السوق (Spot/Futures)
    # إصلاح #237-A: الأصول المُرمَّزة → Futures تلقائياً بدون سؤال
    _mkttype_an = context.user_data.pop("_mkttype", None)
    if _mkttype_an is None:
        tier_an_pre = _sm.get_tier(user_id)
        _pre_asset_an = False
        if tier_an_pre in ("diamond","admin"):
            try:
                _pre_asset_an = await engine.data_layer.is_tokenized_stock(raw_arg)
            except Exception:
                _pre_asset_an = False
        # TK_ROOT_fix: X-prefix = Spot مُرمَّز → لا نُجبر Futures
        _is_x_an = raw_arg.upper().startswith("X") and len(raw_arg) > 2
        if _pre_asset_an and not _is_x_an:
            _mkttype_an = "futures"
        else:
            sent_an = await _ask_market_type(update, context, "analyze", raw_arg, tier_an_pre)
            if sent_an:
                return
    _use_futures_an = (_mkttype_an == "futures")
    _mkt_arg_an = "futures" if _use_futures_an else "spot"  # إصلاح #258

    # TK4/TK1b: التحقق من Spot (X-prefix يتجاوز مباشرة)
    if not _use_futures_an:
        if raw_arg.upper().startswith("X") and len(raw_arg) > 2:
            # TK4_fix: XSPCX/XAMZN أصول Spot مؤكدة — تجاوز check
            from core.data_layer import resolve_stock_symbol as _rss_an
            _stock_res_an = _rss_an(raw_arg, "spot")
            _resolve_sym = _stock_res_an.get("base", raw_arg[1:])  # XSPCX → SPCX
        else:
            try:
                _spot_chk_an = await engine.data_layer.check_spot_available(raw_arg)
                if not _spot_chk_an.get("available", True):
                    await _get_message(update, context).reply_text(
                        _spot_chk_an.get("message", f"⚠️ {raw_arg} غير متاح في Spot"),
                        parse_mode="Markdown"
                    )
                    return
                _an_sym_actual = _spot_chk_an.get("spot_symbol", raw_arg)
                if _an_sym_actual != raw_arg.upper():
                    from core.data_layer import resolve_stock_symbol as _rss_an
                    _stock_res_an = _rss_an(_an_sym_actual, "spot")
                    _resolve_sym = _stock_res_an.get("base", raw_arg)
                    raw_arg = _an_sym_actual
                else:
                    _resolve_sym = raw_arg
            except Exception:
                _resolve_sym = raw_arg

    # تطوير #188 (Phase 2): دعم أزواج BTC/ETH — فقرة إضافية في نهاية
    # التقرير إن كانت الباقة ماسي+ والزوج متوفر (build_pair_addon_lines)
    tier_an    = _sm.get_tier(user_id)
    # TK1b_fix: استخدام الرمز الأساسي لـ resolve_symbol
    _sym_for_resolve_an = locals().get("_resolve_sym", raw_arg)
    resolution = await resolve_symbol(_sym_for_resolve_an, tier_an, engine.data_layer)
    symbol     = resolution.base

    # TK_Spot_fix: للأصول X-prefix في Spot → استخدام XSPCX لجلب البيانات
    if not _use_futures_an and raw_arg.upper().startswith("X") and len(raw_arg) > 2:
        _spot_data_symbol_an = raw_arg.upper()  # XSPCX للـ OKX API
    else:
        _spot_data_symbol_an = symbol

    # تطوير #209: اكتشاف تلقائي للأسهم المُرمَّزة (ماسي+ فقط)
    _is_perp_an = False
    if tier_an in ("diamond", "admin"):
        try:
            _is_perp_an = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            _is_perp_an = False

    # فحص الباقة للعملة المطلوبة
    _LARGE_CAPS = {"XLM","ICP","FIL","VET","EOS","XTZ","ALGO","HBAR","EGLD","WAVES","NEO","QTUM"}
    # إصلاح #1683: diamond/admin وصول كامل لجميع العملات
    if tier_an in ("diamond", "admin"):
        pass  # ماسي+ = بدون قيود
    elif tier_an in ("gold",) and symbol.upper() in _LARGE_CAPS:
        pass  # ذهبي: عملات كبيرة مسموحة
    elif _is_perp_an and tier_an in ("gold",):
        pass  # ذهبي: أصول مُرمَّزة مسموحة
    elif not is_symbol_allowed(symbol, tier_an):
        await _get_message(update, context).reply_text(
            (
                f"⛔ *{symbol}* غير متاحة لباقتك الحالية\n\n"
                f"باقتك: {_sm.get_tier_name(user_id)}\n"
                f"هذه العملة تتطلب باقة أعلى\n\n"
                f"⬆️ للترقية: /upgrade\n"
                f"📋 لعرض عملاتك المتاحة: /premium"
            ), parse_mode="Markdown"); return
    if not symbol:
        await _get_message(update, context).reply_text(
            "📊 مثال الاستخدام: /analyze BTC\n"
            "أو: /analyze ETH"
        )
        return

    msg = await _get_message(update, context).reply_text(f"🧠 جاري التحليل العميق لـ {symbol}...\n⏳ قد يستغرق 1-3 دقائق — يُرجى الانتظار")

    # إصلاح #178: semaphore يمنع تزامن أكثر من 3 أوامر ثقيلة
    _heavy_sem = await engine.acquire_heavy()
    await _heavy_sem.acquire()
    try:
        # M#119: timeout صارم لمنع التجمد
        try:
            if _is_perp_an:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price_perp(symbol),
                        engine.data_layer.get_ohlcv_perp(symbol, 365),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
            else:
                # RSI_Fib_fix: X-prefix يستخدم 90 يوم من OKX (لا Yahoo طويل المدى)
                _an_days = 90 if (
                    _spot_data_symbol_an.upper().startswith("X") and
                    len(_spot_data_symbol_an) > 2
                ) else 365
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        # TK_Spot_fix: XSPCX لجلب السعر والبيانات في Spot
                        engine.data_layer.get_price(_spot_data_symbol_an, mkttype=_mkt_arg_an),
                        engine.data_layer.get_ohlcv(_spot_data_symbol_an, "1d", _an_days, mkttype=_mkt_arg_an),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
        except asyncio.TimeoutError:
            await msg.edit_text(
                f"⏱️ انتهت مهلة تحليل *{symbol}*\n\n"
                f"جرّب: /quicksignal {symbol}",
                parse_mode="Markdown")
            return
        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0

        # retry للعملات الصغيرة خارج top100
        if len(candles) < 10:
            logger.info(f"analyze: retry OHLCV for {symbol}")
            await asyncio.sleep(1)
            retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 60)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or
                           price_d.get("price_change_percentage_24h") or 0)
        volume_24h = float(price_d.get("volume_24h") or 0)
        market_cap = float(price_d.get("market_cap") or 0)

        if price <= 0:
            # إصلاح #87/88: fallback لآخر إغلاق من candles قبل البحث في CoinGecko
            # (BGB وعملات مشابهة: get_price فشل لكن get_ohlcv نجح — /signal كان يعمل)
            if len(candles) >= 1:
                try:
                    _last_close = float(candles[-1].get("close") or 0)
                    if _last_close > 0:
                        price = _last_close
                        if change_24h == 0 and len(candles) >= 2:
                            _prev_close = float(candles[-2].get("close") or 0)
                            if _prev_close > 0:
                                change_24h = (price - _prev_close) / _prev_close * 100
                except Exception:
                    pass

        if price <= 0:
            # M#113: بحث تلقائي في CoinGecko
            try:
                new_id = await engine.data_layer._search_coingecko(symbol)
                if new_id:
                    price_d2 = await engine.data_layer.get_price(symbol)
                    price = float((price_d2 or {}).get("price") or 0)
            except Exception:
                pass
            if price <= 0:
                await msg.edit_text(
                    f"⚠️ لم أجد سعراً لـ *{symbol}*\n\n"
                    f"• تأكد من صحة الرمز (مثال: BTC, ETH, SOL)\n"
                    f"• العملة قد تكون جديدة أو غير مدعومة\n"
                    f"• جرّب: /chart {symbol} للتحليل البصري",
                    parse_mode="Markdown")
                return

        rsi = _calc_rsi(candles)

        # DD1/BB1b/EE2 (#1891/#1905/#1922): فحص جودة البيانات في /analyze
        _atr_an     = _calc_atr(candles)
        _pve50_an   = 0.0
        if len(candles) >= 50:
            try:
                _cls_an = [float(c.get("close", 0)) for c in candles if c.get("close")]
                _e50_an_raw = sum(_cls_an[-50:]) / 50 if len(_cls_an) >= 50 else _cls_an[-1]
                # EE2: cap ema50 بـ price × 3 لمنع قيم تاريخية مشوّهة
                _e50_an = _e50_an_raw if _e50_an_raw <= price * 3.0 else price
                _pve50_an = (price - _e50_an) / max(_e50_an, 0.0001) * 100 if _e50_an > 0 else 0.0
                # FF3 (#2082): كشف الفساد من EMA50_raw (قبل cap)
                _ema50_corrupted = (_e50_an_raw > price * 3.0)
            except Exception:
                _ema50_corrupted = False
        _data_corrupted_an = (_atr_an > 25 or rsi < 5 or _pve50_an < -50 or _ema50_corrupted)
        # EE2 (#1922): تقييد conf ورافعة في /analyze عند بيانات مشوهة
        if _data_corrupted_an:
            # نحتاج تقييد الـ signal الذي سيُبنى لاحقاً
            _analyze_conf_cap = 0.49  # → [LOW] أقصاه
            _analyze_lev_cap  = 1     # → 1x دائماً
        else:
            _analyze_conf_cap = 1.0
            _analyze_lev_cap  = None

        # حساب regime + EMA (مصدر واحد للحقيقة)
        regime_desc = "⚪ جاري تحديث بيانات السوق"
        is_bearish  = False
        ema_bearish = False
        if len(candles) >= 10:  # خُفِّض: 30 → 10
            try:
                regime_obj  = engine.regime_detector.detect(
                    candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=symbol)
                regime_desc = regime_obj.description_ar
                is_bearish  = "هابط" in regime_desc
            except Exception as e:
                logger.warning(f"regime detect: {e}")

        # EMA check
        if len(candles) >= 20:
            try:
                closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
                ema20  = sum(closes[-20:]) / 20
                _ema50_raw = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
                # EE1 (#1956/#2025): cap ema50 بـ price × 3 لمنع قيم تاريخية مشوّهة
                ema50 = _ema50_raw if _ema50_raw <= price * 3.0 else ema20
                # GG2: توحيد منطق ema_bearish — السعر تحت EMA20 أو EMA50 (ليس كليهما)
                # هذا يتوافق مع Header الذي يُظهر "تحت" عند السعر < EMA50 فقط
                ema_bearish = price < ema50
            except Exception:
                pass

        # اتجاه العملة
        trend = ("هابط" if is_bearish or ema_bearish
                 else "صاعد" if not is_bearish and not ema_bearish and rsi > 50
                 else "محايد")

        # candles_summary كـ JSON احترافي لـ Groq
        candles_summary = engine.data_layer.build_candles_summary(candles, symbol)
        if not candles_summary:
            # fallback نصي
            try:
                p5 = [float(c.get("close", 0) or 0) for c in candles[-5:] if c]
                p5 = [p for p in p5 if p > 0]
            except Exception:
                p5 = []
            if len(p5) >= 2 and p5[-1] > p5[0]:
                candles_summary = "آخر 5 شموع: اتجاه صاعد"
            elif len(p5) >= 2:
                candles_summary = "آخر 5 شموع: اتجاه هابط"
            else:
                candles_summary = "بيانات الشموع غير كافية"

        # جلب Derivatives + On-chain + 4H بالتوازي (المرحلة 2)
        try:
            _oi_data, _fund_data, _whale_data, _candles_4h, _onchain_an = await asyncio.wait_for(
                asyncio.gather(
                    engine.data_layer.get_open_interest(symbol),
                    engine.data_layer.get_funding_rate(symbol),
                    engine.data_layer.get_whale_ratio(symbol),
                    engine.data_layer.get_ohlcv_4h(symbol, 50),
                    engine.data_layer.get_onchain(),
                    return_exceptions=True,
                ), timeout=15.0
            )
        except Exception:
            _oi_data = _fund_data = _whale_data = _onchain_an = {}
            _candles_4h = []
        _oi_data     = _oi_data     if isinstance(_oi_data, dict)    else {}
        _fund_data   = _fund_data   if isinstance(_fund_data, dict)   else {}
        _whale_data  = _whale_data  if isinstance(_whale_data, dict)  else {}
        _candles_4h  = _candles_4h  if isinstance(_candles_4h, list)  else []
        _onchain_an  = _onchain_an  if isinstance(_onchain_an, dict)  else {}
        _oi_data    = _oi_data    if isinstance(_oi_data, dict)    else {}
        _fund_data  = _fund_data  if isinstance(_fund_data, dict)  else {}
        _whale_data = _whale_data if isinstance(_whale_data, dict) else {}

        # بناء السيناريوهات من البيانات الحقيقية قبل Groq
        _fib_for_ctx  = _calc_fibonacci(candles)
        _atr_for_ctx  = _calc_atr(candles)
        # T2_fix: تمرير عتبة الباقة للـ scenarios context
        _t_entry_an = _TIER_CONF.get(tier_an, _TIER_CONF["silver"])[1]
        _scenarios_ctx = _build_scenarios_context(
            price     = price,
            atr_pct   = _atr_for_ctx,
            fib       = _fib_for_ctx,
            rsi       = rsi,
            is_bear   = is_bearish,
            threshold = _t_entry_an / 100,
        )
        # دمج السيناريوهات مع candles_summary
        _full_context = f"{candles_summary}\n{_scenarios_ctx}".strip() if candles_summary else _scenarios_ctx

        try:
            # M2b: تمرير market_phase الصحيح من regime_obj
            _mp_for_groq = getattr(regime_obj, "market_phase", "") if "regime_obj" in dir() else ""
            _mp_ar_for_groq = _get_market_phase_ar(_mp_for_groq) if _mp_for_groq else ""
            # DD1b: تقييد confidence عند بيانات مشوهة
            if _data_corrupted_an:
                _full_context_an = _full_context + " [تحذير: بيانات مشوهة — اذكر ذلك في التحليل]"
            else:
                _full_context_an = _full_context
            analysis = await engine.news_engine.analyze_symbol(
                symbol=symbol, price=price, price_change_24h=change_24h,
                volume_24h=volume_24h, market_cap=market_cap, rsi=rsi,
                fear_greed=fear_val, regime_desc=regime_desc,
                candles_summary=_full_context_an,
                ema_bearish=ema_bearish,
                market_phase=_mp_ar_for_groq)
            if not analysis or len(analysis.strip()) < 20:
                raise ValueError("تحليل فارغ")
        except Exception as _ae:
            logger.error(f"analyze_symbol ({symbol}): {_ae}")
            analysis = (f"📊 تحليل {symbol}\n"
                       f"السعر: {_fmt_price(price)} ({change_24h:+.2f}%)\n"
                       f"RSI: {int(rsi)} | السوق: {regime_desc}")

        change_sign = "+" if change_24h >= 0 else ""
        # حساب مستويات دخول/خروج من ATR
        atr_pct = _calc_atr(candles) / 100 if candles else 0.03
        rsi_lbl = _rsi_label(rsi)
        contradiction = _market_contradiction(rsi, fear_val, regime_desc)

        # levels_lines مُدمجة في الهيكل الاحترافي — لا حاجة لها هنا
        levels_lines = []

        # إصلاح #375/#390: استخدام signal_layer.generate الحقيقي
        fib_a  = _calc_fibonacci(candles)
        _atr_a = _calc_atr(candles)
        # إصلاح #34: onchain_data خاص بالعملة بدل {} الفارغة
        _onchain_a = await engine.data_layer.get_signal_enrichment(symbol, {})
        try:
            _sig_a = engine.signal_layer.generate(
                symbol=symbol, candles=candles,
                onchain_data=_onchain_a,
                news_sentiment=float(getattr(engine, "_last_news_sentiment", 0) or 0),
                backtest_win_rate=0.55,
                macro_data={"fear_greed": fear_val},
                regime=engine.regime_detector.detect(
                    candles, btc_dominance=float(btc_dom or 50), fear_greed=fear_val, symbol=symbol),
            )
            # إضافة بيانات Derivatives + 4H لـ technicals
            if hasattr(_sig_a, "technicals") and isinstance(_sig_a.technicals, dict):
                _sig_a.technicals["oi_data"]     = _oi_data
                _sig_a.technicals["fund_data"]   = _fund_data
                _sig_a.technicals["whale_data"]  = _whale_data
                _sig_a.technicals["atr_value"]   = round(_calc_atr(candles) * price / 100, 2)
                _sig_a.technicals["candles_4h"]   = _candles_4h
                _sig_a.technicals["onchain_data"]  = _onchain_an
                # BB من 4H إذا متاح
                if len(_candles_4h) >= 20:
                    _closes_4h = [float(c.get("close",0)) for c in _candles_4h]
                    _sig_a.technicals["bb_pos"] = _calc_bb_pos(_closes_4h)
        except Exception as _se:
            logger.debug(f"signal_layer in analyze: {_se}")
            class _AnalyzeSignal:
                confidence = 0.55
                direction  = "neutral"
                trade_type = "spot"
                technicals = {}
            _sig_a = _AnalyzeSignal()
            _scenario_fb = (
                "counter_trend_bounce" if rsi < 20 and fear_val < 25
                else "trend_continuation"
            )
            if rsi < 20:
                _sig_a.direction  = "long"
                _sig_a.confidence = 0.70
            elif rsi < 35:
                _sig_a.direction  = "long"
                _sig_a.confidence = 0.60
            elif rsi > 70:
                _sig_a.direction  = "short"
                _sig_a.confidence = 0.60
            # تعيين scenario في technicals للـ fallback
            _sig_a.technicals = {
                "scenario":      _scenario_fb,
                "scenario_ar":   (
                    "⚡ ارتداد مؤقت (Counter-trend)" if _scenario_fb == "counter_trend_bounce"
                    else "📉 استمرار الاتجاه الهابط" if is_bearish
                    else "📈 استمرار الاتجاه الصاعد"
                ),
                "scenario_warn": "⚡ ذروة بيع — scalp فقط، وقف صارم" if _scenario_fb == "counter_trend_bounce" else "📉 الاتجاه هابط",
                "max_size_pct":  0.12 if _scenario_fb == "counter_trend_bounce" else 0.20,
                "target_mult":   1.5  if _scenario_fb == "counter_trend_bounce" else 2.0,
                "oi_data": _oi_data, "fund_data": _fund_data,
                "whale_data": _whale_data, "onchain_data": _onchain_an,
                "atr_value": round(_calc_atr(candles) * price / 100, 2),
            }
        class _AnalyzeRegime:
            description_ar = regime_desc
            market_phase   = getattr(regime_obj, "market_phase", "") if "regime_obj" in dir() else (
                "Markdown" if is_bearish else "Markup")
            class regime:
                value = "bear_trend" if is_bearish else "bull_trend"
        _reg_a = _AnalyzeRegime()
        # FA_an: BTC Correlation في /analyze
        _btc_corr_an = ""
        if symbol not in ("BTC", "BITCOIN") and _atr_a < 20:
            try:
                _btc_d_an   = await engine.data_layer.get_price("BTC")
                _btc_chg_an = float((_btc_d_an or {}).get("change_24h", 0) or 0)
                _sym_d_an   = await engine.data_layer.get_price(symbol)
                _sym_chg_an = float((_sym_d_an or {}).get("change_24h", 0) or 0)
                _corr_diff_an = _sym_chg_an - _btc_chg_an
                # FA_missing_fix: إظهار FA دائماً في /analyze
                _sym_an_disp = f" {_display_symbol_an if _display_symbol_an else symbol}"
                if _corr_diff_an > 2:
                    _btc_corr_an = f"\n• 🟢{_sym_an_disp} يتفوق على BTC بـ {abs(_corr_diff_an):.1f}% — قوة نسبية"
                elif _corr_diff_an < -2:
                    _btc_corr_an = f"\n• 🔴{_sym_an_disp} أضعف من BTC بـ {abs(_corr_diff_an):.1f}% — ضعف نسبي"
                else:
                    _btc_corr_an = f"\n• ⚪{_sym_an_disp} يتحرك مع السوق (BTC: {_btc_chg_an:+.1f}%)"
            except Exception:
                pass

        # GG1 (#1945/#1933/#2007/#2080/#2100): تمرير Market Phase الصحيح لـ _build_professional_block
        _mp_for_block = _mp_ar_for_groq if _mp_ar_for_groq else ""
        if _mp_for_block and hasattr(_reg_a, "market_phase"):
            # تحويل الـ ar إلى النوع الإنجليزي المفهوم
            _mp_en_map = {"هبوط": "Markdown", "صعود": "Markup",
                          "تراكم": "Accumulation", "توزيع": "Distribution", "تعزيز": "Consolidation"}
            for _ar_key, _en_val in _mp_en_map.items():
                if _ar_key in _mp_for_block:
                    _reg_a.market_phase = _en_val
                    break
        # EE2b: تطبيق cap على conf في /analyze عند بيانات مشوهة
        if _data_corrupted_an:
            _sig_a.confidence = min(getattr(_sig_a, "confidence", 0.49), _analyze_conf_cap)
            if hasattr(_sig_a, "suggested_leverage"):
                _sig_a.suggested_leverage = _analyze_lev_cap or 1
        # conf_reason_fix_an: تطبيق conf_boost على _sig_a.confidence
        # حتى تعمل conf_reason_fix وتُعرض الثقة الصحيحة في الأسباب
        _sig_a_conf_original = getattr(_sig_a, "confidence", 0.5)
        # احسب conf_boost لـ /analyze
        try:
            _an_rsi = rsi
            _an_dir = getattr(_sig_a, "direction", "neutral")
            _an_div = (_sig_a.technicals or {}).get("rsi_divergence", "none") if hasattr(_sig_a, "technicals") else "none"
            _an_flags = 0
            if hasattr(_sig_a, "technicals") and isinstance(_sig_a.technicals, dict):
                _oi_chk = _sig_a.technicals.get("oi_data", {})
                _fund_chk = _sig_a.technicals.get("fund_data", {})
                _whale_chk = _sig_a.technicals.get("whale_data", {})
                _bb_chk = _sig_a.technicals.get("bb_pos", 0.5)
                if float((_fund_chk or {}).get("funding_rate", 0) or 0) < -0.01: _an_flags += 1
                whale_r = float((_whale_chk or {}).get("whale_ratio", 1.0) or 1.0)
                if whale_r > 1.5 and _an_dir == "long": _an_flags += 1
                elif whale_r < 0.7 and _an_dir == "short": _an_flags += 1
                if _bb_chk > 0.8 or _bb_chk < 0.2: _an_flags += 1
            _an_boost = _an_flags * 3
            if _an_rsi > 80 and _an_dir == "long": _an_boost = max(0, _an_boost - 3)
            if _an_div == "bearish" and _an_dir == "long": _an_boost = 0
            if _an_div == "bullish" and _an_dir == "short": _an_boost = 0
            if _an_boost > 0:
                _sig_a.confidence = min(_sig_a_conf_original + _an_boost / 100, 0.85)
        except Exception:
            pass
        pro_block_a, _ = _build_professional_block(
            symbol, price, _sig_a, _reg_a, candles, rsi, _atr_a, fib_a,
            tech_extra={"is_perp_asset": _is_perp_an} if _is_perp_an else {},
            tier=tier_an,
            is_stock=_is_perp_an)
        fib_lines_a = _fmt_fib_lines(fib_a, price)

        # NYSE_hours_fix: تنبيه ساعات السوق في cmd_analyze
        try:
            _sm_an2 = context.bot_data.get("subscription_manager")
            _uid_an2 = user_id
            _tz_an2 = int((_sm_an2.get_user_data(_uid_an2) or {}).get("tz_offset", 3)) if _sm_an2 else 3
        except Exception:
            _tz_an2 = 3
        _nyse_warn_an = _get_market_hours_warning(symbol, _tz_an2)

        parts = [
            f"🧠 *تحليل {_display_symbol_an} — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: {_fmt_price(price)} ({change_sign}{change_24h:.2f}%)",
            f"📊 RSI: {rsi_lbl} | Fear & Greed: {fear_val}",
            f"🌍 السوق: {regime_desc}",
            f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'} | حجم: {_fmt_volume(volume_24h)}" if volume_24h > 0 else f"📉 EMA50: {'✅ فوق' if not ema_bearish else '❌ تحت'}",
            "━━━━━━━━━━━━━━━━━━",
            analysis,
        ]
        if levels_lines:
            parts.extend(levels_lines)
        # إصلاح #415/#448: لا contradiction إذا السيناريو واضح
        _sig_scenario = getattr(_sig_a, "technicals", {}).get("scenario", "")
        _hide_contradiction = _sig_scenario in (
            "counter_trend_bounce",   # السيناريو يوضح الوضع
            "trend_continuation",     # الرسالة واضحة بالفعل
        )
        if contradiction and contradiction not in analysis and not _hide_contradiction:
            parts += ["", contradiction]
        # إضافة Fibonacci
        if fib_lines_a:
            parts.extend(fib_lines_a)
        # إضافة Professional Block — مع إزالة التكرار
        parts += ["", "━━━━━━━━━━━━━━━━━━"]
        # تنظيف pro_block من أي سطور تكرر الهيدر
        pro_clean = _clean_md(pro_block_a)
        parts.append(pro_clean)
        # تطوير #188 (Phase 2): إلحاق فقرة الزوج الإضافية إن وُجدت
        _pair_addon_a = await build_pair_addon_lines(resolution, engine.data_layer)
        if _pair_addon_a:
            parts.extend(_pair_addon_a)
        # إصلاح #250-B: "📌 أصل مُرمَّز" في /analyze كما في /signal
        if _is_perp_an:
            # TK_label_fix: اعرض نوع السوق الصحيح
            _mkt_label_an_display = "Spot" if not _use_futures_an else "Perpetual"
            parts.append(f"📌 {_display_symbol_an} — أصل مُرمَّز ({_mkt_label_an_display}) على OKX")
        # T25_fix: تحذير Synthetic في /analyze
        _is_x_an = _display_symbol_an.upper().startswith("X") and len(_display_symbol_an) > 2
        if _is_x_an:
            parts.append(
                f"\n⚠️ *تحذير:* {_display_symbol_an} أصل اصطناعي (Synthetic) — "
                "السيولة محدودة + لا تحماية مستثمرين."
            )

        # FA_an: إضافة BTC correlation في /analyze
        if _btc_corr_an:
            parts.append(_clean_md(_btc_corr_an.strip()))
        # إصلاح #250-A: رابط /chart في /analyze كما في /signal
        parts.append(f"📊 للتحليل البصري: /chart {_display_symbol_an}")
        # NYSE_hours_fix: إضافة تنبيه ساعات التداول في نهاية /analyze
        if _nyse_warn_an:
            parts.append(f"\n{_nyse_warn_an}")
        # DD1c (#1891/#1905): تحذير بيانات مشوهة في /analyze
        if _data_corrupted_an:
            parts += [
                "",
                "⚠️ *تحذير مالي: بيانات غير موثوقة*",
                # إصلاح #2170/#2178: EMA50 يُظهر القيمة الخام (قبل cap)
                f"• ATR={_atr_an:.1f}% | RSI={rsi:.0f} | EMA50={((price - _e50_an_raw) / max(_e50_an_raw, 0.0001) * 100) if '_e50_an_raw' in dir() and _e50_an_raw > 0 else _pve50_an:.0f}%",
                "• *لا تعتمد على هذا التحليل للتداول الفعلي*"
            ]
        parts += ["", "⚠️ هذا التحليل استرشادي — القرار للمستخدم"]
        full = _clean_md("\n".join(parts))

        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await _get_message(update, context).reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_analyze: {e}", exc_info=True)
        try:
            await msg.edit_text(
                f"⚠️ تعذّر التحليل العميق مؤقتاً\n\n"
                "🔄 بدائل متاحة:\n"
                "• /quicksignal — تحليل سريع\n"
                "• /signal — إشارة + مستويات دخول"
            )
        except Exception:
            pass
    finally:
        # إصلاح #178: تحرير semaphore دائماً
        try:
            _heavy_sem.release()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
# /chart — معالجة الأوامر (ماسي)
# ════════════════════════════════════════════════════════════════
async def cmd_chart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /chart — تعليمات تحليل الشارت البصري (ماسي)
    """
    user_id = update.effective_user.id
    if not _sm.can_use_command(user_id, "chart"):
        await _get_message(update, context).reply_text(
            "💎 *تحليل الشارت البصري — ماسي فقط*\n\n"
            "هذا الأمر متاح لمشتركي الباقة الماسية.\n"
            "للترقية: /upgrade",
            parse_mode="Markdown"
        )
        return

    await _get_message(update, context).reply_text(
        "⚙️ *تحليل الشارت البصري — قيد الصيانة*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🔄 *بدائل متاحة الآن:*\n"
        "• /analyze — تحليل عميق شامل (ذهبي+)\n"
        "• /signal  — إشارة + مستويات دخول\n"
        "• /quicksignal — تحليل سريع مجاني\n\n"
        "⏳ سيعود تحليل الشارت البصري قريباً",
        parse_mode="Markdown")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور المُرسَلة لتحليل الشارت."""
    user_id = update.effective_user.id
    if not _sm.can_use_command(user_id, "chart"):
        await _get_message(update, context).reply_text(
            "💎 تحليل الشارت البصري متاح لمشتركي الباقة الماسية فقط.\n"
            "للترقية: /upgrade"
        )
        return

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    msg = await _get_message(update, context).reply_text("🔍 جاري تحليل الشارت...")

    try:
        # DD4 (#1879): استخدام update.message مباشرة للصورة (لا _get_message)
        _photo_msg = update.message or update.effective_message
        photo = _photo_msg.photo if _photo_msg else None
        if photo:
            file = await photo[-1].get_file()
        elif _photo_msg and _photo_msg.document:
            file = await _photo_msg.document.get_file()
        else:
            await msg.edit_text(
                "⚠️ يُرجى إرسال صورة الشارت.\n"
                "اكتب /chart لمعرفة طريقة الاستخدام")
            return

        image_bytes = await file.download_as_bytearray()
        caption = (_photo_msg.caption or "") if _photo_msg else ""
        symbol  = ""
        # chart_sym_fix v2: استخراج symbol من caption بأشكال مختلفة
        import re as _re_chart
        # محاولة 1: "xSPY/USDT" أو "BTC-USDT" أو "BICO/USDT"
        _cap_sym = _re_chart.search(
            r'\b([A-Za-z]{1,10})\s*[/\-]\s*USDT', caption, _re_chart.IGNORECASE)
        if _cap_sym:
            symbol = _cap_sym.group(1).upper()
        # محاولة 2: كلمة منفردة
        if not symbol:
            for word in caption.split():
                w = _re_chart.sub(r'[^A-Za-z]', '', word).upper()
                if len(w) >= 2 and w not in ("ANALYZE","CHART","USDT","SPOT","THE","AND"):
                    symbol = w
                    break
        # محاولة 3: أي رمز معروف في النص
        if not symbol:
            _known = ["BTC","ETH","SOL","BNB","XRP","BICO","LAYER","GRASS","XSPY",
                      "XSPCX","XAAPL","XAMD","XMETA","XGOOGL","XAUT","XSKHY"]
            for _k in _known:
                if _k.lower() in caption.lower():
                    symbol = _k
                    break

        # تطوير #222 (محسَّن): اكتشاف نوع السوق من:
        # (1) caption النصي أولاً
        # (2) نص التحليل الذي يُنتجه الـVision model (يتضمن الآن قسم "0-نوع السوق")
        # F9b: كلمات Futures الحقيقية
        _futures_keywords = (
            "PERP", "SWAP", "FUTURES", "PERPETUAL", "USDT-SWAP",
            "MARK PRICE", "FUNDING RATE", "OVERNIGHT", "-PERP", "USDT-M",
            "COIN-M", "DELIVERY", "QUARTERLY", "FUTURES/PERP",
            "عقود دائمة", "عقود آجلة", "عقد مستمر", "فيوتشر",
        )
        # F9b: مصطلحات Spot صريحة — إذا ظهرت تلغي أي 10x/5x/3x
        # ملاحظة: OKX يعرض "10x" بجانب "التداول الفوري" كتنويه فقط وليس Futures
        _spot_override_kw = ("التداول الفوري", "SPOT TRADING", "SPOT MARKET")
        _caption_upper = caption.upper()
        _is_spot_explicit = any(kw in caption or kw in _caption_upper
                                for kw in _spot_override_kw)
        _chart_is_futures = (
            not _is_spot_explicit and
            any(kw in _caption_upper for kw in _futures_keywords)
        )
        if not _chart_is_futures and not _is_spot_explicit and symbol:
            _chart_is_futures = any(kw in symbol.upper()
                                     for kw in ("PERP","SWAP","FUT"))

        # NYSE_hours_fix: تحذير خارج ساعات التداول بتوقيت المستخدم
        try:
            _sm_chart = context.bot_data.get("subscription_manager")
            _uid_chart = update.effective_user.id if update.effective_user else 0
            _tz_chart = int((_sm_chart.get_user_data(_uid_chart) or {}).get("tz_offset", 3)) if _sm_chart else 3
        except Exception:
            _tz_chart = 3
        _pre_market_warn = _get_market_hours_warning(symbol or "", _tz_chart)
        if _pre_market_warn:
            _pre_market_warn = f"\n{_pre_market_warn}\n"

        # T30_fix: تمرير السعر الحالي لـ Qwen3 لمنع تحليل أصل خاطئ
        _current_px = 0.0
        try:
            if symbol and engine:
                _px_data = await engine.data_layer.get_price(symbol.upper())
                if _px_data and _px_data.get("price", 0) > 0:
                    _current_px = float(_px_data["price"])
        except Exception:
            pass

        analysis = await engine.news_engine.analyze_chart_image(
            image_data=bytes(image_bytes),
            symbol=symbol,
            current_price=_current_px)

        # فحص نص التحليل — القسم "0-نوع السوق" يُصرِّح بالنوع صراحةً
        if not _chart_is_futures and analysis:
            _analysis_upper = analysis.upper()
            _chart_is_futures = any(kw in _analysis_upper
                                     for kw in ("FUTURES/PERP", "FUTURES", "PERP",
                                                 "SWAP", "PERPETUAL", "MARK PRICE",
                                                 "FUNDING RATE", "OVERNIGHT",
                                                 "فيوتشر", "عقد دائم", "عقد مستمر"))

        _mkt_label = "Futures/Perp" if _chart_is_futures else "Spot"
        sym_label = f" — {symbol}" if symbol else ""
        # إضافة header بمعلومات العملة (M#54)
        _sym_label = f" — {symbol}" if symbol else ""
        # CHART_FORMAT_fix: تنسيق احترافي محسَّن
        _mkt_icon = "📈 Futures/Perp" if _chart_is_futures else "⚡ Spot"
        # chart_header_fix: إضافة معلومات الأصل الكاملة
        _is_x_chart = symbol.upper().startswith("X") and len(symbol) > 2 if symbol else False
        _synthetic_warn_chart = (
            f"⚠️ تحذير: {symbol.upper()} أصل اصطناعي (Synthetic) — السيولة محدودة"
            if _is_x_chart else ""
        )
        header_lines = [
            f"📊 تحليل الشارت البصري{_sym_label}",
            f"🏪 نوع السوق: {_mkt_icon}",
            "━━━━━━━━━━━━━━━━━━",
        ]
        # chart_header_price: محاولة جلب السعر من caption أو OKX
        # إذا symbol مستخرج من caption → استخدمه
        # إذا فارغ → حاول استخراج من _sym_label (إذا موجود)
        _chart_price_sym = symbol or ""
        if not _chart_price_sym and _sym_label:
            # _sym_label قد يكون " — XSPY" أو " — BTC"
            import re as _re_cps
            _m_lbl = _re_cps.search(r'—\s*([A-Za-z]{2,10})', _sym_label)
            if _m_lbl:
                _chart_price_sym = _m_lbl.group(1).upper()
        # chart_header_price_fix v3: جلب السعر من OKX أو _current_px (T30_fix)
        _chart_px_found = False
        if _chart_price_sym:
            try:
                eng3 = context.bot_data.get("raed_engine")
                if eng3:
                    pd3 = await eng3.data_layer.get_price(_chart_price_sym)
                    if pd3 and pd3.get("price", 0) > 0:
                        p3 = pd3["price"]
                        c3 = pd3.get("change_24h", 0)
                        _c3_icon = "📈" if c3 >= 0 else "📉"
                        header_lines += [
                            f"💰 السعر: {_fmt_price(p3)} ({c3:+.2f}%) {_c3_icon}",
                            f"⏱️ الإطار الزمني: يومي (1D)",
                        ]
                        if _synthetic_warn_chart:
                            header_lines.append(_synthetic_warn_chart)
                        header_lines.append("━━━━━━━━━━━━━━━━━━")
                        _chart_px_found = True
            except Exception:
                pass
        # fallback: _current_px من T30_fix (يُجلَب قبل header_lines)
        if not _chart_px_found and _current_px > 0:
            header_lines += [
                f"💰 السعر: {_fmt_price(_current_px)}",
                f"⏱️ الإطار الزمني: يومي (1D)",
            ]
            if _synthetic_warn_chart:
                header_lines.append(_synthetic_warn_chart)
            header_lines.append("━━━━━━━━━━━━━━━━━━")
            _chart_px_found = True
        # synthetic_warn بدون سعر إذا لم يُجلَب
        if not _chart_px_found and _synthetic_warn_chart:
            header_lines += [
                _synthetic_warn_chart,
                "━━━━━━━━━━━━━━━━━━",
            ]
        # إصلاح #236: ربط /chart ↔ /signal للتكامل التحليلي
        _signal_hint = f"\n💡 للتحليل الشامل متعدد المصادر: /signal {symbol}" if symbol else ""
        # CHART_FORMAT_fix: بناء الرسالة النهائية بتنسيق منظم
        _analysis_clean = analysis.strip()
        # NYSE_hours_fix: تنبيه خارج ساعات NYSE في /analyze
        try:
            _sm_an2 = context.bot_data.get("subscription_manager")
            _tz_an2 = int((_sm_an2.get_user_data(user_id) or {}).get("tz_offset", 3)) if _sm_an2 else 3
        except Exception:
            _tz_an2 = 3
        _nyse_warn_an = _get_market_hours_warning(symbol, _tz_an2)

        # NYSE_hours_fix: دمج تنبيه ساعات التداول في نص /chart
        _nyse_block = f"\n{_pre_market_warn}\n" if _pre_market_warn else ""
        full = (
            "\n".join(header_lines) + "\n\n" +
            _nyse_block +
            _analysis_clean + "\n\n" +
            f"{_signal_hint}\n" +
            f"⚠️ التحليل استرشادي — القرار للمستخدم"
        )
        if len(full) > 4000:
            await msg.edit_text(full[:4000], parse_mode="Markdown")
            await _get_message(update, context).reply_text(full[4000:], parse_mode="Markdown")
        else:
            await msg.edit_text(full, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_chart: {e}")
        await msg.edit_text(
            "⚙️ *تحليل الشارت البصري — قيد الصيانة*\n\n"
            "🔄 *بدائل متاحة الآن:*\n"
            "• /analyze — تحليل عميق شامل\n"
            "• /signal  — إشارة + مستويات دخول\n"
            "• /quicksignal — تحليل سريع",
            parse_mode=ParseMode.MARKDOWN
        )


# ════════════════════════════════════════════════════════════════
# /quicksignal — متاح للجميع
# ════════════════════════════════════════════════════════════════
async def cmd_quicksignal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /quicksignal [عملة] — تحليل أولي سريع مع نقاط الدخول والخروج
    متاح لجميع الباقات
    """
    engine = _eng(context)
    if not engine:
        await _get_message(update, context).reply_text("⚠️ النظام لم يُهيَّأ بعد")
        return

    args      = context.args or []
    raw_arg   = args[0].upper() if args else "BTC"
    qs_sym    = raw_arg  # QS_fix: تعريف qs_sym = raw_arg
    user_id_q = update.effective_user.id if update.effective_user else 0
    tier_q    = _sm.get_tier(user_id_q)

    # تطوير #221: سؤال نوع السوق (Spot/Futures) إن لم يُحدَّد مسبقاً
    # إصلاح #237-A: الأصول المُرمَّزة (أسهم/معادن/سلع) → Futures تلقائياً بدون سؤال
    _mkttype_qs = context.user_data.pop("_mkttype", None)
    if _mkttype_qs is None:
        # فحص مبكر: هل الرمز أصل مُرمَّز (Futures حصراً)؟
        _pre_is_asset = False
        if tier_q in ("diamond", "admin"):
            try:
                _pre_is_asset = await engine.data_layer.is_tokenized_stock(raw_arg.replace("BTC","").replace("ETH","") or raw_arg)
            except Exception:
                _pre_is_asset = False
        # TK_ROOT_fix: X-prefix = Spot مُرمَّز → لا نُجبر Futures
        _is_x_qs = raw_arg.upper().startswith("X") and len(raw_arg) > 2
        if _pre_is_asset and not _is_x_qs:
            _mkttype_qs = "futures"  # Futures تلقائياً للأصول بدون X
        else:
            sent_qs = await _ask_market_type(update, context, "quicksignal", raw_arg, tier_q)
            if sent_qs:
                return
    _use_futures_qs = (_mkttype_qs == "futures")
    _mkt_arg_qs = "futures" if _use_futures_qs else "spot"  # إصلاح #258

    # TK3/TK1b: التحقق من Spot (X-prefix يتجاوز مباشرة)
    if not _use_futures_qs:
        if qs_sym.upper().startswith("X") and len(qs_sym) > 2:
            pass  # TK3_fix: XSPCX/XAMZN أصول Spot مؤكدة
        else:
            try:
                _spot_chk_qs = await engine.data_layer.check_spot_available(qs_sym)
                if not _spot_chk_qs.get("available", True):
                    await _get_message(update, context).reply_text(
                        _spot_chk_qs.get("message", f"⚠️ {qs_sym} غير متاح في Spot"),
                        parse_mode="Markdown"
                    )
                    return
                _qs_sym_actual = _spot_chk_qs.get("spot_symbol", qs_sym)
                if _qs_sym_actual != qs_sym.upper():
                    qs_sym = _qs_sym_actual
            except Exception:
                pass

    msg    = await _get_message(update, context).reply_text(
        f"🔍 جاري التحليل الأولي لـ {raw_arg}...")

    resolution = await resolve_symbol(raw_arg, tier_q, engine.data_layer)
    symbol  = resolution.base

    # تطوير #188 (Phase 2.5): الزوج المطلوب أساسي عند التوفر الكامل
    pair_primary = (resolution.is_pair_request and resolution.eligible_tier
                    and resolution.pair_available)
    quote = resolution.quote if pair_primary else "USDT"
    display_symbol = f"{symbol}/{quote}" if pair_primary else symbol

    # تطوير #209: اكتشاف تلقائي للأسهم المُرمَّزة (ماسي+ فقط)
    is_perp_stock = False
    if not pair_primary and tier_q in ("diamond", "admin"):
        try:
            is_perp_stock = await engine.data_layer.is_tokenized_stock(symbol)
        except Exception:
            is_perp_stock = False
    if is_perp_stock:
        display_symbol = f"{symbol} (Perp)"

    # T10b_fix: X-prefix Spot (XSPCX/XAAPL...) → جلب OKX Spot مباشرة
    _is_x_spot_qs = (raw_arg.upper().startswith("X") and
                     len(raw_arg) > 2 and
                     not is_perp_stock)
    if _is_x_spot_qs:
        display_symbol = raw_arg.upper()  # XSPCX وليس SPCX

    try:
        # M#119: timeout صارم لمنع التجمد
        try:
            if is_perp_stock:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price_perp(symbol),
                        engine.data_layer.get_ohlcv_perp(symbol, 250),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
            elif _is_x_spot_qs:
                # T10b_fix: X-prefix Spot → جلب من OKX Spot مباشرة
                _x_spot_sym = raw_arg.upper()  # XSPCX
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price(_x_spot_sym, "USDT", mkttype="spot"),
                        engine.data_layer.get_ohlcv(_x_spot_sym, "1d", 30, "USDT",
                            mkttype="spot", _cache_hint=_x_spot_sym),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
                # T10b_scaling: إذا candles قليلة من OKX → احسب ATR من ما لديه
                if isinstance(candles, list) and len(candles) < 10:
                    # OKX Spot بيانات محدودة → نحاول جلب آخر 7 أيام مباشرة
                    try:
                        _short_c = await engine.data_layer.get_ohlcv(
                            _x_spot_sym, "1d", 7, "USDT",
                            mkttype="spot", _cache_hint=f"{_x_spot_sym}_short")
                        if isinstance(_short_c, list) and len(_short_c) >= 3:
                            candles = _short_c
                    except Exception:
                        pass
            else:
                price_d, candles, fear, btc_dom = await asyncio.wait_for(
                    asyncio.gather(
                        engine.data_layer.get_price(symbol, quote, mkttype=_mkt_arg_qs),
                        engine.data_layer.get_ohlcv(symbol, "1d", 250, quote,
                            mkttype=_mkt_arg_qs,
                            _cache_hint=raw_arg.upper()),
                        engine.data_layer.get_fear_greed(),
                        engine.data_layer.get_btc_dominance(),
                        return_exceptions=True
                    ), timeout=30.0
                )
        except asyncio.TimeoutError:
            await msg.edit_text(
                f"⏱️ انتهت مهلة تحليل *{display_symbol}*\n\n"
                f"جرّب: /quicksignal {raw_arg}",
                parse_mode="Markdown")
            return

        price_d = price_d if isinstance(price_d, dict) else {}
        candles = candles if isinstance(candles, list) else []
        fear    = fear    if isinstance(fear, dict)    else {"value": 50}
        btc_dom = btc_dom if isinstance(btc_dom, float) else 50.0

        # retry للعملات الصغيرة خارج top100
        if len(candles) < 10:
            logger.info(f"analyze: retry OHLCV for {symbol}")
            await asyncio.sleep(1)
            if is_perp_stock:
                retry_c = await engine.data_layer.get_ohlcv_perp(symbol, 60)
            else:
                retry_c = await engine.data_layer.get_ohlcv(symbol, "1d", 60, quote)
            if isinstance(retry_c, list) and len(retry_c) >= 10:
                candles = retry_c

        price      = float(price_d.get("price") or 0)
        fear_val   = int(fear.get("value") or 50)
        change_24h = float(price_d.get("change_24h") or price_d.get("price_change_percentage_24h") or 0)

        if price <= 0:
            await msg.edit_text(
                f"❌ لم أجد سعراً لـ {display_symbol}.\n"
                f"تحقق من الرمز وأعد المحاولة")
            return

        rsi = _calc_rsi(candles)

        # M3/BB1b (#1782/#1799): فحص جودة البيانات في /quicksignal
        _atr_q     = _calc_atr(candles)
        _pve50_q   = 0.0
        if len(candles) >= 50:
            try:
                _cls_q  = [float(c.get("close", 0)) for c in candles if c.get("close")]
                _e50_q  = sum(_cls_q[-50:]) / 50 if len(_cls_q) >= 50 else _cls_q[-1]
                _pve50_q = (price - _e50_q) / max(_e50_q, 0.0001) * 100 if _e50_q > 0 and "price" in dir() else 0.0
            except Exception: pass
        # GG4b: كشف فساد EMA50 الخام في /quicksignal
        _ema50_q_corrupted = False
        if len(candles) >= 50:
            try:
                _cls_q2 = [float(c.get("close", 0)) for c in candles if c.get("close")]
                _e50_q_raw = sum(_cls_q2[-50:]) / 50 if len(_cls_q2) >= 50 else price
                _ema50_q_corrupted = (_e50_q_raw > price * 3.0)
            except Exception: pass
        _data_corrupted_q = (_atr_q > 25 or rsi < 5 or _pve50_q < -50 or _ema50_q_corrupted)

        # حساب regime أولاً — يؤثر على التوصية
        regime_desc = "⚪ جاري تحديث بيانات السوق"
        regime_obj  = None
        is_bearish  = False
        ema_bearish = False   # السعر تحت EMA الرئيسية

        if len(candles) >= 10:  # خُفِّض: 30 → 10
            try:
                # تطوير #188 (Phase 2.5): مفتاح كاش regime مستقل عند pair_primary
                _regime_key = f"{symbol}{quote}" if pair_primary else symbol
                regime_obj  = engine.regime_detector.detect(
                    candles, btc_dominance=btc_dom, fear_greed=fear_val, symbol=_regime_key)
                regime_desc = regime_obj.description_ar
                is_bearish  = "هابط" in regime_desc
            except Exception as e:
                logger.warning(f"regime detect: {e}")

        # فحص EMA — إذا السعر تحت EMA20 → إشارة هبوطية
        if len(candles) >= 20:
            try:
                closes  = [float(c.get("close", 0)) for c in candles if c.get("close")]
                ema5    = sum(closes[-5:])  / 5  if len(closes) >= 5  else closes[-1]
                ema10   = sum(closes[-10:]) / 10 if len(closes) >= 10 else closes[-1]
                ema20   = sum(closes[-20:]) / 20
                _ema50_raw_q = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
                # EE1: cap ema50 بـ price × 3 لمنع قيم تاريخية مشوّهة
                ema50 = _ema50_raw_q if _ema50_raw_q <= price * 3.0 else ema20
                # هابط حقيقي: السعر تحت معظم EMAs
                ema_below_count = sum([price < ema5, price < ema10, price < ema20, price < ema50])
                ema_bearish = ema_below_count >= 3  # تحت 3 من 4 EMAs = هابط
            except Exception:
                pass

        # إصلاح #239-A: دعم/مقاومة من أقرب مستويات السعر (5 شموع) لا القاع/القمة الشهرية
        support = resistance = 0.0
        if len(candles) >= 5:
            # أولاً: آخر 5 شموع (أقرب للسعر الحالي)
            lows5  = [float(c.get("low",  c.get("close", 0))) for c in candles[-5:]
                      if float(c.get("low", c.get("close", 0))) > 0]
            highs5 = [float(c.get("high", c.get("close", 0))) for c in candles[-5:]
                      if float(c.get("high", c.get("close", 0))) > 0]
            if lows5 and highs5:
                sup5 = min(lows5)  * 0.999
                res5 = max(highs5) * 1.001
                # إذا كانت مستويات 5 شموع قريبة بما يكفي (±15%) → استخدمها
                if price > 0 and (price - sup5) / price <= 0.15 and (res5 - price) / price <= 0.15:
                    support    = sup5
                    resistance = res5
            # fallback: آخر 20 شمعة إذا كانت 5 شموع ضيقة جداً أو غير كافية
            if (support == 0 or resistance == 0) and len(candles) >= 20:
                lows20  = [float(c.get("low",  c.get("close", 0))) for c in candles[-20:]
                           if float(c.get("low", c.get("close", 0))) > 0]
                highs20 = [float(c.get("high", c.get("close", 0))) for c in candles[-20:]
                           if float(c.get("high", c.get("close", 0))) > 0]
                if lows20 and highs20:
                    support    = min(lows20)  * 0.99
                    resistance = max(highs20) * 1.01

        # توصية شاملة: RSI + Fear + Regime + EMA
        # مبدأ الحذر: إذا regime غير محدد → لا توصية شراء
        regime_unknown = regime_desc in ("⚪ جاري تحديث بيانات السوق", "", None)

        if rsi < 30 and fear_val < 40 and not ema_bearish and not is_bearish and not regime_unknown:
            # ذروة بيع + خوف + EMA صاعد + سوق غير هابط → شراء محتمل
            direction = "🟢 شراء محتمل"
            entry = price * 0.99; tp1 = price * 1.05; tp2 = price * 1.10; sl = price * 0.95
        elif rsi < 30 and (is_bearish or ema_bearish or regime_unknown):
            # ذروة بيع لكن في سوق هابط → انتظار ارتداد فقط (لا شراء)
            direction = "⏳ انتظار ارتداد"
            entry = price * 0.98; tp1 = price * 1.03; tp2 = price * 1.06; sl = price * 0.95
        elif rsi > 70 and fear_val > 60:
            direction = "🔴 بيع محتمل"
            entry = price * 1.01; tp1 = price * 0.95; tp2 = price * 0.90; sl = price * 1.05
        elif rsi > 70 and ema_bearish:
            direction = "🔴 بيع قوي"
            entry = price * 1.005; tp1 = price * 0.94; tp2 = price * 0.88; sl = price * 1.04
        elif 30 <= rsi <= 45 and fear_val < 50 and not is_bearish:
            direction = "🟡 شراء محتاط"
            entry = price * 0.99; tp1 = price * 1.04; tp2 = price * 1.08; sl = price * 0.96
        elif 55 <= rsi <= 70 and fear_val > 50:
            direction = "🟠 بيع محتاط"
            entry = price * 1.01; tp1 = price * 0.96; tp2 = price * 0.92; sl = price * 1.04
        # إصلاح #240: else branch — يجب مراعاة اتجاه السوق
        # في السوق الهابط: لا يُعطي Long ضمنياً — TP/SL تعكس الانتظار الحقيقي
        elif is_bearish or ema_bearish:
            # إصلاح #244-A: سوق هابط + RSI وسط → انتظار حذر
            # R/R يجب ≥ 1:1: SL=-4% → TP1 يجب ≥ +4% على الأقل
            direction = "⚪ انتظار — سوق هابط"
            entry = price * 0.990   # عند الدعم القريب
            tp1   = price * 1.040   # +4% (= SL، R/R 1:1 كحد أدنى)
            tp2   = price * 1.080   # +8% (R/R 2:1)
            sl    = price * 0.960   # -4% وقف صارم
        else:
            # سوق محايد + RSI وسط → انتظار بياض
            direction = "⚪ انتظار"
            entry = price * 0.985; tp1 = price * 1.05; tp2 = price * 1.08; sl = price * 0.96

        # regime_desc محسوبة أعلاه

        # تحذيرات متعددة
        bear_buy_warning = ""
        if direction.startswith("🟢") and regime_desc and "هابط" in regime_desc:
            bear_buy_warning = "\n⚠️ *تنبيه:* إشارة شراء في سوق هابط — تحقق من تأكيد الاتجاه قبل الدخول"
        if abs(change_24h) > 15:
            sign_ar = "ارتفاع" if change_24h > 0 else "انخفاض"
            bear_buy_warning += f"\n🚨 *تحذير:* {sign_ar} حاد {abs(change_24h):.1f}% في 24 ساعة — خطر الدخول مرتفع"

        change_sign = "+" if change_24h >= 0 else ""
        lines = [
            f"📊 *التحليل الأولي — {display_symbol}*",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 السعر: {_fmt_price(price, quote)} ({change_sign}{change_24h:.2f}%)",
            f"🌍 السوق: {regime_desc}",
            f"📈 RSI: {int(rsi)} | Fear & Greed: {fear_val}",
            "",
            f"🎯 *التوصية: {direction}*",
            "",
            "📍 *مناطق الدخول والخروج*",
            f"• نقطة الدخول: {_fmt_price(entry, quote)}",
            f"• هدف 1:       {_fmt_price(tp1, quote)} ({(tp1/max(price,1e-9)-1)*100:+.1f}%)",
            f"• هدف 2:       {_fmt_price(tp2, quote)} ({(tp2/max(price,1e-9)-1)*100:+.1f}%)",
            f"• وقف الخسارة: {_fmt_price(sl, quote)} ({(sl/max(price,1e-9)-1)*100:+.1f}%)",
        ]
        if support > 0 and resistance > 0:
            _sup_s, _res_s = _fmt_price_pair(support, resistance, quote)
            lines += [
                "",
                "🏗️ *المستويات الرئيسية*",
                f"• دعم:    {_sup_s}",
                f"• مقاومة: {_res_s}",
            ]
        if bear_buy_warning:
            lines.append(bear_buy_warning)
        # FC: إضافة ATR في /quicksignal
        _atr_qs = _calc_atr(candles) if candles else 0.0
        if _atr_qs > 0:
            lines.append(f"📊 ATR: {_atr_qs:.1f}% يومياً")
        # T24_fix: تحذير Synthetic + سيناريوهات
        _is_x_qs = display_symbol.upper().startswith("X") and len(display_symbol) > 2
        if _is_x_qs:
            lines.append(
                f"\n⚠️ *تحذير:* {display_symbol} أصل اصطناعي (Synthetic) — "
                "السيولة محدودة + مخاطر إضافية."
            )

        # T24_fix: سيناريوهات بناءً على RSI + Direction
        _qs_scenarios = []
        if direction.startswith("🟢") and rsi < 40:
            _qs_scenarios = [
                f"🟢 صاعد (60%): ثبات فوق {_fmt_price(support, quote)} → هدف {_fmt_price(tp1, quote)}",
                f"⚪ محايد (30%): تذبذب {_fmt_price(support, quote)}-{_fmt_price(resistance, quote)}",
                f"🔴 هابط (10%): كسر {_fmt_price(sl, quote)} → مراجعة المركز",
            ]
        elif direction.startswith("⚪"):
            _qs_scenarios = [
                f"🟢 صاعد: كسر {_fmt_price(resistance, quote)} مع حجم → دخول",
                f"⚪ محايد (50%): انتظار — السيناريو الأرجح",
                f"🔴 هابط: كسر {_fmt_price(support, quote)} → لا دخول",
            ]
        if _qs_scenarios:
            lines += ["", "📋 *السيناريوهات*"] + [f"• {s}" for s in _qs_scenarios]

        # T1_fix: تحذير للمجاني + عتبة الباقة في /quicksignal
        _tier_qs_t1 = _sm.get_tier(user_id_q) if user_id_q else "silver"
        _entry_qs = _TIER_CONF.get(_tier_qs_t1, _TIER_CONF["silver"])[1]
        if _tier_qs_t1 == "free":
            lines.append(f"\n⚠️ *إشارة تقنية فقط* — تحقق من الأخبار قبل الدخول")
        lines += [
            "",
            f"💡 للتحليل العميق الكامل: /analyze (ذهبي+)",
            "⚠️ هذا تحليل استرشادي — القرار للمستخدم",
        ]
        # تطوير #188 (Phase 2.5): فقرة إضافية في نهاية التقرير
        if pair_primary:
            # الزوج هو الأساس -> فقرة مرجعية صغيرة بسعر USDT
            _addon_q = await build_usdt_addendum(resolution, engine.data_layer)
        else:
            # USDT أساسي -> فقرة الزوج (ترقية/غير متوفر/الزوج كبديل مبسّط)
            _addon_q = await build_pair_addon_lines(resolution, engine.data_layer)
        if _addon_q:
            lines = lines[:-1] + _addon_q + ["", lines[-1]]

        # M3b: تحذير بيانات مشوّهة في /quicksignal
        if _data_corrupted_q:
            lines.append(
                f"\n⚠️ *تحذير: بيانات غير موثوقة*\n"
                f"• ATR={_atr_q:.1f}% | RSI={rsi:.0f} — السعر انهار حديثاً"
            )

        await msg.edit_text(
            _clean_md("\n".join(lines)), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_quicksignal: {e}", exc_info=True)
        await msg.edit_text("❌ خطأ في التحليل الأولي. حاول لاحقاً")


# ════════════════════════════════════════════════════════════════
# /upgrade — جدول الباقات
# ════════════════════════════════════════════════════════════════
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state_manager import state_manager as _sm, TIERS
    user_id   = update.effective_user.id
    cur_tier  = _sm.get_tier(user_id)
    tier_info = TIERS[cur_tier]

    lines = [
        "💎 *باقات رائد للتداول الذكي*",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🆓 *مجاني — مجاناً*",
        "• /quicksignal — تحليل تقني سريع",
        "• عتبة الدخول: 45% | حجم صفقة: 3% فقط",
        "• RSI filter: 35-65 (حماية من الذروات)",
        "• تداول آلي مجاني 30 يوم | 15 عملة",
        "• ⚠️ عملات Spot فقط — لا أسهم مُرمَّزة",
        "",
        "🥈 *فضي — $9/شهر*",
        "• كل المجاني +",
        "• /signal — إشارة 5 مصادر | /news | /regime | /backtest",
        "• عتبة الدخول: 50% | حجم صفقة: 5–10%",
        "• RSI filter: 25-75 | 35 عملة",
        "• ⚠️ عملات Spot فقط — لا أسهم مُرمَّزة",
        "",
        "🥇 *ذهبي — $29/شهر ⭐ الأكثر طلباً*",
        "• كل الفضي +",
        "• /analyze + /liquidity + /planweek + /planmonth",
        "• عتبة الدخول: 55% | حجم صفقة: 10–20%",
        "• ✅ أسهم مُرمَّزة Spot وFutures (عتبة 60%)",
        "• بدون RSI filter | 100 عملة",
        "",
        "💎 *ماسي — $99/شهر*",
        "• كل الذهبي +",
        "• /chart — تحليل شارت بصري | دعم 24/7",
        "• عتبة الدخول: 55% | حجم صفقة: 20–35%",
        "• ✅ أسهم مُرمَّزة بعتبة 55% وحجم أكبر",
        "• 300 عملة | تحليل كمي متقدم",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "📊 *مقارنة سريعة (ثقة 54% — مثل XAAPL)*",
        "• مجاني: ✅ دخول 3%",
        "• فضي: ✅ دخول 5-10%",
        "• ذهبي: 🟡 محتاط 5-10% (أسهم: حجم أقل)",
        "• ماسي: 🟡 محتاط 10-20% (أسهم: حجم أكبر)",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if cur_tier == "admin":
        lines.append(f"✅ باقتك: {tier_info['name']} — صلاحيات كاملة")
    elif cur_tier == "diamond":
        lines.append(f"✅ باقتك: {tier_info['name']} — أعلى باقة")
    else:
        lines.append(f"📌 باقتك الحالية: {tier_info['name']}")
        lines.append("للترقية: تواصل مع الدعم الفني")

    lines += ["", "📞 *الدعم الفني*", "للاشتراك والاستفسارات: قريباً"]

    await _get_message(update, context).reply_text(
        "\n".join(lines), parse_mode="Markdown")


# ════════════════════════════════════════════════════════════════
# تسجيل الـ handlers
# ════════════════════════════════════════════════════════════════
def register(app):
    logger.info("analysis handlers: جاري التسجيل...")
    app.add_handler(CommandHandler("news",         cmd_news))
    app.add_handler(CommandHandler("onchain",      cmd_onchain))
    app.add_handler(CommandHandler("regime",       cmd_regime))
    app.add_handler(CommandHandler("signal",       cmd_signal))
    app.add_handler(CommandHandler("backtest",     cmd_backtest))
    app.add_handler(CommandHandler("liquidity",    cmd_liquidity))
    app.add_handler(CommandHandler("events",       cmd_events))
    app.add_handler(CommandHandler("outlook", cmd_outlook))
    app.add_handler(CommandHandler("drift",        cmd_drift))
    app.add_handler(CommandHandler("analyze",      cmd_analyze))
    app.add_handler(CommandHandler("quicksignal",  cmd_quicksignal))
    app.add_handler(CommandHandler("upgrade",      cmd_upgrade))
    app.add_handler(CommandHandler("chart",        cmd_chart_cmd))
    app.add_handler(CommandHandler("risk",         cmd_risk))   # R1: تسجيل /risk
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE,
        cmd_chart))
    logger.info("✅ analysis handlers: تم تسجيل جميع الأوامر")

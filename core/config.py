"""
🤖 رائد التداول الذكي — الإعدادات المركزية
مبني على NexusTrader (MIT) — Quantweb3
تطوير: فريق رائد التداول الذكي
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── هوية رائد ──────────────────────────────────────────────────────────────
RAED_NAME      = "🤖 رائد التداول الذكي"
RAED_VERSION   = "2.0.0"
RAED_CREDIT    = "مبني على NexusTrader (MIT) — Quantweb3"
RAED_TEAM      = "تطوير: فريق رائد التداول الذكي"
RAED_SIGNATURE = f"\n\n─────────────────\n📊 {RAED_NAME}\n{RAED_CREDIT}\n{RAED_TEAM}"

# ── إيموجي الهوية ──────────────────────────────────────────────────────────
E = {
    "bot":      "🤖",
    "chart":    "📈",
    "money":    "💰",
    "alert":    "⚡",
    "lock":     "🔒",
    "report":   "📊",
    "ok":       "✅",
    "error":    "❌",
    "bank":     "🏦",
    "warn":     "⚠️",
    "rocket":   "🚀",
    "star":     "⭐",
    "diamond":  "💎",
    "gold":     "🥇",
    "silver":   "🥈",
    "free":     "🆓",
    "time":     "⏱️",
    "up":       "📈",
    "down":     "📉",
    "wallet":   "👛",
    "virtual":  "🎮",
    "settings": "⚙️",
    "help":     "❓",
    "exchange": "🔄",
    "fire":     "🔥",
    "shield":   "🛡️",
    "eye":      "👁️",
    "brain":    "🧠",
    "medal":    "🏅",
    "gift":     "🎁",
    "news":     "📰",
    "pin":      "📌",
    "link":     "🔗",
    "info":     "ℹ️",
    "trash":    "🗑️",
    "back":     "🔙",
    "next":     "▶️",
    "stop":     "⏹️",
    "key":      "🔑",
    "bell":     "🔔",
    "mute":     "🔕",
    "saudi":    "🇸🇦",
}

# ── متغيرات البيئة ──────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
ADMIN_ID_STR    = os.getenv("ADMIN_ID", "0")
try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    ADMIN_ID = 0
    logger.warning("ADMIN_ID غير صحيح في متغيرات البيئة — سيُعطَّل")

REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379")

_enc_key_raw    = os.getenv("ENCRYPTION_KEY", "")
ENCRYPTION_KEY  = _enc_key_raw.encode() if _enc_key_raw else b""
if not ENCRYPTION_KEY:
    logger.warning("ENCRYPTION_KEY غير موجود — التشفير مُعطَّل")

ENVIRONMENT     = os.getenv("ENVIRONMENT", "production")

# ── حدود الأمان ────────────────────────────────────────────────────────────
RATE_LIMIT      = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
MAX_FAILS       = int(os.getenv("MAX_FAILED_ATTEMPTS", "5"))
BAN_HOURS       = int(os.getenv("BAN_DURATION_HOURS", "1"))

# ── البورصات المدعومة ───────────────────────────────────────────────────────
EXCHANGES = {
    "binance": {"name": "بايننس",  "emoji": "🟡", "testnet": True},
    "okx":     {"name": "OKX",     "emoji": "🔵", "testnet": True},
    "bybit":   {"name": "بايبت",   "emoji": "🟠", "testnet": True},
    "bitget":  {"name": "بيتجيت", "emoji": "⚫", "testnet": True},
}

# ── باقات الاشتراك ──────────────────────────────────────────────────────────
PLANS = {
    "free": {
        "name":         "🆓 مجاني",
        "price":        0,
        "alerts":       1,
        "exchanges":    1,
        "virtual":      True,
        "real_trading": False,
        "auto_trading": False,
        "reports":      "يومي",
        "price_coins":  15,
        "support":      "مجتمع",
    },
    "silver": {
        "name":         "🥈 فضي",
        "price":        9,
        "alerts":       10,
        "exchanges":    4,
        "virtual":      True,
        "real_trading": True,
        "auto_trading": False,
        "reports":      "أسبوعي",
        "price_coins":  35,
        "support":      "بريد إلكتروني",
    },
    "gold": {
        "name":         "🥇 ذهبي",
        "price":        29,
        "alerts":       999,
        "exchanges":    4,
        "virtual":      True,
        "real_trading": True,
        "auto_trading": True,
        "reports":      "يومي + أسبوعي",
        "price_coins":  100,
        "support":      "أولوية",
    },
    "diamond": {
        "name":         "💎 ماسي",
        "price":        99,
        "alerts":       999,
        "exchanges":    4,
        "virtual":      True,
        "real_trading": True,
        "auto_trading": True,
        "reports":      "كامل + مؤسسي",
        "price_coins":  300,
        "support":      "مباشر 24/7",
    },
}

# ── المحفظة الافتراضية ──────────────────────────────────────────────────────
VIRTUAL_WALLET_START = 10_000.0  # دولار

# ── حدود المخاطر (موحَّدة مع raed_engine) ───────────────────────────────────
RISK = {
    "max_loss_pct":        2.0,    # وقف الخسارة: 2%
    "take_profit_pct":     4.0,    # هدف الربح: 4%
    "max_position_pct":    10.0,   # أقصى حجم صفقة: 10% من المحفظة (موحَّد مع MAX_SINGLE_EXPOSURE)
    "max_open_trades":     5,      # أقصى صفقات مفتوحة
    "max_leverage":        3,      # أقصى رافعة مالية
    "min_confidence":      0.65,   # أدنى ثقة للتنفيذ (موحَّد مع MIN_CONFIDENCE)
}

# ── مؤشرات الاستراتيجية ─────────────────────────────────────────────────────
STRATEGY = {
    "ma_fast":        20,     # المتوسط السريع
    "ma_slow":        50,     # المتوسط البطيء
    "warmup_candles": 100,    # شموع الإحماء
    "default_tf":     "1h",   # الإطار الزمني الافتراضي
    "rsi_oversold":   30,     # RSI: ذروة بيع (تحسين من 35)
    "rsi_overbought": 70,     # RSI: ذروة شراء
}

# ── رسائل النظام (عربية بالكامل) ────────────────────────────────────────────
MSG = {
    "unauthorized":   (
        f"{E['lock']} عذراً، هذا الأمر غير متاح في باقتك الحالية.\n"
        f"للترقية اكتب: /upgrade"
    ),
    "error":          (
        f"{E['error']} حدث خطأ غير متوقع.\n"
        f"يرجى المحاولة مرة أخرى أو التواصل مع الدعم."
    ),
    "rate_limit":     f"{E['warn']} أرسلت طلبات كثيرة. يرجى الانتظار دقيقة.",
    "banned":         (
        f"{E['lock']} تم إيقاف حسابك مؤقتاً بسبب نشاط مشبوه.\n"
        f"تواصل مع الدعم للإلغاء."
    ),
    "maintenance":    f"{E['settings']} رائد في وضع الصيانة. نعود قريباً!",
    "no_data":        f"{E['warn']} البيانات غير متاحة حالياً. أعد المحاولة بعد دقيقة.",
    "phishing_warn":  (
        f"{E['lock']} تحذير أمني!\n\n"
        f"رائد لن يطلب أبداً:\n"
        f"• كلمة مرور أو رمز 2FA\n"
        f"• تحويل أموال\n"
        f"• النقر على روابط خارجية\n"
        f"• مفاتيح محفظتك الخاصة\n\n"
        f"أي رسالة بهذا المحتوى = احتيال!"
    ),
    "disclaimer":     (
        f"{E['warn']} *إخلاء المسؤولية*\n"
        f"جميع التحليلات والإشارات استرشادية فقط.\n"
        f"لا تُعدّ نصيحة مالية أو استثمارية.\n"
        f"القرار النهائي يعود للمستخدم.\n"
        f"التداول ينطوي على مخاطر — استثمر بحكمة."
    ),
}

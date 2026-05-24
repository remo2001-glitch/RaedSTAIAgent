"""
🤖 رائد التداول الذكي — الإعدادات المركزية
مبني على NexusTrader (MIT) — Quantweb3
تطوير: فريق رائد التداول الذكي
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── هوية رائد ──────────────────────────────────────────────────────────────
RAED_NAME      = "🤖 رائد التداول الذكي"
RAED_VERSION   = "1.0.0"
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
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))
REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379")
ENCRYPTION_KEY  = os.getenv("ENCRYPTION_KEY", "").encode()
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
        "price_coins":  5,
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
        "price_coins":  50,
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
        "price_coins":  200,
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
        "price_coins":  999,
        "support":      "مباشر 24/7",
    },
}

# ── المحفظة الافتراضية ──────────────────────────────────────────────────────
VIRTUAL_WALLET_START = 10_000.0  # دولار

# ── حدود المخاطر ────────────────────────────────────────────────────────────
RISK = {
    "max_loss_pct":       2.0,   # وقف الخسارة: 2%
    "take_profit_pct":    4.0,   # هدف الربح: 4%
    "max_position_pct":   10.0,  # أقصى حجم صفقة: 10% من المحفظة
    "max_open_trades":    5,     # أقصى صفقات مفتوحة
    "max_leverage":       3,     # أقصى رافعة مالية
}

# ── مؤشرات الاستراتيجية ─────────────────────────────────────────────────────
STRATEGY = {
    "ma_fast":       20,    # المتوسط السريع
    "ma_slow":       50,    # المتوسط البطيء
    "warmup_candles": 100,  # شموع الإحماء
    "default_tf":    "1h",  # الإطار الزمني الافتراضي
}

# ── رسائل النظام ────────────────────────────────────────────────────────────
MSG = {
    "unauthorized": f"{E['lock']} عذراً، هذا الأمر غير متاح في باقتك الحالية.\nللترقية: /upgrade",
    "error":        f"{E['error']} حدث خطأ. يرجى المحاولة مرة أخرى أو التواصل مع الدعم.",
    "rate_limit":   f"{E['warn']} أرسلت طلبات كثيرة. يرجى الانتظار دقيقة.",
    "banned":       f"{E['lock']} تم إيقاف حسابك مؤقتاً بسبب نشاط مشبوه. تواصل مع الدعم.",
    "maintenance":  f"{E['settings']} رائد في وضع الصيانة. نعود قريباً!",
    "phishing_warn": (
        f"{E['lock']} تحذير أمني!\n\n"
        f"رائد لن يطلب أبداً:\n"
        f"• كلمة مرور أو رمز 2FA\n"
        f"• تحويل أموال\n"
        f"• النقر على روابط خارجية\n"
        f"• مفاتيح محفظتك الخاصة\n\n"
        f"أي رسالة بهذا المحتوى = احتيال!"
    ),
}

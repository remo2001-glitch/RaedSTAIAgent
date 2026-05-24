"""
🤖 رائد التداول الذكي — نظام الأمان والحماية
طبقات متعددة: تشفير + anti-phishing + rate limiting + تعلم ذاتي
"""

import re
from cryptography.fernet import Fernet
from loguru import logger
from core.config import ENCRYPTION_KEY, RATE_LIMIT, MAX_FAILS, BAN_HOURS, E, MSG


# ── مصطلحات الاحتيال المشبوهة ───────────────────────────────────────────────
PHISHING_PATTERNS = [
    # طلب كلمات مرور
    r"كلمة\s*المرور", r"password", r"passwd", r"2fa", r"رمز التحقق",
    # طلب مفاتيح خاصة
    r"private\s*key", r"مفتاح\s*خاص", r"seed\s*phrase", r"عبارة\s*الاسترداد",
    r"12\s*كلمة", r"24\s*كلمة", r"mnemonic",
    # تحويل أموال
    r"أرسل\s*\d+", r"حوّل\s*\d+", r"send\s*\d+",
    r"محفظة\s*مجانية", r"free\s*crypto", r"ضاعف\s*ربحك",
    r"doubl.*crypto", r"guaranteed\s*profit",
    # روابط مشبوهة
    r"click\s*here", r"انقر\s*هنا", r"verify.*account",
    r"urgent.*action", r"عاجل.*تحقق",
    # ادعاء الدعم الفني
    r"admin.*here", r"support.*team", r"مسؤول\s*هنا",
    r"telegram.*support", r"customer.*service.*ask",
]

# تجميع الأنماط في regex واحد
_PHISHING_RE = re.compile(
    "|".join(PHISHING_PATTERNS),
    re.IGNORECASE | re.UNICODE
)

# مفتاح التشفير
try:
    _cipher = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None
except Exception:
    _cipher = None
    logger.warning("⚠️ مفتاح التشفير غير صالح — التشفير معطل")


# ── التشفير وفك التشفير ─────────────────────────────────────────────────────

def encrypt(text: str) -> str:
    """تشفير نص (مفاتيح API)"""
    if not _cipher or not text:
        return text
    try:
        return _cipher.encrypt(text.encode()).decode()
    except Exception as e:
        logger.error(f"خطأ في التشفير: {e}")
        return ""

def decrypt(token: str) -> str:
    """فك تشفير نص"""
    if not _cipher or not token:
        return token
    try:
        return _cipher.decrypt(token.encode()).decode()
    except Exception as e:
        logger.error(f"خطأ في فك التشفير: {e}")
        return ""

def generate_key() -> str:
    """توليد مفتاح تشفير جديد"""
    return Fernet.generate_key().decode()


# ── كشف الاحتيال والتصيد ───────────────────────────────────────────────────

def is_phishing(text: str) -> tuple[bool, str]:
    """
    يفحص النص للكشف عن محاولات الاحتيال
    Returns: (is_suspicious, matched_pattern)
    """
    if not text:
        return False, ""
    match = _PHISHING_RE.search(text)
    if match:
        return True, match.group(0)
    return False, ""

def sanitize_input(text: str) -> str:
    """تنظيف المدخلات من الأحرف الخطيرة"""
    if not text:
        return ""
    # إزالة HTML/Markdown injection
    dangerous = ["<", ">", "&", "javascript:", "data:", "vbscript:"]
    for d in dangerous:
        text = text.replace(d, "")
    return text.strip()[:500]  # حد أقصى 500 حرف


# ── التحقق من صحة مفاتيح API ───────────────────────────────────────────────

def validate_api_key(key: str, exchange: str) -> tuple[bool, str]:
    """التحقق الأساسي من شكل مفتاح API"""
    if not key or len(key) < 16:
        return False, "المفتاح قصير جداً"

    patterns = {
        "binance": r"^[A-Za-z0-9]{64}$",
        "okx":     r"^[a-f0-9\-]{36}$",
        "bybit":   r"^[A-Za-z0-9]{18,36}$",
        "bitget":  r"^bg_[A-Za-z0-9]{48}$",
    }

    pattern = patterns.get(exchange.lower())
    if pattern and not re.match(pattern, key):
        return False, f"شكل المفتاح غير صحيح لـ {exchange}"

    return True, "صالح"


# ── decorator للتحقق من الأمان ──────────────────────────────────────────────

def security_check(func):
    """
    Decorator يُضاف على handlers لفحص:
    1. هل المستخدم محظور؟
    2. هل تجاوز حد الطلبات؟
    3. هل الرسالة تحتوي على محتوى مشبوه؟
    """
    from functools import wraps

    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        from core.database import db

        user_id = update.effective_user.id
        message_text = ""
        if update.message and update.message.text:
            message_text = update.message.text

        # 1. فحص الحظر
        if await db.is_banned(user_id):
            await update.effective_message.reply_text(MSG["banned"])
            return

        # 2. فحص Rate Limit
        allowed = await db.check_rate_limit(user_id, RATE_LIMIT)
        if not allowed:
            await update.effective_message.reply_text(MSG["rate_limit"])
            logger.warning(f"⏱️ Rate limit: {user_id}")
            return

        # 3. فحص التصيد في الرسائل الواردة
        if message_text:
            suspicious, pattern = is_phishing(message_text)
            if suspicious:
                await update.effective_message.reply_text(MSG["phishing_warn"])
                await db.log_blocked_pattern(f"{user_id}:{pattern}")
                logger.warning(f"🚨 نمط مشبوه من {user_id}: {pattern}")
                # زيادة عداد المحاولات المشبوهة
                fails = await db.increment_fails(user_id)
                if fails >= MAX_FAILS:
                    await db.ban_user(user_id, BAN_HOURS)
                    logger.warning(f"🔒 حظر مؤقت: {user_id} ({fails} محاولة)")
                return

        return await func(update, context, *args, **kwargs)

    return wrapper


# ── التحقق من صلاحيات الباقة ────────────────────────────────────────────────

def requires_plan(*plans):
    """Decorator للتحقق أن المستخدم في الباقة المطلوبة"""
    from functools import wraps
    from core.config import PLANS

    plan_order = ["free", "silver", "gold", "diamond"]

    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            from core.database import db
            user_id = update.effective_user.id
            user = await db.get_user(user_id)
            if not user:
                await update.effective_message.reply_text(MSG["error"])
                return

            user_plan = user.get("plan", "free")
            user_level = plan_order.index(user_plan)
            required_level = min(plan_order.index(p) for p in plans if p in plan_order)

            if user_level < required_level:
                needed = plans[0]
                plan_info = PLANS.get(needed, {})
                msg = (
                    f"{E['lock']} هذه الميزة متاحة في {plan_info.get('name', needed)} "
                    f"فأعلى.\n\n"
                    f"باقتك الحالية: {PLANS[user_plan]['name']}\n"
                    f"للترقية: /upgrade"
                )
                await update.effective_message.reply_text(msg)
                return

            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def requires_admin(func):
    """Decorator للأوامر المخصصة للمشرف فقط"""
    from functools import wraps
    from core.config import ADMIN_ID

    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        if update.effective_user.id != ADMIN_ID:
            await update.effective_message.reply_text(
                f"{E['lock']} هذا الأمر للمشرف فقط."
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

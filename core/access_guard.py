"""
core/access_guard.py — حماية البوت (الدفعة 69، قرارات رحال 2026-11-08)

  • وضع خاص: لا يستخدم البوت إلا المالك والمدراء ومن يضيفه المدير (/access add). الباقي رسالة ثابتة بلا معالجة.
  • البوت يعمل في المحادثات الخاصة فقط؛ يغادر أي مجموعة أو قناة فوراً (بلا استثناء) ويُشعر المالك.
  • حد يومي 5 لأوامر التحليل لكل مستخدم غير مدير (يتجدد 00:00 KSA) + قيود إغراق (حظر مؤقت).
  • أوامر المدير والأوامر الفنية مخفية: لغير صاحبها تُتجاهل كأنها غير موجودة (بلا «للمدير فقط»)،
    وتُسجَّل المحاولات ويُشعَر المالك عند تكرارها.
  • لا رسائل لمستخدم غير مسموح (فلتر الإرسال) ولا نص أخطاء برمجية في أي رسالة (منقّي الإرسال).
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Set

logger = logging.getLogger("RAED.access")

KSA = timezone(timedelta(hours=3))

DAILY_ANALYSIS_LIMIT = 5
FLOOD_WINDOW_SEC = 20
FLOOD_MAX_UPDATES = 6
FLOOD_MUTE_SEC = 600
PROBE_NOTIFY_AFTER = 3          # محاولات أوامر مخفية في اليوم قبل إشعار المالك
DENY_REPLY_EVERY_SEC = 12 * 3600

# أوامر فنية — للمالك فقط
OWNER_COMMANDS = {"killswitch", "drift", "whaletest", "survey_report", "review"}
# أوامر إدارة — للمالك والمدراء
ADMIN_COMMANDS = {"access"}
# أوامر لها خيارات إدارية (الأمر نفسه عام، والخيار للمدراء)
ADMIN_SUBCOMMANDS = {
    "premium": lambda args: bool(args),                                         # /premium بلا خيارات = ملفك
    "autotrade": lambda args: bool(args) and (args[0].lower() == "users" or
                                              (args[0].lower() in ("on", "off") and len(args) >= 2)),
    "autostats": lambda args: bool(args),
}
# أوامر التحليل — يُحتسب عليها الحد اليومي
ANALYSIS_COMMANDS = {
    "signal", "quicksignal", "analyze", "planweek", "planmonth", "planweek_full", "planmonth_full",
    "trade_plan", "trade_plan_month", "news", "onchain", "liquidity", "regime", "outlook", "backtest",
    "chart", "events", "marketrisk",
}

_ALLOW_KEY = "access_allow_b69"


def _sm():
    from core.state_manager import state_manager
    return state_manager


# ── الهوية ───────────────────────────────────────────────────
def owner_id() -> int:
    try:
        from core.admin_ids import get_primary_admin_id
        return int(get_primary_admin_id())
    except Exception:
        return 0


def admin_ids() -> Set[int]:
    ids: Set[int] = set()
    try:
        from core.admin_ids import get_admin_ids
        ids |= set(int(x) for x in get_admin_ids())
    except Exception:
        pass
    try:
        from core.config import ADMIN_ID
        if int(ADMIN_ID or 0) > 0:
            ids.add(int(ADMIN_ID))
    except Exception:
        pass
    o = owner_id()
    if o:
        ids.add(o)
    return ids


def all_admins() -> Set[int]:
    """أرقام المدراء من البيئة + من يحمل باقة admin في الحالة."""
    ids = set(admin_ids())
    try:
        ids |= {int(u) for u in (_sm().get_all_user_ids() or []) if _sm().get_tier(u) == "admin"}
    except Exception:
        pass
    return ids


def is_owner(uid) -> bool:
    try:
        return int(uid) == owner_id() and owner_id() > 0
    except Exception:
        return False


def is_admin(uid) -> bool:
    """المصدر الموحّد لـ«المدير»: المالك، أو أرقام المدراء في البيئة، أو باقة admin."""
    try:
        uid = int(uid)
    except Exception:
        return False
    if uid in admin_ids():
        return True
    try:
        return _sm().get_tier(uid) == "admin"
    except Exception:
        return False


# ── قائمة السماح ─────────────────────────────────────────────
def allow_list() -> Set[int]:
    try:
        raw = _sm().kv_get(_ALLOW_KEY, "") or ""
        return set(int(x) for x in json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _save_allow(s: Iterable[int]) -> None:
    try:
        _sm().kv_set(_ALLOW_KEY, json.dumps(sorted(int(x) for x in s)))
    except Exception as e:
        logger.warning(f"access save: {type(e).__name__}")


def allow_add(uid: int) -> None:
    s = allow_list(); s.add(int(uid)); _save_allow(s)


def allow_remove(uid: int) -> None:
    s = allow_list(); s.discard(int(uid)); _save_allow(s)


def is_allowed(uid) -> bool:
    try:
        uid = int(uid)
    except Exception:
        return False
    return is_admin(uid) or uid in allow_list()


# ── الحد اليومي ──────────────────────────────────────────────
def _day_key() -> str:
    return datetime.now(KSA).strftime("%Y%m%d")


def quota_used(uid: int) -> int:
    try:
        return int(_sm().kv_get(f"quota_b69:{int(uid)}:{_day_key()}", 0) or 0)
    except Exception:
        return 0


def quota_take(uid: int) -> bool:
    """يحجز تحليلاً من حد اليوم؛ False إن نفد. المدراء بلا حد."""
    if is_admin(uid):
        return True
    n = quota_used(uid)
    if n >= DAILY_ANALYSIS_LIMIT:
        return False
    try:
        _sm().kv_set(f"quota_b69:{int(uid)}:{_day_key()}", n + 1)
    except Exception:
        pass
    return True


# ── الإغراق ──────────────────────────────────────────────────
_hits = {}        # uid → [أوقات]
_muted = {}       # uid → حتى
_deny_last = {}   # uid → آخر رد «غير متاح»
_probes = {}      # (uid, يوم) → عدد
_probe_notified = set()


def flood_check(uid: int, now: Optional[float] = None):
    """يُرجع (مسموح، أُغلق-الآن). المدراء مستثنون."""
    now = now or time.time()
    if is_admin(uid):
        return True, False
    if _muted.get(uid, 0) > now:
        return False, False
    h = [t for t in _hits.get(uid, []) if now - t < FLOOD_WINDOW_SEC]
    h.append(now)
    _hits[uid] = h
    if len(h) > FLOOD_MAX_UPDATES:
        _muted[uid] = now + FLOOD_MUTE_SEC
        _hits[uid] = []
        return False, True
    return True, False


def should_reply_deny(uid: int, now: Optional[float] = None) -> bool:
    now = now or time.time()
    if now - _deny_last.get(uid, 0) < DENY_REPLY_EVERY_SEC:
        return False
    _deny_last[uid] = now
    return True


def record_probe(uid: int) -> bool:
    """يسجّل محاولة أمر مخفي؛ True حين يجب إشعار المالك (مرة يومياً لكل مستخدم)."""
    k = (int(uid), _day_key())
    _probes[k] = _probes.get(k, 0) + 1
    if _probes[k] >= PROBE_NOTIFY_AFTER and k not in _probe_notified:
        _probe_notified.add(k)
        return True
    return False


# ── تصنيف الأوامر ────────────────────────────────────────────
def parse_command(text: str):
    """'/Signal@RaedBot BTC' → ('signal', ['BTC'])؛ None لغير الأوامر."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return None, []
    parts = t.split()
    cmd = parts[0][1:].split("@", 1)[0].lower()
    return cmd, parts[1:]


def command_visible(uid: int, cmd: str, args) -> bool:
    """هل الأمر موجود لهذا المستخدم؟ (الأوامر المخفية تُتجاهل كأنها غير موجودة)."""
    if cmd in OWNER_COMMANDS:
        return is_owner(uid)
    if cmd in ADMIN_COMMANDS:
        return is_admin(uid)
    f = ADMIN_SUBCOMMANDS.get(cmd)
    if f and f(list(args or [])):
        return is_admin(uid)
    return True


# ── منقّي نص الأخطاء البرمجية ─────────────────────────────────
_ERR_RX = re.compile(
    r"(Traceback \(most recent call last\)|\b[A-Z][A-Za-z]+(Error|Exception)\b|"
    r"object has no attribute|NoneType|unsupported operand|not subscriptable|"
    r"invalid literal for|takes \d+ positional|unexpected keyword argument|"
    r"\bFile \"[^\"]+\.py\"|line \d+, in )")


_GENERIC = "⚠️ تعذّر إكمال الطلب الآن — حاول مرة أخرى بعد قليل."


def sanitize_text(text: str) -> str:
    """كل سطر فيه نص خطأ برمجي يُستبدل برسالة عامة (التفاصيل في السجل فقط)؛ بقية الرسالة كما هي."""
    try:
        if not text or not _ERR_RX.search(str(text)):
            return text
        logger.warning(f"access: نص خطأ برمجي حُجب من رسالة: {str(text)[:300]!r}")
        out, done = [], False
        for ln in str(text).split("\n"):
            if _ERR_RX.search(ln):
                if not done:
                    out.append(_GENERIC)
                    done = True
                continue
            out.append(ln)
        res = "\n".join(out).strip()
        return res or _GENERIC
    except Exception:
        return text


# ── قوائم أوامر Telegram ─────────────────────────────────────
PUBLIC_MENU = [
    ("start", "بدء التشغيل"), ("quicksignal", "تحليل أولي سريع"), ("signal", "إشارة تداول شاملة"),
    ("news", "تحليل الأخبار"), ("regime", "حالة السوق"), ("onchain", "تحليل On-Chain"),
    ("liquidity", "تحليل السيولة"), ("backtest", "اختبار تاريخي"), ("analyze", "تحليل عميق"),
    ("planmonth", "خطة شهرية"), ("planweek", "خطة أسبوعية"), ("portfolio", "توزيع المحفظة"),
    ("autotrade", "تداول تلقائي on/off"), ("autostats", "نتائج التداول الآلي"),
    ("execute", "تنفيذ فوري"), ("virtual", "تداول افتراضي"),
    ("stats", "إحصائيات حسابك"), ("performance", "أداء الإشارات حسب النوع"), ("risk", "حالة مخاطر المحفظة"),
    ("marketrisk", "تقييم مخاطر السوق"), ("events", "الأحداث القادمة"), ("outlook", "رؤية المؤسسات — BlackRock"),
    ("about", "عن رائد"), ("setportfolio", "ضبط حجم محفظتك"), ("live", "التداول الحقيقي"),
    ("trades", "صفقاتي الحقيقية"), ("premium", "باقتك"), ("upgrade", "الباقات والأسعار"),
    ("chart", "تحليل شارت بصري"),
]
ADMIN_MENU_EXTRA = [("access", "إدارة الحسابات المسموح لها")]
OWNER_MENU_EXTRA = [("killswitch", "Kill Switch"), ("drift", "حالة النموذج"), ("review", "المراجعة الأسبوعية"),
                    ("survey_report", "تقرير الاستبيان"), ("whaletest", "اختبار Whale")]


async def apply_command_menus(bot) -> None:
    """القائمة العامة بلا أوامر المدراء؛ المدراء والمالك قوائم خاصة بمحادثاتهم فقط."""
    from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, BotCommandScopeAllPrivateChats
    pub = [BotCommand(c, d) for c, d in PUBLIC_MENU]
    await bot.set_my_commands(pub, scope=BotCommandScopeDefault())
    try:
        await bot.set_my_commands(pub, scope=BotCommandScopeAllPrivateChats())
    except Exception:
        pass
    for a in sorted(all_admins()):
        extra = ADMIN_MENU_EXTRA + (OWNER_MENU_EXTRA if is_owner(a) else [])
        try:
            await bot.set_my_commands(pub + [BotCommand(c, d) for c, d in extra], scope=BotCommandScopeChat(a))
        except Exception as e:   # المدير لم يبدأ محادثة مع البوت بعد
            logger.info(f"access menu {a}: {type(e).__name__}")


DENY_TEXT = "🔒 رائد في مرحلة تشغيل خاصة حالياً، وغير متاح للاستخدام العام."


# ── البوت المحروس: فلتر الإرسال + منقّي الأخطاء (نقطة واحدة لكل رسائل البوت) ──
def make_guarded_bot_class():
    from telegram.ext import ExtBot

    class GuardedBot(ExtBot):
        """كل send/edit يمر من هنا: لا رسالة لحساب غير مسموح، ولا نص خطأ برمجي لأي مستخدم."""

        @staticmethod
        def _blocked(chat_id, text=None) -> bool:
            try:
                cid = int(chat_id)
            except Exception:
                return False          # @username أو قناة باسمها — لا يُرسل البوت لها أصلاً
            if cid < 0:
                return True           # مجموعات وقنوات: لا إرسال أبداً
            if text is not None and str(text) == DENY_TEXT:
                return False
            return not is_allowed(cid)

        async def send_message(self, chat_id, text, *args, **kwargs):
            if self._blocked(chat_id, text):
                logger.info(f"access: إرسال محجوب إلى {chat_id}")
                return None
            return await super().send_message(chat_id, sanitize_text(text), *args, **kwargs)

        async def edit_message_text(self, text, *args, **kwargs):
            return await super().edit_message_text(sanitize_text(text), *args, **kwargs)

        async def send_photo(self, chat_id, photo, *args, **kwargs):
            if self._blocked(chat_id):
                return None
            if kwargs.get("caption"):
                kwargs["caption"] = sanitize_text(kwargs["caption"])
            return await super().send_photo(chat_id, photo, *args, **kwargs)

        async def send_document(self, chat_id, document, *args, **kwargs):
            if self._blocked(chat_id):
                return None
            if kwargs.get("caption"):
                kwargs["caption"] = sanitize_text(kwargs["caption"])
            return await super().send_document(chat_id, document, *args, **kwargs)

        async def answer_callback_query(self, callback_query_id, text=None, *args, **kwargs):
            return await super().answer_callback_query(
                callback_query_id, sanitize_text(text) if text else text, *args, **kwargs)

    return GuardedBot

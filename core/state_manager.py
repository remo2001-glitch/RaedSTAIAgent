"""
💾 رائد — State Manager v3 (Redis)
الحل الجذري للاستمرارية عبر Railway deploys.

طبقات التخزين (بالأولوية):
١. Redis → دائم عبر جميع Deploys ✅
٢. ENV Variables → OWNER_CHAT_ID, ADMIN_USERS, PREMIUM_USERS
٣. ملف محلي → fallback عند غياب Redis

Redis يُستخدم لكل شيء:
- باقات المستخدمين (tier)
- إعدادات التداول (autotrade, futures, margin)
- حجم المحفظة
- أول استخدام (للتداول المجاني)
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'bot_state.json')

TIERS = {
    "free":    {"name": "🆓 مجاني",  "coins": 15,  "level": 0},
    "silver":  {"name": "🥈 فضي",    "coins": 35,  "level": 1},
    "gold":    {"name": "🥇 ذهبي",   "coins": 100, "level": 2},
    "diamond": {"name": "💎 ماسي",   "coins": 300, "level": 3},
    "admin":   {"name": "👑 مدير",   "coins": 300, "level": 99},
}

CMD_TIER = {
    "start": "free", "help": "free", "live": "free",
    "setportfolio": "free", "trades": "free", "premium": "free",
    "about": "free", "upgrade": "free", "quicksignal": "free",
    "portfolio": "free",
    "signal": "silver", "news": "silver", "regime": "silver",
    "backtest": "silver", "stats": "silver", "events": "silver",
    "autotrade": "silver", "execute": "silver", "risk": "silver",
    "approve": "silver", "reject": "silver",
    "analyze": "gold", "liquidity": "gold", "onchain": "gold",
    "planweek": "gold", "planmonth": "gold", "drift": "gold",
    "chart": "diamond",
    "killswitch": "admin",
}

TIER_EXCHANGES = {
    "free":    ["okx"],
    "silver":  ["okx"],
    "gold":    ["okx", "binance", "bybit", "bitget", "mexc"],
    "diamond": ["okx", "binance", "bybit", "bitget", "mexc"],
    "admin":   ["okx", "binance", "bybit", "bitget", "mexc"],
}

# Redis key prefix
_PREFIX = "raed:user:"
_ALL_USERS_KEY = "raed:all_users"


class StateManager:
    """
    يُدير حالة رائد — Redis أولاً، ملف محلي fallback.
    جميع البيانات تبقى بعد كل Deploy.
    """

    def __init__(self):
        self._redis = None
        self._state: Dict[str, Any] = {"users": {}}
        self._redis_ok = False
        self._init_redis()
        self._load_fallback()
        self._apply_env_vars()

    # ═══════════════════════════════════════════════════════════
    # Redis — التهيئة
    # ═══════════════════════════════════════════════════════════
    def _init_redis(self):
        """يُهيّئ اتصال Redis."""
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if not redis_url:
            logger.warning("REDIS_URL غير موجود — سيُستخدم الملف المحلي")
            return
        try:
            import redis as redis_lib
            self._redis = redis_lib.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # اختبار الاتصال
            self._redis.ping()
            self._redis_ok = True
            logger.info("✅ Redis متصل — البيانات دائمة")
        except ImportError:
            logger.warning("مكتبة redis غير مُثبَّتة — pip install redis")
        except Exception as e:
            logger.error(f"Redis connection error: {e} — fallback للملف")

    # ═══════════════════════════════════════════════════════════
    # Redis — عمليات المستخدم
    # ═══════════════════════════════════════════════════════════
    def _redis_get_user(self, user_id: int) -> dict:
        """يجلب بيانات المستخدم من Redis."""
        if not self._redis_ok:
            return {}
        try:
            key  = f"{_PREFIX}{user_id}"
            data = self._redis.get(key)
            return json.loads(data) if data else {}
        except Exception as e:
            logger.warning(f"Redis get_user {user_id}: {e}")
            return {}

    def _redis_set_user(self, user_id: int, data: dict):
        """يحفظ بيانات المستخدم في Redis."""
        if not self._redis_ok:
            return
        try:
            key = f"{_PREFIX}{user_id}"
            self._redis.set(key, json.dumps(data, ensure_ascii=False))
            # إضافة للقائمة العامة
            self._redis.sadd(_ALL_USERS_KEY, str(user_id))
        except Exception as e:
            logger.warning(f"Redis set_user {user_id}: {e}")

    def _redis_get_all_users(self) -> list:
        """يجلب قائمة جميع المستخدمين من Redis."""
        if not self._redis_ok:
            return []
        try:
            members = self._redis.smembers(_ALL_USERS_KEY)
            return [int(m) for m in members if m.isdigit()]
        except Exception as e:
            logger.warning(f"Redis get_all_users: {e}")
            return []

    # ═══════════════════════════════════════════════════════════
    # تحميل البيانات
    # ═══════════════════════════════════════════════════════════
    def _load_fallback(self):
        """يُحمِّل من الملف المحلي كـ fallback."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # ترحيل البيانات من الملف إلى Redis
                if self._redis_ok and "users" in data:
                    migrated = 0
                    for uid_str, ud in data["users"].items():
                        if ud.get("tier") and ud["tier"] != "free":
                            uid = int(uid_str)
                            existing = self._redis_get_user(uid)
                            if not existing.get("tier") or existing.get("tier") == "free":
                                self._redis_set_user(uid, ud)
                                migrated += 1
                    if migrated:
                        logger.info(f"✅ Redis: ترحيل {migrated} مستخدم من الملف")
                else:
                    self._state = data
        except Exception as e:
            logger.warning(f"fallback load: {e}")

    def _apply_env_vars(self):
        """يُطبّق OWNER + ADMIN + PREMIUM من ENV."""
        owner = self._get_owner_id()
        if owner:
            ud = self._get_user(owner)
            ud["tier"] = "admin"
            ud["tier_by"] = "env_owner"
            self._set_user(owner, ud)

        for uid in self._get_admin_ids():
            ud = self._get_user(uid)
            if ud.get("tier") != "admin":
                ud["tier"] = "admin"
                ud["tier_by"] = "env_admin"
                self._set_user(uid, ud)

        for uid in self._get_premium_env_ids():
            ud = self._get_user(uid)
            if not ud.get("tier") or ud.get("tier") == "free":
                ud["tier"] = "silver"
                ud["tier_by"] = "env_premium"
                self._set_user(uid, ud)

    # ═══════════════════════════════════════════════════════════
    # Helpers — قراءة/كتابة موحَّدة (Redis أولاً، ملف fallback)
    # ═══════════════════════════════════════════════════════════
    def _get_user(self, user_id: int) -> dict:
        if self._redis_ok:
            return self._redis_get_user(user_id)
        return dict(self._state.setdefault("users", {}).get(str(user_id), {}))

    def _set_user(self, user_id: int, data: dict):
        if self._redis_ok:
            self._redis_set_user(user_id, data)
        # دائماً نحفظ في الملف كـ backup
        self._state.setdefault("users", {})[str(user_id)] = data
        self.save()

    def save(self):
        """يحفظ في الملف المحلي كـ backup."""
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"state save error: {e}")

    def initialize_owner(self):
        """يُشغَّل عند startup."""
        self._apply_env_vars()
        logger.info(f"✅ StateManager v3 (Redis={'✅' if self._redis_ok else '❌ fallback'})")

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def get_tier(self, user_id: int) -> str:
        # ١. المالك من ENV
        owner = self._get_owner_id()
        if owner and owner == user_id:
            return "admin"
        # ٢. ADMIN_USERS من ENV
        if user_id in self._get_admin_ids():
            return "admin"
        # ٣. Redis / ملف
        ud   = self._get_user(user_id)
        tier = ud.get("tier", "")
        if tier in TIERS and tier != "free":
            return tier
        # ٤. PREMIUM_USERS من ENV
        if user_id in self._get_premium_env_ids():
            return "silver"
        return "free"

    def set_tier(self, user_id: int, tier: str,
                  by: str = "admin", requester_id: int = 0) -> bool:
        if tier not in TIERS:
            return False
        if requester_id != 0:
            if self.get_tier(requester_id) != "admin":
                return False
            owner = self._get_owner_id()
            if owner and user_id == owner:
                return False
        ud = self._get_user(user_id)
        ud["tier"]       = tier
        ud["tier_by"]    = by
        ud["tier_since"] = time.time()
        self._set_user(user_id, ud)
        logger.info(f"✅ tier: {user_id} → {tier} by {by}")
        return True

    def is_premium(self, user_id: int) -> bool:
        return TIERS[self.get_tier(user_id)]["level"] >= 1

    def set_premium(self, user_id: int, is_premium: bool, by: str = "admin"):
        self.set_tier(user_id, "silver" if is_premium else "free", by=by)

    def can_use_command(self, user_id: int, command: str) -> bool:
        req  = CMD_TIER.get(command, "free")
        tier = self.get_tier(user_id)
        return TIERS[tier]["level"] >= TIERS[req]["level"]

    def get_blocked_reason(self, user_id: int, command: str) -> str:
        req  = CMD_TIER.get(command, "free")
        name = TIERS[req]["name"]
        return f"🔒 هذا الأمر يتطلب *{name}*\n\n/upgrade لعرض الباقات"

    def coin_limit(self, user_id: int) -> int:
        return TIERS[self.get_tier(user_id)]["coins"]

    def allowed_exchanges(self, user_id: int) -> List[str]:
        return TIER_EXCHANGES.get(self.get_tier(user_id), ["okx"])

    def can_use_exchange(self, user_id: int, exchange: str) -> bool:
        return exchange.lower() in self.allowed_exchanges(user_id)

    # ═══════════════════════════════════════════════════════════
    # إعدادات التداول
    # ═══════════════════════════════════════════════════════════
    def get_futures_enabled(self, user_id: int) -> bool:
        if TIERS[self.get_tier(user_id)]["level"] < 2:
            return False
        return self._get_user(user_id).get("futures_enabled", False)

    def set_futures_enabled(self, user_id: int, enabled: bool):
        if TIERS[self.get_tier(user_id)]["level"] < 2:
            return
        ud = self._get_user(user_id)
        ud["futures_enabled"] = enabled
        self._set_user(user_id, ud)

    def get_margin_enabled(self, user_id: int) -> bool:
        if TIERS[self.get_tier(user_id)]["level"] < 2:
            return False
        return self._get_user(user_id).get("margin_enabled", False)

    def set_margin_enabled(self, user_id: int, enabled: bool):
        if TIERS[self.get_tier(user_id)]["level"] < 2:
            return
        ud = self._get_user(user_id)
        ud["margin_enabled"] = enabled
        self._set_user(user_id, ud)

    def is_autotrade_on(self, user_id: int) -> bool:
        return bool(self._get_user(user_id).get("autotrade_on", False))

    def set_autotrade_on(self, user_id: int, enabled: bool):
        ud = self._get_user(user_id)
        ud["autotrade_on"] = enabled
        self._set_user(user_id, ud)

    def can_use_autotrade_free(self, user_id: int) -> bool:
        if self.get_tier(user_id) != "free":
            return True
        return self.get_free_autotrade_days(user_id) > 0

    def get_free_autotrade_days(self, user_id: int) -> int:
        ud    = self._get_user(user_id)
        first = ud.get("first_use", 0)
        if not first:
            ud["first_use"] = time.time()
            self._set_user(user_id, ud)
            first = ud["first_use"]
        elapsed = (time.time() - first) / 86400
        return max(0, 30 - int(elapsed))

    # ═══════════════════════════════════════════════════════════
    # المحفظة
    # ═══════════════════════════════════════════════════════════
    def get_user_portfolio(self, user_id: int, default: float = 10000) -> float:
        owner = self._get_owner_id()
        if owner and user_id == owner:
            return float(os.environ.get("PORTFOLIO_SIZE", default))
        return float(self._get_user(user_id).get("portfolio", default))

    def set_user_portfolio(self, user_id: int, amount: float):
        ud = self._get_user(user_id)
        ud["portfolio"] = amount
        self._set_user(user_id, ud)

    # ═══════════════════════════════════════════════════════════
    # قوائم المستخدمين
    # ═══════════════════════════════════════════════════════════
    def get_all_user_ids(self) -> list:
        if self._redis_ok:
            return self._redis_get_all_users()
        return [int(uid) for uid in self._state.get("users", {}).keys()
                if str(uid).isdigit()]

    def get_autotrade_users(self) -> list:
        result = []
        for uid in self.get_all_user_ids():
            try:
                if self._get_user(uid).get("autotrade_on"):
                    result.append(uid)
            except Exception:
                pass
        return result

    def list_premium_users(self) -> List[dict]:
        result = []
        for uid in self.get_all_user_ids():
            tier = self.get_tier(uid)
            if tier != "free":
                result.append({"user_id": uid, "tier": tier})
        return result

    # ═══════════════════════════════════════════════════════════
    # تنسيق
    # ═══════════════════════════════════════════════════════════
    def format_profile_ar(self, user_id: int) -> str:
        tier      = self.get_tier(user_id)
        tier_info = TIERS[tier]
        owner     = self._get_owner_id()
        is_owner  = owner and user_id == owner
        futures   = self.get_futures_enabled(user_id)
        margin    = self.get_margin_enabled(user_id)
        portfolio = self.get_user_portfolio(user_id)
        lines = [
            f"👤 *ملف المستخدم*",
            f"• المعرّف: `{user_id}`",
            f"• الباقة: {'👑 مالك' if is_owner else tier_info['name']}",
            f"• حجم المحفظة: ${portfolio:,.0f}",
            f"• حد العملات: {tier_info['coins']} عملة",
            f"• المنصات: {', '.join(self.allowed_exchanges(user_id)).upper()}",
            f"• Futures: {'✅' if futures else '❌'}",
            f"• Margin: {'✅' if margin else '❌'}",
            f"• التخزين: {'🔴 Redis' if self._redis_ok else '🟡 ملف محلي'}",
        ]
        return "\n".join(lines)

    def get_commands_for_user(self, user_id: int) -> dict:
        tier = self.get_tier(user_id)
        categories = {
            "أساسية":  ["start","help","about","live","setportfolio",
                         "trades","premium","quicksignal","upgrade"],
            "التحليل": ["signal","news","regime","backtest","onchain",
                         "liquidity","drift","analyze","chart","events"],
            "التخطيط": ["planweek","planmonth","portfolio"],
            "التداول": ["autotrade","execute","approve","reject","risk"],
            "النظام":  ["stats","killswitch"],
        }
        result = {}
        for cat, cmds in categories.items():
            available = [
                cmd for cmd in cmds
                if TIERS[tier]["level"] >= TIERS[CMD_TIER.get(cmd,"free")]["level"]
            ]
            if available:
                result[cat] = available
        return result

    # ═══════════════════════════════════════════════════════════
    # ENV Helpers
    # ═══════════════════════════════════════════════════════════
    def _get_owner_id(self) -> Optional[int]:
        try:
            val = os.environ.get("OWNER_CHAT_ID", "").strip()
            return int(val) if val and val.isdigit() else None
        except Exception:
            return None

    def _get_admin_ids(self) -> set:
        try:
            val = os.environ.get("ADMIN_USERS", "").strip()
            return {int(x.strip()) for x in val.split(",")
                    if x.strip().isdigit()} if val else set()
        except Exception:
            return set()

    def _get_premium_env_ids(self) -> set:
        try:
            val = os.environ.get("PREMIUM_USERS", "").strip()
            return {int(x.strip()) for x in val.split(",")
                    if x.strip().isdigit()} if val else set()
        except Exception:
            return set()

    def _is_owner(self, user_id: int) -> bool:
        owner = self._get_owner_id()
        return owner is not None and owner == user_id

    def get_tier_name(self, user_id: int) -> str:
        return TIERS[self.get_tier(user_id)]["name"]

    def get_redis_status(self) -> str:
        if self._redis_ok:
            try:
                info  = self._redis.info("memory")
                users = self._redis.scard(_ALL_USERS_KEY)
                mem   = info.get("used_memory_human", "?")
                return f"✅ Redis متصل | {users} مستخدم | ذاكرة: {mem}"
            except Exception:
                return "✅ Redis متصل"
        return "❌ Redis غير متصل — ملف محلي"

    # ═══════════════════════════════════════════════════════════
    # حفظ/جلب Exchange Credentials (مُشفَّرة في Redis)
    # ═══════════════════════════════════════════════════════════
    def _encrypt(self, text: str) -> str:
        """تشفير بسيط بـ base64 + XOR."""
        import base64
        key = (os.environ.get("ENCRYPTION_KEY", "raed_default_key_2024") * 100)[:len(text)]
        xored = bytes(ord(a) ^ ord(b) for a, b in zip(text, key))
        return base64.b64encode(xored).decode()

    def _decrypt(self, text: str) -> str:
        """فك تشفير."""
        import base64
        try:
            xored = base64.b64decode(text.encode())
            key   = (os.environ.get("ENCRYPTION_KEY", "raed_default_key_2024") * 100)[:len(xored)]
            return bytes(b ^ ord(k) for b, k in zip(xored, key)).decode()
        except Exception:
            return ""

    def save_exchange_credentials(self, user_id: int, exchange_name: str,
                                    api_key: str, api_secret: str,
                                    passphrase: str = "", testnet: bool = False):
        """يحفظ بيانات الربط مُشفَّرة في Redis."""
        if not self._redis_ok:
            logger.warning(f"Redis غير متصل — لن تُحفظ بيانات {exchange_name}")
            return
        try:
            data = {
                "exchange_name": exchange_name,
                "api_key":       self._encrypt(api_key),
                "api_secret":    self._encrypt(api_secret),
                "passphrase":    self._encrypt(passphrase) if passphrase else "",
                "testnet":       testnet,
                "saved_at":      time.time(),
            }
            key = f"raed:exchange:{user_id}"
            self._redis.set(key, json.dumps(data))
            logger.info(f"✅ Exchange credentials حُفظت: user={user_id} ex={exchange_name}")
        except Exception as e:
            logger.error(f"save_exchange_credentials: {e}")

    def load_exchange_credentials(self, user_id: int) -> Optional[dict]:
        """يجلب بيانات الربط من Redis ويفك تشفيرها."""
        if not self._redis_ok:
            return None
        try:
            key  = f"raed:exchange:{user_id}"
            data = self._redis.get(key)
            if not data:
                return None
            d = json.loads(data)
            return {
                "exchange_name": d.get("exchange_name", ""),
                "api_key":       self._decrypt(d.get("api_key", "")),
                "api_secret":    self._decrypt(d.get("api_secret", "")),
                "passphrase":    self._decrypt(d.get("passphrase", "")) if d.get("passphrase") else "",
                "testnet":       d.get("testnet", False),
                "saved_at":      d.get("saved_at", 0),
            }
        except Exception as e:
            logger.error(f"load_exchange_credentials: {e}")
            return None

    def delete_exchange_credentials(self, user_id: int):
        """يحذف بيانات الربط من Redis."""
        if not self._redis_ok:
            return
        try:
            self._redis.delete(f"raed:exchange:{user_id}")
            logger.info(f"🔌 Exchange credentials حُذفت: user={user_id}")
        except Exception as e:
            logger.error(f"delete_exchange_credentials: {e}")

    def get_all_exchange_users(self) -> list:
        """يُعيد قائمة المستخدمين الذين لديهم exchange مرتبط."""
        if not self._redis_ok:
            return []
        try:
            keys = self._redis.keys("raed:exchange:*")
            return [int(k.split(":")[-1]) for k in keys
                    if k.split(":")[-1].isdigit()]
        except Exception as e:
            logger.error(f"get_all_exchange_users: {e}")
            return []


# Singleton
state_manager = StateManager()

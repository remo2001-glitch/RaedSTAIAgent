"""
💾 رائد — State Manager
الحل الجذري للاستمرارية عبر Railway deploys:

طبقات الحفظ (بالأولوية):
١. ENV: OWNER_CHAT_ID, PREMIUM_USERS, ADMIN_USERS → دائمة
٢. ENV: RAED_STATE → JSON مضغوط، يُحدَّث تلقائياً
٣. bot_state.json → محلي (يُفقد عند deploy)
"""

import json
import logging
import os
import time
import base64
import zlib
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
    "gold":    ["okx","binance","bybit","bitget","mexc"],
    "diamond": ["okx","binance","bybit","bitget","mexc"],
    "admin":   ["okx","binance","bybit","bitget","mexc"],
}


class StateManager:

    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._load_all()

    # ═══════════════════════════════════════════════════════════
    # تحميل — ٣ مصادر
    # ═══════════════════════════════════════════════════════════
    def _load_all(self):
        """يحمّل من جميع المصادر بالترتيب."""
        # ١. ملف محلي
        self._load_file()
        # ٢. RAED_STATE ENV (يُغلب على الملف)
        self._load_env_state()
        # ٣. تطبيق OWNER + PREMIUM من ENV
        self._apply_env_vars()
        logger.info(f"StateManager: {len(self._state.get('users',{}))} مستخدم محمّل")

    def _load_file(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
        except Exception as e:
            logger.warning(f"state file load: {e}")
            self._state = {}

    def _load_env_state(self):
        """يحمّل RAED_STATE من ENV إذا موجود."""
        raed_state = os.environ.get("RAED_STATE", "").strip()
        if not raed_state:
            return
        try:
            decoded   = base64.b64decode(raed_state.encode())
            json_str  = zlib.decompress(decoded).decode("utf-8")
            env_data  = json.loads(json_str)
            # دمج مع الملف — ENV يُغلب
            if "users" in env_data:
                existing = self._state.setdefault("users", {})
                for uid, data in env_data["users"].items():
                    existing[uid] = {**existing.get(uid, {}), **data}
            logger.info("✅ RAED_STATE محمّل من ENV")
        except Exception as e:
            logger.warning(f"RAED_STATE load error: {e}")

    def _apply_env_vars(self):
        """يُطبّق OWNER_CHAT_ID و PREMIUM_USERS و ADMIN_USERS."""
        # المالك
        owner = self._get_owner_id()
        if owner:
            ud = self._get_user(owner)
            ud["tier"] = "admin"
            ud["tier_by"] = "env_owner"
            self._state.setdefault("users", {})[str(owner)] = ud

        # ADMIN_USERS
        for uid in self._get_admin_ids():
            ud = self._get_user(uid)
            if ud.get("tier") != "admin":
                ud["tier"] = "admin"
                ud["tier_by"] = "env_admin"
                self._state.setdefault("users", {})[str(uid)] = ud

        # PREMIUM_USERS → silver إذا لم تكن لهم باقة
        for uid in self._get_premium_env_ids():
            ud = self._get_user(uid)
            if not ud.get("tier") or ud.get("tier") == "free":
                ud["tier"] = "silver"
                ud["tier_by"] = "env_premium"
                self._state.setdefault("users", {})[str(uid)] = ud

    # ═══════════════════════════════════════════════════════════
    # حفظ — ملف + ENV
    # ═══════════════════════════════════════════════════════════
    def save(self):
        """يحفظ في الملف المحلي."""
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"state save error: {e}")

    def get_env_state_value(self) -> str:
        """
        يُنتج قيمة RAED_STATE للنسخ في Railway Variables.
        استخدم: /admin state لعرضها
        """
        try:
            # حفظ فقط بيانات المستخدمين المهمة
            users = self._state.get("users", {})
            important = {
                uid: {k: v for k, v in data.items()
                      if k in ("tier","tier_by","futures_enabled",
                                "margin_enabled","portfolio")}
                for uid, data in users.items()
                if data.get("tier") and data.get("tier") != "free"
            }
            payload  = json.dumps({"users": important}, ensure_ascii=False)
            compressed = zlib.compress(payload.encode("utf-8"), level=9)
            b64       = base64.b64encode(compressed).decode()
            return b64
        except Exception as e:
            logger.error(f"get_env_state_value: {e}")
            return ""

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def get_tier(self, user_id: int) -> str:
        """
        يُعيد باقة المستخدم — يقرأ ENV في كل مرة لضمان الاستمرارية.
        """
        uid_str = str(user_id)

        # ١. المالك — ENV مباشرة
        owner = self._get_owner_id()
        if owner and owner == user_id:
            return "admin"

        # ٢. ADMIN_USERS ENV
        if user_id in self._get_admin_ids():
            return "admin"

        # ٣. state (ملف + RAED_STATE)
        ud    = self._get_user(user_id)
        tier  = ud.get("tier", "")
        if tier in TIERS and tier != "free":
            return tier

        # ٤. PREMIUM_USERS ENV
        if user_id in self._get_premium_env_ids():
            return "silver"

        return "free"

    def set_tier(self, user_id: int, tier: str,
                  by: str = "admin", requester_id: int = 0) -> bool:
        """يضبط باقة المستخدم — المدير فقط."""
        if tier not in TIERS:
            return False

        # فحص صلاحية الطالب
        if requester_id != 0:
            req_tier = self.get_tier(requester_id)
            if req_tier != "admin":
                logger.warning(
                    f"SET_TIER BLOCKED: {requester_id}({req_tier})"
                    f" → {user_id}:{tier}")
                return False
            # لا يمكن تغيير المالك
            owner = self._get_owner_id()
            if owner and user_id == owner:
                logger.warning("SET_TIER BLOCKED: لا يمكن تغيير المالك")
                return False

        ud = self._get_user(user_id)
        ud["tier"]      = tier
        ud["tier_by"]   = by
        ud["tier_since"]= time.time()
        self._set_user(user_id, ud)
        logger.info(f"✅ tier: {user_id} → {tier} by {by}")
        return True

    def initialize_owner(self):
        """يُشغَّل عند startup لتطبيق ENV."""
        self._apply_env_vars()
        self.save()
        logger.info("✅ StateManager: initialize_owner done")

    def is_premium(self, user_id: int) -> bool:
        return TIERS[self.get_tier(user_id)]["level"] >= 1

    def set_premium(self, user_id: int, is_premium: bool, by: str = "admin"):
        self.set_tier(user_id, "silver" if is_premium else "free", by=by)

    def can_use_command(self, user_id: int, command: str) -> bool:
        req   = CMD_TIER.get(command, "free")
        tier  = self.get_tier(user_id)
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

    def get_user_portfolio(self, user_id: int, default: float = 10000) -> float:
        owner = self._get_owner_id()
        if owner and user_id == owner:
            return float(os.environ.get("PORTFOLIO_SIZE", default))
        return float(self._get_user(user_id).get("portfolio", default))

    def set_user_portfolio(self, user_id: int, amount: float):
        ud = self._get_user(user_id)
        ud["portfolio"] = amount
        self._set_user(user_id, ud)

    def get_all_user_ids(self) -> list:
        """يُعيد قائمة بجميع user_ids المسجّلين."""
        return [int(uid) for uid in self._state.get("users", {}).keys()
                if str(uid).isdigit()]

    def get_autotrade_users(self) -> list:
        """يُعيد user_ids الذين فعّلوا autotrade."""
        result = []
        for uid_str, data in self._state.get("users", {}).items():
            if isinstance(data, dict) and data.get("autotrade_on"):
                try:
                    result.append(int(uid_str))
                except ValueError:
                    pass
        return result

    def get_free_autotrade_days(self, user_id: int) -> int:
        ud = self._get_user(user_id)
        first = ud.get("first_use", 0)
        if not first:
            ud["first_use"] = time.time()
            self._set_user(user_id, ud)
            first = ud["first_use"]
        elapsed = (time.time() - first) / 86400
        return max(0, 30 - int(elapsed))

    def can_use_autotrade_free(self, user_id: int) -> bool:
        tier = self.get_tier(user_id)
        if tier != "free":
            return True
        return self.get_free_autotrade_days(user_id) > 0

    def is_autotrade_on(self, user_id: int) -> bool:
        """يتحقق إذا كان التداول الآلي مُفعَّلاً للمستخدم."""
        ud = self._get_user(user_id)
        return bool(ud.get("autotrade_on", False))

    def set_autotrade_on(self, user_id: int, enabled: bool):
        """يُفعِّل أو يُوقف التداول الآلي للمستخدم."""
        ud = self._get_user(user_id)
        ud["autotrade_on"] = enabled
        self._set_user(user_id, ud)
        self.save()

    def list_premium_users(self) -> List[dict]:
        result = []
        for uid_str, data in self._state.get("users", {}).items():
            tier = data.get("tier", "free")
            if tier != "free":
                result.append({"user_id": int(uid_str), "tier": tier})
        return result

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
        ]
        return "\n".join(lines)

    def get_commands_for_user(self, user_id: int) -> dict:
        tier  = self.get_tier(user_id)
        level = TIERS[tier]["level"]
        categories = {
            "أساسية": ["start","help","about","live","setportfolio",
                        "trades","premium","quicksignal","upgrade"],
            "التحليل": ["signal","news","regime","backtest","onchain",
                         "liquidity","drift","analyze","chart","events"],
            "التخطيط": ["planweek","planmonth","portfolio"],
            "التداول": ["autotrade","execute","approve","reject","risk"],
            "النظام":  ["stats","killswitch"],
        }
        result = {}
        for cat, cmds in categories.items():
            available = []
            for cmd in cmds:
                req = CMD_TIER.get(cmd, "free")
                if TIERS[tier]["level"] >= TIERS[req]["level"]:
                    available.append(cmd)
            if available:
                result[cat] = available
        return result

    # ═══════════════════════════════════════════════════════════
    # Helpers
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

    def _get_user(self, user_id: int) -> dict:
        return dict(self._state.setdefault("users", {}).get(str(user_id), {}))

    def _set_user(self, user_id: int, data: dict):
        self._state.setdefault("users", {})[str(user_id)] = data
        self.save()


# Singleton
state_manager = StateManager()

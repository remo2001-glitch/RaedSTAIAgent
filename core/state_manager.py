"""
💾 رائد — State Manager
يحفظ حالة المستخدمين بشكل دائم عبر Railway deploys.

الباقات الأربع:
- free    (🆓 مجاني):  15 عملة — أوامر أساسية
- silver  (🥈 فضي):   35 عملة — signal/news/regime/backtest/portfolio
- gold    (🥇 ذهبي):  100 عملة — analyze/liquidity/onchain/plans
- diamond (💎 ماسي):  300 عملة — chart/quant/multi-timeframe

الاستمرارية:
١. OWNER_CHAT_ID → دائماً diamond + admin
٢. PREMIUM_USERS → silver افتراضياً من ENV
٣. bot_state.json → حفظ محلي
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'bot_state.json')

# ── تعريف الباقات ─────────────────────────────────────────────
TIERS = {
    "free":    {"name": "🆓 مجاني",  "coins": 15,  "level": 0},
    "silver":  {"name": "🥈 فضي",    "coins": 35,  "level": 1},
    "gold":    {"name": "🥇 ذهبي",   "coins": 100, "level": 2},
    "diamond": {"name": "💎 ماسي",   "coins": 300, "level": 3},
    "admin":   {"name": "👑 مدير",   "coins": 300, "level": 99},
}

# ── الأوامر وحدها الدنيا من الباقة ────────────────────────────
CMD_TIER = {
    # أوامر مجانية للجميع
    "start":        "free",
    "help":         "free",
    "live":         "free",
    "setportfolio": "free",
    "trades":       "free",
    "premium":      "free",
    "about":        "free",
    "upgrade":      "free",    # جدول الباقات والأسعار
    "quicksignal":  "free",    # تحليل أولي مختصر
    "portfolio":    "free",    # استعراض المحفظة للجميع

    # فضي وأعلى
    "signal":    "silver",
    "news":      "silver",
    "regime":    "silver",
    "backtest":  "silver",
    "portfolio": "free",    # استعراض المحفظة للجميع
    "stats":     "silver",
    "events":    "silver",
    "autotrade": "silver",
    "execute":   "silver",
    "risk":      "silver",
    "approve":   "silver",
    "reject":    "silver",

    # ذهبي وأعلى
    "analyze":   "gold",
    "liquidity": "gold",
    "onchain":   "gold",
    "planweek":  "gold",
    "planmonth": "gold",
    "drift":     "gold",

    # ماسي فقط
    "chart":     "diamond",

    # مدير فقط
    "killswitch": "admin",
}

# ── المنصات المتاحة لكل باقة ──────────────────────────────────
TIER_EXCHANGES = {
    "free":    ["okx"],
    "silver":  ["okx"],
    "gold":    ["okx", "binance", "bybit", "bitget", "mexc"],
    "diamond": ["okx", "binance", "bybit", "bitget", "mexc"],
    "admin":   ["okx", "binance", "bybit", "bitget", "mexc"],
}


class StateManager:
    """يُدير الحالة الدائمة لرائد."""

    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._load()

    # ═══════════════════════════════════════════════════════════
    # تحميل وحفظ
    # ═══════════════════════════════════════════════════════════
    def _load(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                logger.info(f"StateManager: {len(self._state.get('users',{}))} مستخدم")
        except Exception as e:
            logger.error(f"StateManager load: {e}")
            self._state = {}

    def save(self):
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"StateManager save: {e}")

    def get(self, key: str, default=None):
        return self._state.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True):
        self._state[key] = value
        if auto_save:
            self.save()

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def get_tier(self, user_id: int) -> str:
        """
        يُعيد باقة المستخدم بترتيب الأولوية:
        ١. OWNER_CHAT_ID → admin
        ٢. ADMIN_USERS   → admin
        ٣. bot_state.json → الباقة المسجّلة
        ٤. PREMIUM_USERS → silver (ENV)
        ٥. افتراضي → free
        """
        # ١. المدير
        if self._is_owner(user_id) or self._is_admin(user_id):
            return "admin"

        # ٢. state.json
        user_data = self._get_user(user_id)
        stored    = user_data.get("tier", "")
        if stored in TIERS:
            return stored

        # ٣. PREMIUM_USERS ENV → silver
        if self._in_premium_env(user_id):
            return "silver"

        return "free"

    def set_tier(self, user_id: int, tier: str, by: str = "admin"):
        """يضبط باقة المستخدم."""
        if tier not in TIERS:
            raise ValueError(f"باقة غير صالحة: {tier}")
        user_data          = self._get_user(user_id)
        user_data["tier"]  = tier
        user_data["tier_set_by"] = by
        user_data["tier_since"]  = time.time()
        self._set_user(user_id, user_data)
        logger.info(f"Tier: user {user_id} → {tier} by {by}")

    def is_premium(self, user_id: int) -> bool:
        """هل المستخدم مدفوع (أي باقة أعلى من مجاني)؟"""
        return TIERS[self.get_tier(user_id)]["level"] >= 1

    def set_premium(self, user_id: int, is_premium: bool, by: str = "admin"):
        """للتوافق مع الكود القديم — يضبط silver أو free."""
        self.set_tier(user_id, "silver" if is_premium else "free", by=by)

    def can_use_command(self, user_id: int, command: str) -> bool:
        """هل المستخدم مسموح له باستخدام هذا الأمر؟"""
        required = CMD_TIER.get(command, "free")
        user_tier = self.get_tier(user_id)
        return TIERS[user_tier]["level"] >= TIERS[required]["level"]

    def get_blocked_reason(self, user_id: int, command: str) -> str:
        """يُعيد سبب الحجب مع اسم الباقة المطلوبة."""
        required  = CMD_TIER.get(command, "free")
        tier_name = TIERS[required]["name"]
        tier_info = TIERS[required]
        return (
            f"🔒 هذا الأمر يتطلب *{tier_name}*\n\n"
            f"للترقية: /premium"
        )

    def coin_limit(self, user_id: int) -> int:
        return TIERS[self.get_tier(user_id)]["coins"]

    def allowed_exchanges(self, user_id: int) -> List[str]:
        tier = self.get_tier(user_id)
        return TIER_EXCHANGES.get(tier, ["okx"])

    def can_use_exchange(self, user_id: int, exchange: str) -> bool:
        return exchange.lower() in self.allowed_exchanges(user_id)

    def get_free_autotrade_days(self, user_id: int) -> int:
        """
        المجاني يحصل على 30 يوم autotrade مجاناً.
        يُحسب من تاريخ أول استخدام.
        """
        user_data   = self._get_user(user_id)
        first_use   = user_data.get("first_use", 0)
        if not first_use:
            # أول استخدام — سجّل التاريخ
            user_data["first_use"] = time.time()
            self._set_user(user_id, user_data)
            first_use = user_data["first_use"]

        days_elapsed = (time.time() - first_use) / 86400
        remaining    = max(0, 30 - int(days_elapsed))
        return remaining

    def can_use_autotrade_free(self, user_id: int) -> bool:
        """هل المستخدم المجاني لا يزال ضمن فترة 30 يوم؟"""
        tier = self.get_tier(user_id)
        if tier != "free":
            return True   # المدفوع لا قيد عليه
        return self.get_free_autotrade_days(user_id) > 0

    def get_futures_enabled(self, user_id: int) -> bool:
        tier = self.get_tier(user_id)
        if TIERS[tier]["level"] < 2:  # يحتاج ذهبي+
            return False
        return self._get_user(user_id).get("futures_enabled", False)

    def set_futures_enabled(self, user_id: int, enabled: bool):
        tier = self.get_tier(user_id)
        if TIERS[tier]["level"] < 2:
            return
        user_data = self._get_user(user_id)
        user_data["futures_enabled"] = enabled
        self._set_user(user_id, user_data)

    def get_margin_enabled(self, user_id: int) -> bool:
        tier = self.get_tier(user_id)
        if TIERS[tier]["level"] < 2:
            return False
        return self._get_user(user_id).get("margin_enabled", False)

    def set_margin_enabled(self, user_id: int, enabled: bool):
        tier = self.get_tier(user_id)
        if TIERS[tier]["level"] < 2:
            return
        user_data = self._get_user(user_id)
        user_data["margin_enabled"] = enabled
        self._set_user(user_id, user_data)

    def get_user_portfolio(self, user_id: int, default: float = 10000) -> float:
        if self._is_owner(user_id):
            return float(os.environ.get("PORTFOLIO_SIZE", default))
        return float(self._get_user(user_id).get("portfolio", default))

    def set_user_portfolio(self, user_id: int, amount: float):
        user_data = self._get_user(user_id)
        user_data["portfolio"] = amount
        self._set_user(user_id, user_data)

    def list_premium_users(self) -> List[dict]:
        """قائمة جميع المستخدمين غير المجانيين."""
        result = []
        users  = self._state.get("users", {})
        for uid_str, data in users.items():
            tier = data.get("tier", "free")
            if tier != "free":
                result.append({"user_id": int(uid_str), "tier": tier})
        # من ENV
        env_val = os.environ.get("PREMIUM_USERS", "")
        env_ids = {int(x.strip()) for x in env_val.split(",")
                   if x.strip().isdigit()}
        for uid in env_ids:
            if not any(r["user_id"] == uid for r in result):
                result.append({"user_id": uid, "tier": "silver"})
        return result

    def format_profile_ar(self, user_id: int) -> str:
        tier      = self.get_tier(user_id)
        tier_info = TIERS[tier]
        futures   = self.get_futures_enabled(user_id)
        margin    = self.get_margin_enabled(user_id)
        portfolio = self.get_user_portfolio(user_id)
        exchanges = self.allowed_exchanges(user_id)

        lines = [
            f"👤 *ملف المستخدم*",
            f"• المعرّف: `{user_id}`",
            f"• الباقة: {tier_info['name']}",
            f"• حجم المحفظة: ${portfolio:,.0f}",
            f"• حد العملات: {tier_info['coins']} عملة",
            f"• المنصات: {', '.join(exchanges).upper()}",
            f"• Futures: {'✅' if futures else '❌'}",
            f"• Margin: {'✅' if margin else '❌'}",
        ]
        return "\n".join(lines)

    def get_commands_for_user(self, user_id: int) -> dict:
        """يُعيد الأوامر المتاحة للمستخدم مصنّفة."""
        tier  = self.get_tier(user_id)
        level = TIERS[tier]["level"]

        cmds = {
            "أساسية": [],
            "التحليل": [],
            "التخطيط": [],
            "التداول": [],
            "النظام":  [],
        }

        categories = {
            "أساسية": ["start","help","about","live","setportfolio","trades","premium"],
            "التحليل": ["signal","news","regime","backtest","onchain","liquidity","drift","analyze","chart","events"],
            "التخطيط": ["planweek","planmonth","portfolio"],
            "التداول": ["autotrade","execute","approve","reject","risk"],
            "النظام":  ["stats","killswitch"],
        }

        for cat, cat_cmds in categories.items():
            for cmd in cat_cmds:
                req = CMD_TIER.get(cmd, "free")
                if TIERS[tier]["level"] >= TIERS[req]["level"]:
                    cmds[cat].append(cmd)

        return cmds

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════
    def _is_owner(self, user_id: int) -> bool:
        try:
            val = os.environ.get("OWNER_CHAT_ID", "")
            return bool(val) and int(val) == user_id
        except (ValueError, TypeError):
            return False

    def _is_admin(self, user_id: int) -> bool:
        try:
            val = os.environ.get("ADMIN_USERS", "")
            admins = {int(x.strip()) for x in val.split(",") if x.strip().isdigit()}
            return user_id in admins
        except Exception:
            return False

    def _in_premium_env(self, user_id: int) -> bool:
        try:
            val = os.environ.get("PREMIUM_USERS", "")
            ids = {int(x.strip()) for x in val.split(",") if x.strip().isdigit()}
            return user_id in ids
        except Exception:
            return False

    def _get_user(self, user_id: int) -> dict:
        return self._state.setdefault("users", {}).get(str(user_id), {})

    def _set_user(self, user_id: int, data: dict):
        self._state.setdefault("users", {})[str(user_id)] = data
        self.save()

    def _get_owner_id(self) -> Optional[int]:
        try:
            val = os.environ.get("OWNER_CHAT_ID", "")
            return int(val) if val and val.isdigit() else None
        except (ValueError, TypeError):
            return None


# Singleton
state_manager = StateManager()

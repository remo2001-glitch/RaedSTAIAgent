"""
👥 رائد — User Manager v2
المصدر الوحيد للحقيقة: state_manager
user_manager = واجهة موحَّدة تُفوَّض بالكامل إلى state_manager

الإصلاح الجذري:
- إزالة التعارض بين ملف JSON و state_manager
- جميع الاستعلامات تقرأ من state_manager مباشرة
- UserProfile يعكس tier الحقيقي (admin/diamond/gold/silver/free)
- is_premium = tier in (silver, gold, diamond, admin)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.state_manager import state_manager as _sm

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'premium_users.json')

# الباقات المدفوعة
PAID_TIERS  = {"silver", "gold", "diamond", "admin"}
# الباقات التي تدعم Futures
FUTURES_TIERS = {"gold", "diamond", "admin"}
# الباقات التي تدعم جميع المنصات
ALL_EXCHANGES_TIERS = {"silver", "gold", "diamond", "admin"}


@dataclass
class UserProfile:
    """
    نسخة موحَّدة من بيانات المستخدم — تعكس state_manager مباشرة.
    """
    user_id:         int
    tier:            str   = "free"
    futures_enabled: bool  = False
    margin_enabled:  bool  = False
    notes:           str   = ""

    @property
    def is_premium(self) -> bool:
        """المدير والمدفوعون = premium."""
        return self.tier in PAID_TIERS

    @property
    def is_admin(self) -> bool:
        return self.tier == "admin"

    @property
    def coin_limit(self) -> int:
        limits = {"free": 15, "silver": 35, "gold": 100,
                  "diamond": 350, "admin": 9999}
        return limits.get(self.tier, 15)

    @property
    def tier_name(self) -> str:
        names = {
            "free":    "🆓 مجاني",
            "silver":  "🥈 فضي",
            "gold":    "🥇 ذهبي",
            "diamond": "💎 ماسي",
            "admin":   "👑 مدير",
        }
        return names.get(self.tier, "🆓 مجاني")

    @property
    def allowed_exchanges(self) -> List[str]:
        if self.tier in ALL_EXCHANGES_TIERS:
            return ["okx", "bybit", "bitget", "mexc", "binance"]
        return ["okx"]

    @property
    def allowed_trade_types(self) -> List[str]:
        types = ["spot"]
        if self.is_premium:
            if self.futures_enabled or self.tier in FUTURES_TIERS:
                types.append("futures")
            if self.margin_enabled:
                types.append("margin")
        return types


class UserManager:
    """
    يُدير المستخدمين والباقات.
    المصدر الوحيد: state_manager (دائم عبر deploys).
    ملف JSON يُستخدم فقط للـ futures/margin flags.
    """

    def __init__(self):
        self._futures_flags: Dict[int, bool] = {}
        self._margin_flags:  Dict[int, bool] = {}
        self._notes:         Dict[int, str]  = {}
        self._load_flags()
        self._load_from_env()

    # ═══════════════════════════════════════════════════════════
    # الاستعلام الأساسي — المصدر الوحيد: state_manager
    # ═══════════════════════════════════════════════════════════
    def get(self, user_id: int) -> UserProfile:
        """يُعيد UserProfile حقيقياً من state_manager."""
        tier = _sm.get_tier(user_id)
        return UserProfile(
            user_id         = user_id,
            tier            = tier,
            futures_enabled = self._futures_flags.get(user_id, False),
            margin_enabled  = self._margin_flags.get(user_id, False),
            notes           = self._notes.get(user_id, ""),
        )

    def get_profile(self, user_id: int) -> UserProfile:
        """مرادف لـ get() — للتوافق."""
        return self.get(user_id)

    # ═══════════════════════════════════════════════════════════
    # استعلامات مُفوَّضة لـ state_manager
    # ═══════════════════════════════════════════════════════════
    def is_premium(self, user_id: int) -> bool:
        return _sm.get_tier(user_id) in PAID_TIERS

    def is_admin(self, user_id: int) -> bool:
        return _sm.get_tier(user_id) == "admin"

    def coin_limit(self, user_id: int) -> int:
        return _sm.coin_limit(user_id)

    def allowed_exchanges(self, user_id: int) -> List[str]:
        return _sm.allowed_exchanges(user_id)

    def can_use_exchange(self, user_id: int, exchange: str) -> bool:
        return exchange.lower() in _sm.allowed_exchanges(user_id)

    def can_use_futures(self, user_id: int) -> bool:
        tier = _sm.get_tier(user_id)
        if tier in FUTURES_TIERS:
            return True
        return self._futures_flags.get(user_id, False) and self.is_premium(user_id)

    def can_use_margin(self, user_id: int) -> bool:
        return self._margin_flags.get(user_id, False) and self.is_premium(user_id)

    def get_free_autotrade_days(self, user_id: int) -> int:
        return _sm.get_free_autotrade_days(user_id)

    def is_autotrade_on(self, user_id: int) -> bool:
        return _sm.is_autotrade_on(user_id)

    def set_autotrade_on(self, user_id: int, enabled: bool):
        _sm.set_autotrade_on(user_id, enabled)

    def format_profile_ar(self, user_id: int) -> str:
        return _sm.format_profile_ar(user_id)

    def can_use_autotrade_free(self, user_id: int) -> bool:
        return _sm.can_use_autotrade_free(user_id)

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def add_premium(self, user_id: int, by: str = "admin",
                     notes: str = "", tier: str = "gold") -> UserProfile:
        """يمنح المستخدم باقة مدفوعة."""
        valid = {"silver", "gold", "diamond"}
        if tier not in valid:
            tier = "gold"
        _sm.set_tier(user_id, tier)
        _sm.set_premium(user_id, True, by=by)
        if notes:
            self._notes[user_id] = notes
        self._save_flags()
        logger.info(f"✅ Premium ({tier}): user {user_id} بواسطة {by}")
        return self.get(user_id)

    def remove_premium(self, user_id: int) -> bool:
        """يُلغي باقة المستخدم → free."""
        _sm.set_tier(user_id, "free")
        _sm.set_premium(user_id, False, by="admin")
        self._futures_flags.pop(user_id, None)
        self._margin_flags.pop(user_id, None)
        self._save_flags()
        logger.info(f"❌ Premium removed: user {user_id}")
        return True

    def set_futures(self, user_id: int, enabled: bool) -> bool:
        """يُفعّل/يُوقف Futures للمستخدم (مدفوع فقط)."""
        if not self.is_premium(user_id):
            return False
        self._futures_flags[user_id] = enabled
        self._save_flags()
        return True

    def set_margin(self, user_id: int, enabled: bool) -> bool:
        """يُفعّل/يُوقف Margin للمستخدم (مدفوع فقط)."""
        if not self.is_premium(user_id):
            return False
        self._margin_flags[user_id] = enabled
        self._save_flags()
        return True

    def list_premium(self) -> List[UserProfile]:
        """يُعيد قائمة المستخدمين المدفوعين."""
        result = []
        for uid_str in _sm._state.get("users", {}).keys():
            try:
                uid = int(uid_str)
                if self.is_premium(uid):
                    result.append(self.get(uid))
            except ValueError:
                pass
        return result

    # ═══════════════════════════════════════════════════════════
    # حفظ وتحميل (flags فقط)
    # ═══════════════════════════════════════════════════════════
    def _save_flags(self):
        """يحفظ flags الـ futures/margin فقط."""
        try:
            data = {}
            for uid in set(list(self._futures_flags) + list(self._margin_flags)):
                data[str(uid)] = {
                    "futures_enabled": self._futures_flags.get(uid, False),
                    "margin_enabled":  self._margin_flags.get(uid, False),
                    "notes":           self._notes.get(uid, ""),
                }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"UserManager save_flags error: {e}")

    def _load_flags(self):
        """يُحمِّل flags الـ futures/margin."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for uid_str, vals in data.items():
                    try:
                        uid = int(uid_str)
                        self._futures_flags[uid] = vals.get("futures_enabled", False)
                        self._margin_flags[uid]  = vals.get("margin_enabled", False)
                        self._notes[uid]         = vals.get("notes", "")
                    except (ValueError, AttributeError):
                        pass
                logger.info(f"UserManager: flags مُحمَّلة لـ {len(data)} مستخدم")
        except Exception as e:
            logger.error(f"UserManager load_flags error: {e}")

    def _load_from_env(self):
        """تحميل المستخدمين المدفوعين من ENV Variable."""
        try:
            env_val = os.environ.get("PREMIUM_USERS", "")
            if env_val:
                count = 0
                for uid_str in env_val.split(","):
                    uid_str = uid_str.strip()
                    if uid_str.isdigit():
                        uid = int(uid_str)
                        if not self.is_premium(uid):
                            self.add_premium(uid, by="env_var")
                            count += 1
                if count:
                    logger.info(f"UserManager: {count} مستخدم مدفوع من ENV")
        except Exception as e:
            logger.error(f"_load_from_env: {e}")


# Singleton
user_manager = UserManager()

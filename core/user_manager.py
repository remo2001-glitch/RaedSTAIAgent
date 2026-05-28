"""
👥 رائد — User Manager
نظام إدارة المستخدمين والباقات
- مجاني: Spot فقط + OKX + 30 عملة
- مدفوع: جميع المنصات + جميع أنواع التداول + 150 عملة
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# state_manager يُوفر الاستمرارية الحقيقية
from core.state_manager import state_manager as _sm

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'premium_users.json')


@dataclass
class UserProfile:
    user_id:        int
    is_premium:     bool    = False
    premium_since:  float   = 0.0
    premium_by:     str     = ""        # admin ID الذي أضافه
    futures_enabled:bool    = False     # تفعيل العقود (مدفوع فقط)
    margin_enabled: bool    = False     # تفعيل الهامش
    notes:          str     = ""

    @property
    def coin_limit(self) -> int:
        return 150 if self.is_premium else 30

    @property
    def allowed_exchanges(self) -> List[str]:
        if self.is_premium:
            return ["okx", "binance", "bybit", "bitget", "mexc"]
        return ["okx"]

    @property
    def allowed_trade_types(self) -> List[str]:
        types = ["spot"]
        if self.is_premium:
            if self.futures_enabled:
                types.append("futures")
            if self.margin_enabled:
                types.append("margin")
        return types


class UserManager:
    """يُدير المستخدمين والباقات — Thread-safe."""

    def __init__(self):
        self._users: Dict[int, UserProfile] = {}
        self._load()
        # تحميل المستخدمين من ENV إذا متاح (يبقى عبر deploys)
        self._load_from_env()

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def add_premium(self, user_id: int, by: str = "admin",
                     notes: str = "") -> UserProfile:
        """يمنح المستخدم باقة مدفوعة — دائمة عبر deploys."""
        if user_id not in self._users:
            self._users[user_id] = UserProfile(user_id=user_id)
        self._users[user_id].is_premium     = True
        self._users[user_id].premium_since  = time.time()
        self._users[user_id].premium_by     = str(by)
        self._users[user_id].notes          = notes
        # حفظ في state_manager أيضاً (يبقى عبر deploys)
        _sm.set_premium(user_id, True, by=by)
        self._save()
        logger.info(f"✅ Premium: user {user_id} بواسطة {by}")
        return self._users[user_id]

    def remove_premium(self, user_id: int) -> bool:
        """يُلغي باقة المستخدم."""
        if user_id in self._users:
            self._users[user_id].is_premium      = False
            self._users[user_id].futures_enabled = False
            self._users[user_id].margin_enabled  = False
            _sm.set_premium(user_id, False, by="admin")
            self._save()
            logger.info(f"❌ Premium removed: user {user_id}")
            return True
        _sm.set_premium(user_id, False, by="admin")
        return False

    def set_futures(self, user_id: int, enabled: bool) -> bool:
        """يُفعّل/يُوقف Futures للمستخدم (مدفوع فقط)."""
        profile = self.get(user_id)
        if not profile.is_premium:
            return False
        profile.futures_enabled = enabled
        self._save()
        return True

    def set_margin(self, user_id: int, enabled: bool) -> bool:
        """يُفعّل/يُوقف Margin للمستخدم (مدفوع فقط)."""
        profile = self.get(user_id)
        if not profile.is_premium:
            return False
        profile.margin_enabled = enabled
        self._save()
        return True

    # ═══════════════════════════════════════════════════════════
    # استعلامات
    # ═══════════════════════════════════════════════════════════
    def get(self, user_id: int) -> UserProfile:
        if user_id not in self._users:
            self._users[user_id] = UserProfile(user_id=user_id)
        return self._users[user_id]

    def is_premium(self, user_id: int) -> bool:
        """يتحقق من الباقة عبر state_manager (دائم عبر deploys)."""
        return _sm.is_premium(user_id)

    def coin_limit(self, user_id: int) -> int:
        return _sm.coin_limit(user_id)

    def can_use_exchange(self, user_id: int, exchange: str) -> bool:
        return exchange.lower() in _sm.allowed_exchanges(user_id)

    def can_use_futures(self, user_id: int) -> bool:
        p = self.get(user_id)
        return p.is_premium and p.futures_enabled

    def can_use_margin(self, user_id: int) -> bool:
        p = self.get(user_id)
        return p.is_premium and p.margin_enabled

    def list_premium(self) -> List[UserProfile]:
        return [u for u in self._users.values() if u.is_premium]

    def format_profile_ar(self, user_id: int) -> str:
        return _sm.format_profile_ar(user_id)

    # ═══════════════════════════════════════════════════════════
    # حفظ وتحميل
    # ═══════════════════════════════════════════════════════════
    def get_free_autotrade_days(self, user_id: int) -> int:
        """يُعيد عدد أيام التداول الآلي المجاني المتبقية."""
        return _sm.get_free_autotrade_days(user_id)

    def is_autotrade_on(self, user_id: int) -> bool:
        """يتحقق من حالة التداول الآلي."""
        return _sm.is_autotrade_on(user_id)

    def get_profile(self, user_id: int) -> "UserProfile":
        """مرادف لـ get() — للتوافق."""
        return self.get(user_id)

    def allowed_exchanges(self, user_id: int) -> List[str]:
        """يُعيد المنصات المتاحة للمستخدم."""
        return _sm.allowed_exchanges(user_id)

    def _save(self):
        try:
            data = {
                str(uid): {
                    "is_premium":      u.is_premium,
                    "premium_since":   u.premium_since,
                    "premium_by":      u.premium_by,
                    "futures_enabled": u.futures_enabled,
                    "margin_enabled":  u.margin_enabled,
                    "notes":           u.notes,
                }
                for uid, u in self._users.items()
            }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"UserManager save error: {e}")

    def _load_from_env(self):
        """
        تحميل المستخدمين المدفوعين من ENV Variable.
        PREMIUM_USERS=id1,id2,id3
        يبقى عبر Railway deploys.
        """
        try:
            env_val = os.environ.get("PREMIUM_USERS", "")
            if env_val:
                for uid_str in env_val.split(","):
                    uid_str = uid_str.strip()
                    if uid_str.isdigit():
                        uid = int(uid_str)
                        if not self._users.get(uid) or not self._users[uid].is_premium:
                            self.add_premium(uid, by="env_var")
                logger.info(f"UserManager: {len([u for u in self._users.values() if u.is_premium])} مدفوع من ENV")
        except Exception as e:
            logger.error(f"_load_from_env: {e}")

    def _load(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for uid_str, vals in data.items():
                    uid = int(uid_str)
                    self._users[uid] = UserProfile(
                        user_id=uid,
                        is_premium     =vals.get("is_premium", False),
                        premium_since  =vals.get("premium_since", 0.0),
                        premium_by     =vals.get("premium_by", ""),
                        futures_enabled=vals.get("futures_enabled", False),
                        margin_enabled =vals.get("margin_enabled", False),
                        notes          =vals.get("notes", ""),
                    )
                logger.info(f"UserManager: تُحمِّل {len(self._users)} مستخدم")
        except Exception as e:
            logger.error(f"UserManager load error: {e}")


# Singleton
user_manager = UserManager()

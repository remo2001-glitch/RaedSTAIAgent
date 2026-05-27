"""
💾 رائد — State Manager
يحفظ حالة المستخدمين بشكل دائم عبر Railway deploys.

الاستراتيجية:
١. OWNER_CHAT_ID → دائماً Premium (من ENV)
٢. PREMIUM_USERS → قائمة IDs من ENV (يدوي)
٣. state.json → حفظ محلي (يُفقد عند deploy)
٤. Bot Telegram → يُرسل state للمالك عند كل تغيير

الأولوية: ENV Variables > state.json > default
"""

import json
import logging
import os
import time
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'bot_state.json')


class StateManager:
    """يُدير الحالة الدائمة لرائد."""

    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._load()

    # ═══════════════════════════════════════════════════════════
    # تحميل وحفظ
    # ═══════════════════════════════════════════════════════════
    def _load(self):
        """تحميل الحالة من الملف المحلي."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                logger.info(f"StateManager: تُحمِّل {len(self._state)} مفتاح")
        except Exception as e:
            logger.error(f"StateManager load: {e}")
            self._state = {}

    def save(self):
        """حفظ الحالة في الملف المحلي."""
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

    def delete(self, key: str):
        self._state.pop(key, None)
        self.save()

    # ═══════════════════════════════════════════════════════════
    # إدارة المستخدمين
    # ═══════════════════════════════════════════════════════════
    def get_user(self, user_id: int) -> Dict:
        """يُعيد بيانات المستخدم."""
        users = self._state.get("users", {})
        return users.get(str(user_id), {})

    def set_user(self, user_id: int, data: Dict):
        """يحفظ بيانات المستخدم."""
        if "users" not in self._state:
            self._state["users"] = {}
        self._state["users"][str(user_id)] = data
        self.save()

    def is_premium(self, user_id: int) -> bool:
        """
        يتحقق من الباقة المدفوعة بترتيب الأولوية:
        ١. OWNER_CHAT_ID → دائماً premium
        ٢. PREMIUM_USERS ENV → premium
        ٣. state.json → premium إذا مسجّل
        """
        # ١. المالك دائماً premium
        owner_id = self._get_owner_id()
        if owner_id and user_id == owner_id:
            return True

        # ٢. PREMIUM_USERS من ENV
        premium_env = os.environ.get("PREMIUM_USERS", "")
        if premium_env:
            env_ids = {int(x.strip()) for x in premium_env.split(",")
                       if x.strip().isdigit()}
            if user_id in env_ids:
                return True

        # ٣. state.json
        user_data = self.get_user(user_id)
        return user_data.get("is_premium", False)

    def set_premium(self, user_id: int, is_premium: bool, by: str = "admin"):
        """يضبط حالة الباقة في state.json."""
        user_data = self.get_user(user_id)
        user_data["is_premium"]    = is_premium
        user_data["premium_since"] = time.time() if is_premium else 0
        user_data["premium_by"]    = by
        self.set_user(user_id, user_data)
        logger.info(f"Premium: user {user_id} = {is_premium} by {by}")

    def get_user_portfolio(self, user_id: int, default: float = 10000) -> float:
        """يُعيد حجم محفظة المستخدم."""
        owner_id = self._get_owner_id()
        if owner_id and user_id == owner_id:
            # المالك يستخدم PORTFOLIO_SIZE
            return float(os.environ.get("PORTFOLIO_SIZE", default))
        user_data = self.get_user(user_id)
        return float(user_data.get("portfolio", default))

    def set_user_portfolio(self, user_id: int, amount: float):
        """يحفظ حجم محفظة المستخدم."""
        user_data = self.get_user(user_id)
        user_data["portfolio"] = amount
        self.set_user(user_id, user_data)

    def get_futures_enabled(self, user_id: int) -> bool:
        """هل Futures مُفعَّل للمستخدم؟"""
        if not self.is_premium(user_id):
            return False
        user_data = self.get_user(user_id)
        return user_data.get("futures_enabled", False)

    def set_futures_enabled(self, user_id: int, enabled: bool):
        user_data = self.get_user(user_id)
        user_data["futures_enabled"] = enabled
        self.set_user(user_id, user_data)

    def get_margin_enabled(self, user_id: int) -> bool:
        if not self.is_premium(user_id):
            return False
        user_data = self.get_user(user_id)
        return user_data.get("margin_enabled", False)

    def set_margin_enabled(self, user_id: int, enabled: bool):
        user_data = self.get_user(user_id)
        user_data["margin_enabled"] = enabled
        self.set_user(user_id, user_data)

    def coin_limit(self, user_id: int) -> int:
        return 150 if self.is_premium(user_id) else 30

    def allowed_exchanges(self, user_id: int):
        if self.is_premium(user_id):
            return ["okx", "binance", "bybit", "bitget", "mexc"]
        return ["okx"]

    def list_premium_users(self):
        """قائمة جميع المستخدمين المدفوعين."""
        users = self._state.get("users", {})
        premium = []

        # من state.json
        for uid_str, data in users.items():
            if data.get("is_premium"):
                premium.append(int(uid_str))

        # من ENV
        env_val = os.environ.get("PREMIUM_USERS", "")
        for uid_str in env_val.split(","):
            uid_str = uid_str.strip()
            if uid_str.isdigit():
                uid = int(uid_str)
                if uid not in premium:
                    premium.append(uid)

        # المالك
        owner = self._get_owner_id()
        if owner and owner not in premium:
            premium.append(owner)

        return premium

    def format_profile_ar(self, user_id: int) -> str:
        prem    = self.is_premium(user_id)
        futures = self.get_futures_enabled(user_id)
        margin  = self.get_margin_enabled(user_id)
        port    = self.get_user_portfolio(user_id)
        owner   = self._get_owner_id()
        is_owner = owner and user_id == owner

        lines = [
            f"👤 *ملف المستخدم*",
            f"• المعرّف: {user_id}",
            f"• الباقة: {'👑 مالك' if is_owner else '💎 مدفوع' if prem else '🆓 مجاني'}",
            f"• حجم المحفظة: ${port:,.0f}",
            f"• حد العملات: {self.coin_limit(user_id)}",
            f"• المنصات: {', '.join(self.allowed_exchanges(user_id)).upper()}",
            f"• Futures: {'✅' if futures else '❌'}",
            f"• Margin: {'✅' if margin else '❌'}",
        ]
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # Helper
    # ═══════════════════════════════════════════════════════════
    def _get_owner_id(self) -> Optional[int]:
        try:
            val = os.environ.get("OWNER_CHAT_ID", "")
            return int(val) if val and val.isdigit() else None
        except (ValueError, TypeError):
            return None


# Singleton
state_manager = StateManager()

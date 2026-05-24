"""
🤖 رائد التداول الذكي — قاعدة البيانات
نسخة مؤقتة بدون Redis للاختبار
"""

import json
from datetime import datetime, timezone
from loguru import logger
from core.config import VIRTUAL_WALLET_START, PLANS

# تخزين مؤقت في الذاكرة
_store = {}

class Database:
    async def connect(self):
        logger.info("✅ قاعدة بيانات مؤقتة جاهزة")

    async def disconnect(self):
        pass

    async def get_user(self, user_id: int):
        return _store.get(f"user:{user_id}")

    async def save_user(self, user: dict):
        _store[f"user:{user['id']}"] = user

    async def create_user(self, user_id, username, full_name):
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": user_id, "username": username or "",
            "full_name": full_name, "plan": "free",
            "joined_at": now, "last_seen": now,
            "virtual_wallet": {
                "balance": VIRTUAL_WALLET_START,
                "invested": 0.0, "profit": 0.0,
                "positions": {}, "history": [],
            },
            "alerts": [], "memory": {"last_commands": [], "favorite_coins": []},
            "stats": {"commands_count": 0},
        }
        await self.save_user(user)
        return user

    async def get_or_create_user(self, user_id, username, full_name):
        user = await self.get_user(user_id)
        if not user:
            user = await self.create_user(user_id, username, full_name)
        return user

    async def update_user_field(self, user_id, field, value):
        user = await self.get_user(user_id)
        if user:
            user[field] = value
            await self.save_user(user)

    async def get_all_users(self):
        return [v for k, v in _store.items() if k.startswith("user:")]

    async def count_users(self):
        users = await self.get_all_users()
        counts = {"total": len(users), "free": 0, "silver": 0, "gold": 0, "diamond": 0}
        for u in users:
            counts[u.get("plan", "free")] = counts.get(u.get("plan", "free"), 0) + 1
        return counts

    async def add_to_memory(self, user_id, command):
        user = await self.get_user(user_id)
        if user:
            cmds = user["memory"].get("last_commands", [])
            cmds.append({"cmd": command})
            user["memory"]["last_commands"] = cmds[-50:]
            await self.save_user(user)

    async def remember_favorite_coin(self, user_id, coin):
        user = await self.get_user(user_id)
        if user:
            favs = user["memory"].get("favorite_coins", [])
            if coin not in favs:
                favs.append(coin)
            user["memory"]["favorite_coins"] = favs[-10:]
            await self.save_user(user)

    async def get_virtual_wallet(self, user_id):
        user = await self.get_user(user_id)
        return user.get("virtual_wallet", {}) if user else {}

    async def update_virtual_wallet(self, user_id, wallet):
        user = await self.get_user(user_id)
        if user:
            user["virtual_wallet"] = wallet
            await self.save_user(user)

    async def add_alert(self, user_id, alert):
        user = await self.get_user(user_id)
        if not user:
            return False
        alerts = user.get("alerts", [])
        alerts.append(alert)
        user["alerts"] = alerts
        await self.save_user(user)
        return True

    async def get_active_alerts(self, user_id):
        user = await self.get_user(user_id)
        return user.get("alerts", []) if user else []

    async def remove_alert(self, user_id, alert_id):
        pass

    async def is_banned(self, user_id):
        return _store.get(f"ban:{user_id}", False)

    async def ban_user(self, user_id, hours=1):
        _store[f"ban:{user_id}"] = True

    async def get_fail_count(self, user_id):
        return _store.get(f"fails:{user_id}", 0)

    async def increment_fails(self, user_id):
        count = _store.get(f"fails:{user_id}", 0) + 1
        _store[f"fails:{user_id}"] = count
        return count

    async def reset_fails(self, user_id):
        _store.pop(f"fails:{user_id}", None)

    async def check_rate_limit(self, user_id, limit=30):
        return True

    async def log_blocked_pattern(self, pattern):
        pass

    async def get_stats(self):
        counts = await self.count_users()
        return {"users": counts, "blocked_patterns": 0, "redis_ping": True}

db = Database()

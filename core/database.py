"""
🤖 رائد التداول الذكي — قاعدة البيانات
إدارة المستخدمين والذاكرة الذكية عبر Redis
"""

import json
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
from datetime import datetime, timezone
from loguru import logger
from core.config import REDIS_URL, VIRTUAL_WALLET_START, PLANS


class Database:
    """إدارة جميع بيانات رائد في Redis"""

    def __init__(self):
        self.redis: aioredis.Redis = None

    async def connect(self):
        self.redis = await aioredis.from_url(
            REDIS_URL, encoding="utf-8", decode_responses=True
        )
        logger.info("✅ اتصال Redis ناجح")

    async def disconnect(self):
        if self.redis:
            await self.redis.aclose()

    # ── المستخدمون ─────────────────────────────────────────────────────────

    async def get_user(self, user_id: int) -> dict | None:
        data = await self.redis.get(f"user:{user_id}")
        return json.loads(data) if data else None

    async def save_user(self, user: dict):
        await self.redis.set(f"user:{user['id']}", json.dumps(user, ensure_ascii=False))

    async def create_user(self, user_id: int, username: str, full_name: str) -> dict:
        """إنشاء مستخدم جديد مع محفظة افتراضية"""
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id":            user_id,
            "username":      username or "",
            "full_name":     full_name,
            "plan":          "free",
            "plan_expires":  None,
            "joined_at":     now,
            "last_seen":     now,
            "language":      "ar",
            "exchange":      None,          # البورصة المفضلة
            "risk_level":    "medium",      # low | medium | high
            "notifications": True,
            "auto_trading":  False,
            "total_trades":  0,
            "virtual_wallet": {
                "balance":   VIRTUAL_WALLET_START,
                "invested":  0.0,
                "profit":    0.0,
                "positions": {},
                "history":   [],
            },
            "real_exchanges":  {},          # مشفّرة
            "alerts":          [],
            "memory": {                     # الذاكرة الذكية
                "favorite_coins": [],
                "last_commands":  [],
                "trade_patterns": {},
            },
            "stats": {
                "commands_count":   0,
                "virtual_trades":   0,
                "real_trades":      0,
                "win_rate":         0.0,
                "best_trade":       0.0,
                "worst_trade":      0.0,
            }
        }
        await self.save_user(user)
        logger.info(f"👤 مستخدم جديد: {full_name} ({user_id})")
        return user

    async def get_or_create_user(self, user_id: int, username: str, full_name: str) -> dict:
        user = await self.get_user(user_id)
        if not user:
            user = await self.create_user(user_id, username, full_name)
        else:
            # تحديث آخر ظهور
            user["last_seen"] = datetime.now(timezone.utc).isoformat()
            await self.save_user(user)
        return user

    async def update_user_field(self, user_id: int, field: str, value):
        user = await self.get_user(user_id)
        if user:
            user[field] = value
            await self.save_user(user)

    async def get_all_users(self) -> list[dict]:
        keys = await self.redis.keys("user:*")
        users = []
        for key in keys:
            data = await self.redis.get(key)
            if data:
                users.append(json.loads(data))
        return users

    async def count_users(self) -> dict:
        users = await self.get_all_users()
        counts = {"total": len(users), "free": 0, "silver": 0, "gold": 0, "diamond": 0}
        for u in users:
            plan = u.get("plan", "free")
            counts[plan] = counts.get(plan, 0) + 1
        return counts

    # ── الذاكرة الذكية ──────────────────────────────────────────────────────

    async def add_to_memory(self, user_id: int, command: str):
        """إضافة الأمر لذاكرة المستخدم (آخر 50 أمر)"""
        user = await self.get_user(user_id)
        if not user:
            return
        memory = user.get("memory", {})
        last_commands = memory.get("last_commands", [])
        last_commands.append({"cmd": command, "time": datetime.now(timezone.utc).isoformat()})
        if len(last_commands) > 50:
            last_commands = last_commands[-50:]
        memory["last_commands"] = last_commands
        user["memory"] = memory
        user["stats"]["commands_count"] = user["stats"].get("commands_count", 0) + 1
        await self.save_user(user)

    async def remember_favorite_coin(self, user_id: int, coin: str):
        user = await self.get_user(user_id)
        if not user:
            return
        favs = user["memory"].get("favorite_coins", [])
        if coin.upper() not in favs:
            favs.append(coin.upper())
        if len(favs) > 10:
            favs = favs[-10:]
        user["memory"]["favorite_coins"] = favs
        await self.save_user(user)

    # ── المحفظة الافتراضية ──────────────────────────────────────────────────

    async def get_virtual_wallet(self, user_id: int) -> dict:
        user = await self.get_user(user_id)
        if not user:
            return {}
        return user.get("virtual_wallet", {})

    async def update_virtual_wallet(self, user_id: int, wallet: dict):
        user = await self.get_user(user_id)
        if user:
            user["virtual_wallet"] = wallet
            await self.save_user(user)

    # ── التنبيهات ───────────────────────────────────────────────────────────

    async def add_alert(self, user_id: int, alert: dict) -> bool:
        user = await self.get_user(user_id)
        if not user:
            return False
        plan_limits = PLANS[user.get("plan", "free")]
        current = user.get("alerts", [])
        if len(current) >= plan_limits["alerts"]:
            return False
        alert["id"] = f"alert_{user_id}_{len(current)}"
        alert["created_at"] = datetime.now(timezone.utc).isoformat()
        current.append(alert)
        user["alerts"] = current
        await self.save_user(user)
        return True

    async def get_active_alerts(self, user_id: int) -> list:
        user = await self.get_user(user_id)
        if not user:
            return []
        return [a for a in user.get("alerts", []) if a.get("active", True)]

    async def remove_alert(self, user_id: int, alert_id: str):
        user = await self.get_user(user_id)
        if user:
            user["alerts"] = [a for a in user.get("alerts", []) if a.get("id") != alert_id]
            await self.save_user(user)

    # ── الأمان والحماية ──────────────────────────────────────────────────────

    async def is_banned(self, user_id: int) -> bool:
        return await self.redis.exists(f"ban:{user_id}") > 0

    async def ban_user(self, user_id: int, hours: int = 1):
        await self.redis.setex(f"ban:{user_id}", hours * 3600, "1")

    async def get_fail_count(self, user_id: int) -> int:
        val = await self.redis.get(f"fails:{user_id}")
        return int(val) if val else 0

    async def increment_fails(self, user_id: int) -> int:
        count = await self.redis.incr(f"fails:{user_id}")
        await self.redis.expire(f"fails:{user_id}", 3600)
        return count

    async def reset_fails(self, user_id: int):
        await self.redis.delete(f"fails:{user_id}")

    async def check_rate_limit(self, user_id: int, limit: int = 30) -> bool:
        """True = مسموح، False = تجاوز الحد"""
        key = f"rate:{user_id}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 60)
        return count <= limit

    async def log_blocked_pattern(self, pattern: str):
        """تسجيل أنماط الهجمات للتعلم"""
        await self.redis.lpush("security:blocked_patterns", pattern)
        await self.redis.ltrim("security:blocked_patterns", 0, 999)

    # ── الإحصاءات العامة ─────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        user_counts = await self.count_users()
        patterns = await self.redis.llen("security:blocked_patterns")
        return {
            "users":            user_counts,
            "blocked_patterns": patterns,
            "redis_ping":       await self.redis.ping(),
        }


# ── مثيل وحيد ──────────────────────────────────────────────────────────────
db = Database()

"""
رائد — State Manager v5.1
إصلاح: ذاكرة المستخدمين + رسائل الباقات
"""

import json
import logging
import os
import time
import base64
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'bot_state.json')

# ═══════════════════════════════════════════════════════════
# تعريف الباقات
# ═══════════════════════════════════════════════════════════
TIERS = {
    "free":    {"name": "🆓 مجاني",   "coins": 30,   "level": 0},
    "silver":  {"name": "🥈 فضي",     "coins": 90,   "level": 1},
    "gold":    {"name": "🥇 ذهبي",    "coins": 150,  "level": 2},
    "diamond": {"name": "💎 ماسي",    "coins": 350,  "level": 3},
    "custom":  {"name": "⚙️ خاصة",   "coins": 100,  "level": 2},
    "admin":   {"name": "👑 مدير",    "coins": 9999, "level": 99},
}

# الأوامر والباقة المطلوبة لكل أمر
CMD_TIER = {
    # ── مجاني ──
    "start":         "free",
    "help":          "free",
    "about":         "free",
    "premium":       "free",
    "upgrade":       "free",
    "quicksignal":   "free",
    "portfolio":     "free",
    "setportfolio":  "free",
    "stats":         "free",

    # ── فضي ──
    "signal":        "silver",
    "news":          "silver",
    "regime":        "silver",
    "events":        "gold",    # M#34
    "autotrade":     "silver",
    "execute":       "silver",
    "risk":          "silver",
    "approve":       "silver",
    "reject":        "silver",
    "live":          "silver",
    "trades":        "silver",

    # ── ذهبي ──
    "analyze":       "gold",
    "planweek":      "gold",
    "backtest":      "gold",   # ✅ إصلاح #4: نُقل من ماسي إلى ذهبي

    # ── ماسي ──
    "chart":         "diamond",
    "liquidity":     "diamond",
    "onchain":       "diamond",
    "planmonth":     "diamond",
    "drift":         "diamond",

    # ── مدير ──
    "killswitch":    "admin",
    "setpremium":    "admin",
    "settier":       "admin",
    "broadcast":     "admin",
    "setcustom":     "admin",
}

# المنصات لكل باقة
TIER_EXCHANGES = {
    "free":    [],
    "silver":  ["okx"],
    "gold":    ["okx", "bitget", "bybit"],
    "diamond": ["okx", "bitget", "bybit", "binance", "mexc"],
    "custom":  ["okx"],
    "admin":   ["okx", "bitget", "bybit", "binance", "mexc"],
}

# ═══════════════════════════════════════════════════════════
# رسائل الترقية — مخصصة لكل باقة وكل أمر
# ═══════════════════════════════════════════════════════════

# ترتيب الباقات للعرض
TIER_ORDER = ["free", "silver", "gold", "diamond"]
TIER_UPGRADE_CMD = {
    "free":    "الباقة المجانية",
    "silver":  "الباقة الفضية",
    "gold":    "الباقة الذهبية",
    "diamond": "الباقة الماسية",
}

def _build_blocked_message(user_tier: str, required_tier: str, command: str) -> str:
    """
    بناء رسالة ترقية دقيقة:
    - تذكر باقة المستخدم الحالية
    - تذكر الباقة المطلوبة بالضبط
    - تعرض الأمر المحجوب
    """
    curr_name = TIERS[user_tier]["name"]
    req_name  = TIERS[required_tier]["name"]

    # تحديد الباقة التالية المباشرة للمستخدم
    curr_level = TIERS[user_tier]["level"]
    next_tier  = None
    for t in TIER_ORDER:
        if TIERS[t]["level"] > curr_level:
            next_tier = t
            break

    next_name = TIERS[next_tier]["name"] if next_tier else req_name

    # هل المطلوب هو الباقة التالية مباشرة أم أعلى؟
    if next_tier and TIERS[next_tier]["level"] >= TIERS[required_tier]["level"]:
        upgrade_to = next_name
    else:
        upgrade_to = req_name

    # بدائل متاحة لكل أمر محجوب
    alternatives = {
        "analyze":    "• جرّب: /signal (فضي+) أو /quicksignal (مجاني)",
        "backtest":   "• جرّب: /signal للإشارة مباشرة",
        "events":     "• جرّب: /regime لحالة السوق",
        "planweek":   "• جرّب: /signal لإشارة العملة",
        "planmonth":  "• جرّب: /planweek (ذهبي+)",
        "chart":      "• جرّب: /quicksignal للتحليل السريع",
        "liquidity":  "• جرّب: /signal للإشارة مع المستويات",
        "onchain":    "• جرّب: /regime لحالة السوق",
    }
    alt_txt = alternatives.get(command, "")

    return (
        f"🔒 *{curr_name} — هذا الأمر غير متاح*\n\n"
        f"• الأمر `/{command}` متاح من: {req_name} وأعلى\n"
        + (f"\n{alt_txt}\n" if alt_txt else "\n") +
        f"⬆️ الترقية إلى {upgrade_to}: /upgrade\n"
        f"📋 مزايا باقتك: /premium"
    )


class StateManager:
    def __init__(self):
        self._state: Dict[str, Any] = {"users": {}}
        self._redis      = None
        self._redis_ok   = False
        self._init_redis()
        self._load_fallback()
        self._apply_env_vars()
        logger.info(f"✅ StateManager v5.1 (Redis={'✅' if self._redis_ok else '❌ fallback'})")

    # ─── Redis ───────────────────────────────────────────────
    def _init_redis(self):
        try:
            import redis as redis_lib
            REDIS_ENV_NAMES = ["REDIS_URL","REDIS_PUBLIC_URL","REDIS_PRIVATE_URL","REDISURL"]
            redis_url = ""
            for env_name in REDIS_ENV_NAMES:
                val = os.environ.get(env_name, "").strip()
                if val:
                    redis_url = val
                    logger.info(f"Redis URL from: {env_name}")
                    break
            if not redis_url:
                return
            for kwargs in [
                {"decode_responses": True, "socket_connect_timeout": 10, "socket_timeout": 10},
                {"decode_responses": True, "socket_connect_timeout": 20},
            ]:
                try:
                    self._redis = redis_lib.from_url(redis_url, **kwargs)
                    self._redis.ping()
                    self._redis_ok = True
                    return
                except Exception as e:
                    logger.debug(f"Redis attempt: {e}")
        except Exception as e:
            logger.debug(f"_init_redis: {e}")

    def _redis_get_user(self, user_id: int) -> dict:
        if not self._redis_ok: return {}
        try:
            key  = f"raed:user:{user_id}"
            data = self._redis.get(key)
            return json.loads(data) if data else {}
        except Exception: return {}

    def _redis_set_user(self, user_id: int, data: dict):
        if not self._redis_ok: return
        try:
            self._redis.set(f"raed:user:{user_id}", json.dumps(data, ensure_ascii=False))
        except Exception: pass

    def _redis_get_all_users(self) -> list:
        if not self._redis_ok: return []
        try:
            keys = self._redis.keys("raed:user:*")
            return [int(k.split(":")[-1]) for k in keys if k.split(":")[-1].isdigit()]
        except Exception: return []

    # ─── تحميل/حفظ ───────────────────────────────────────────
    def _load_fallback(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                logger.info(f"✅ State loaded: {len(self._state.get('users', {}))} users")
        except Exception as e:
            logger.warning(f"state load: {e}")
            self._state = {"users": {}}

    def save(self):
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"state save: {e}")

    def _apply_env_vars(self):
        owner = self._get_owner_id()
        if owner:
            ud = self._get_user(owner)
            ud["tier"] = "admin"
            self._set_user(owner, ud)
        for uid in self._get_admin_ids():
            ud = self._get_user(uid)
            if ud.get("tier") != "admin":
                ud["tier"] = "admin"
                self._set_user(uid, ud)

    def initialize_owner(self):
        self._apply_env_vars()
        if self._redis_ok:
            self._sync_from_redis()
        logger.info("✅ StateManager: initialized")

    def _sync_from_redis(self):
        """
        مزامنة شاملة: users + virtual wallets + autotrade
        يضمن عدم فقدان أي بيانات بعد كل deploy
        """
        try:
            import json as _j
            # 1. تحميل جميع users من Redis
            redis_users = self._redis_get_all_users()
            for uid in redis_users:
                ud = self._redis_get_user(uid)
                if ud:
                    self._state.setdefault("users", {})[str(uid)] = ud

            # 2. رفع مستخدمي _state إلى Redis
            for uid_str, ud in self._state.get("users", {}).items():
                if uid_str.isdigit():
                    uid = int(uid_str)
                    if uid not in redis_users and isinstance(ud, dict):
                        # نحفظ الجميع وليس فقط non-free
                        if ud.get("tier","free") != "free" or ud.get("autotrade_on"):
                            self._redis_set_user(uid, ud)

            # 3. تحميل virtual wallets من Redis
            if self._redis_ok:
                try:
                    vw_keys = self._redis.keys("raed:vw:*")
                    for key in vw_keys:
                        uid_str = key.decode().replace("raed:vw:", "") if isinstance(key, bytes) else key.replace("raed:vw:", "")
                        raw = self._redis.get(key)
                        if raw:
                            self._state.setdefault("wallets", {})[uid_str] = _j.loads(raw)
                except Exception as e:
                    logger.debug(f"sync wallets: {e}")

            if redis_users:
                self.save()
                logger.info(f"✅ Redis sync شامل: {len(redis_users)} users + {len(self._state.get('wallets',{}))} wallets")
        except Exception as e:
            logger.warning(f"_sync_from_redis: {e}")

    # ═══════════════════════════════════════════════════════════
    # إدارة الباقات
    # ═══════════════════════════════════════════════════════════
    def get_tier(self, user_id: int) -> str:
        owner = self._get_owner_id()
        if owner and owner == user_id:
            return "admin"
        if user_id in self._get_admin_ids():
            return "admin"
        ud   = self._get_user(user_id)
        tier = ud.get("tier", "free")
        return tier if tier in TIERS else "free"

    def set_tier(self, user_id: int, tier: str,
                  by: str = "admin", requester_id: int = 0) -> bool:
        if tier not in TIERS:
            return False
        if requester_id and self.get_tier(requester_id) != "admin":
            return False
        owner = self._get_owner_id()
        if owner and user_id == owner and requester_id and requester_id != owner:
            return False
        ud = self._get_user(user_id)
        ud["tier"]       = tier
        ud["tier_by"]    = by
        ud["tier_since"] = time.time()
        self._set_user(user_id, ud)
        logger.info(f"tier: {user_id} → {tier} by {by}")
        return True

    def get_tier_name(self, user_id: int) -> str:
        return TIERS[self.get_tier(user_id)]["name"]

    def coin_limit(self, user_id: int) -> int:
        tier = self.get_tier(user_id)
        if tier == "admin":
            return 9999
        ud = self._get_user(user_id)
        if tier == "custom" and "custom_coins" in ud:
            return int(ud["custom_coins"])
        return TIERS[tier]["coins"]

    def can_use_command(self, user_id: int, command: str) -> bool:
        tier     = self.get_tier(user_id)
        required = CMD_TIER.get(command, "free")
        if tier == "custom":
            ud = self._get_user(user_id)
            custom_cmds = ud.get("custom_commands", [])
            if custom_cmds:
                return command in custom_cmds
            return TIERS["gold"]["level"] >= TIERS[required]["level"]
        if tier == "admin":
            return True
        return TIERS[tier]["level"] >= TIERS[required]["level"]

    def get_blocked_reason(self, user_id: int, command: str) -> str:
        """
        ✅ إصلاح #3: رسائل ترقية دقيقة لكل مستخدم
        تعرض باقته الحالية والباقة المطلوبة بالضبط
        """
        user_tier    = self.get_tier(user_id)
        required     = CMD_TIER.get(command, "free")
        return _build_blocked_message(user_tier, required, command)

    def allowed_exchanges(self, user_id: int) -> List[str]:
        tier = self.get_tier(user_id)
        if tier == "custom":
            ud = self._get_user(user_id)
            return ud.get("custom_exchanges", TIER_EXCHANGES["silver"])
        return TIER_EXCHANGES.get(tier, [])

    def can_use_exchange(self, user_id: int, exchange: str) -> bool:
        tier = self.get_tier(user_id)
        if tier == "admin": return True
        return exchange.lower() in self.allowed_exchanges(user_id)

    def can_use_live_trading(self, user_id: int) -> bool:
        return self.get_tier(user_id) != "free"

    # ═══ الباقة الخاصة ══════════════════════════════════════
    def set_custom_tier(self, user_id: int, config: dict,
                         requester_id: int = 0) -> bool:
        if requester_id and self.get_tier(requester_id) != "admin":
            return False
        ud = self._get_user(user_id)
        ud["tier"] = "custom"
        if "coins"     in config: ud["custom_coins"]     = config["coins"]
        if "commands"  in config: ud["custom_commands"]  = config["commands"]
        if "exchanges" in config: ud["custom_exchanges"] = config["exchanges"]
        if "label"     in config: ud["custom_label"]     = config["label"]
        ud["tier_by"]    = f"custom by {requester_id}"
        ud["tier_since"] = time.time()
        self._set_user(user_id, ud)
        logger.info(f"custom tier: {user_id} — {config}")
        return True

    def get_custom_config(self, user_id: int) -> dict:
        if self.get_tier(user_id) != "custom":
            return {}
        ud = self._get_user(user_id)
        return {
            "coins":     ud.get("custom_coins", 100),
            "commands":  ud.get("custom_commands", []),
            "exchanges": ud.get("custom_exchanges", ["okx"]),
            "label":     ud.get("custom_label", "⚙️ خاصة"),
        }

    # ═══ معلومات ════════════════════════════════════════════
    def is_premium(self, user_id: int) -> bool:
        return TIERS[self.get_tier(user_id)]["level"] >= 1

    def set_premium(self, user_id: int, is_prem: bool, by: str = "admin"):
        self.set_tier(user_id, "silver" if is_prem else "free", by=by)

    # ═══ إعدادات التداول ════════════════════════════════════
    def get_futures_enabled(self, user_id: int) -> bool:
        tier = self.get_tier(user_id)
        if tier == "admin": return True
        if TIERS[tier]["level"] < 2: return False
        return self._get_user(user_id).get("futures_enabled", False)

    def set_futures_enabled(self, user_id: int, enabled: bool):
        if TIERS[self.get_tier(user_id)]["level"] < 2: return
        ud = self._get_user(user_id)
        ud["futures_enabled"] = enabled
        self._set_user(user_id, ud)

    def get_margin_enabled(self, user_id: int) -> bool:
        tier = self.get_tier(user_id)
        if tier == "admin": return True
        if TIERS[tier]["level"] < 2: return False
        return self._get_user(user_id).get("margin_enabled", False)

    def set_margin_enabled(self, user_id: int, enabled: bool):
        if TIERS[self.get_tier(user_id)]["level"] < 2: return
        ud = self._get_user(user_id)
        ud["margin_enabled"] = enabled
        self._set_user(user_id, ud)

    def is_autotrade_on(self, user_id: int) -> bool:
        return bool(self._get_user(user_id).get("autotrade_on", False))

    def set_autotrade_on(self, user_id: int, enabled: bool):
        ud = self._get_user(user_id)
        ud["autotrade_on"] = enabled
        self._set_user(user_id, ud)
        # حفظ فوري في Redis لضمان الاستمرارية عند Restart
        if self._redis_ok:
            try:
                self._redis_set_user(user_id, ud)
                logger.info(f"autotrade_on={enabled} saved to Redis for user {user_id}")
            except Exception as e:
                logger.warning(f"set_autotrade_on Redis: {e}")

    def can_use_autotrade_free(self, user_id: int) -> bool:
        if self.get_tier(user_id) != "free": return True
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

    # ═══ المحفظة ════════════════════════════════════════════
    def get_user_portfolio(self, user_id: int, default: float = 10000) -> float:
        owner = self._get_owner_id()
        if owner and user_id == owner:
            return float(os.environ.get("PORTFOLIO_SIZE", default))
        return float(self._get_user(user_id).get("portfolio", default))

    def set_user_portfolio(self, user_id: int, amount: float):
        ud = self._get_user(user_id)
        ud["portfolio"] = amount
        self._set_user(user_id, ud)

    # ═══ قوائم المستخدمين ═══════════════════════════════════
    def get_all_user_ids(self) -> list:
        return [int(uid) for uid in self._state.get("users", {}).keys()
                if str(uid).isdigit()]

    # ── Virtual Wallet (Redis-persistent) ──────────────────────
    # ══════════════════════════════════════════════════════
    # نظام الاستبيان الشخصي (T4)
    # ══════════════════════════════════════════════════════
    def get_profile(self, user_id: int) -> dict:
        """يُعيد الملف الشخصي للمستخدم من الاستبيان."""
        return self._get_user(user_id).get("profile", {})

    def save_profile(self, user_id: int, profile: dict):
        """يحفظ الملف الشخصي + تاريخ آخر استبيان."""
        import time as _t
        ud = self._get_user(user_id)
        ud["profile"]           = profile
        ud["profile_updated_at"] = _t.time()
        ud["profile_done"]      = True
        self._set_user(user_id, ud)
        if self._redis_ok:
            try: self._redis_set_user(user_id, ud)
            except: pass

    def is_profile_done(self, user_id: int) -> bool:
        """هل أكمل المستخدم الاستبيان؟"""
        return bool(self._get_user(user_id).get("profile_done", False))

    def needs_profile_reminder(self, user_id: int) -> bool:
        """هل يحتاج تذكيراً بالاستبيان؟"""
        ud = self._get_user(user_id)
        if ud.get("profile_done"):
            return False
        # تذكير كل 3 رسائل
        count = ud.get("reminder_count", 0) + 1
        ud["reminder_count"] = count
        self._set_user(user_id, ud)
        return count % 3 == 0

    def log_violation(self, user_id: int, violation: dict):
        """يُسجّل مخالفة للخطة الشخصية."""
        import time as _t
        ud = self._get_user(user_id)
        violations = ud.get("violations", [])
        violations.append({**violation, "ts": _t.time()})
        ud["violations"] = violations[-50:]  # آخر 50 مخالفة
        self._set_user(user_id, ud)

    def get_violations(self, user_id: int) -> list:
        """يُعيد قائمة المخالفات."""
        return self._get_user(user_id).get("violations", [])

    # ── T5: ملاحظات المستخدم على الخطط/الصفقات ─────────────
    def save_user_comment(self, user_id: int, comment: dict):
        """يحفظ ملاحظة المستخدم على خطة أو صفقة."""
        import time as _t
        ud = self._get_user(user_id)
        comments = ud.get("comments", [])
        comments.append({**comment, "ts": _t.time()})
        ud["comments"] = comments[-100:]
        self._set_user(user_id, ud)
        if self._redis_ok:
            try: self._redis_set_user(user_id, ud)
            except: pass

    def get_user_comments(self, user_id: int) -> list:
        return self._get_user(user_id).get("comments", [])

    def get_user_preferences(self, user_id: int) -> dict:
        """تفضيلات التداول المستمرة للمستخدم."""
        return self._get_user(user_id).get("preferences", {})

    def save_user_preferences(self, user_id: int, prefs: dict):
        ud = self._get_user(user_id)
        existing = ud.get("preferences", {})
        existing.update(prefs)
        ud["preferences"] = existing
        self._set_user(user_id, ud)
        if self._redis_ok:
            try: self._redis_set_user(user_id, ud)
            except: pass

    # ── T7: إعدادات الاستراتيجية الشخصية ───────────────────
    def get_strategy_config(self, user_id: int) -> dict:
        """إعدادات الاستراتيجية المخصصة."""
        tier = self.get_tier(user_id)
        # حدود افتراضية حسب الباقة
        defaults = {
            "free":    {"min_confidence": 0.75, "max_position_pct": 5,  "max_daily_trades": 2},
            "silver":  {"min_confidence": 0.70, "max_position_pct": 8,  "max_daily_trades": 4},
            "gold":    {"min_confidence": 0.65, "max_position_pct": 15, "max_daily_trades": 6},
            "diamond": {"min_confidence": 0.60, "max_position_pct": 25, "max_daily_trades": 10},
            "admin":   {"min_confidence": 0.55, "max_position_pct": 35, "max_daily_trades": 20},
        }
        base = defaults.get(tier, defaults["free"]).copy()
        # تطبيق إعدادات الاستبيان إذا وُجدت
        profile = self.get_profile(user_id)
        if profile:
            risk_level = profile.get("risk_level", "medium")
            if risk_level == "low":
                base["min_confidence"] = min(base["min_confidence"] + 0.05, 0.90)
                base["max_position_pct"] = max(base["max_position_pct"] - 2, 3)
            elif risk_level == "high":
                base["min_confidence"] = max(base["min_confidence"] - 0.05, 0.50)
                base["max_position_pct"] = min(base["max_position_pct"] + 5, 35)
        # تطبيق override المستخدم إذا وُجد
        overrides = self._get_user(user_id).get("strategy_override", {})
        base.update(overrides)
        return base

    def can_update_profile(self, user_id: int) -> tuple:
        """
        يتحقق إذا كان المستخدم يمكنه تحديث ملفه.
        يُعيد (can_update: bool, reason: str)
        """
        import time as _t
        tier = self.get_tier(user_id)
        ud   = self._get_user(user_id)
        last = ud.get("profile_updated_at", 0)
        now  = _t.time()
        elapsed_days = (now - last) / 86400

        if tier in ("free", "silver"):
            if elapsed_days >= 120:  # 4 أشهر
                return True, "✅ يمكنك تحديث ملفك الشخصي"
            remaining = int(120 - elapsed_days)
            return False, f"⏳ يمكنك التحديث بعد {remaining} يوم"

        # gold+ يحتاج شروط أداء
        if tier in ("gold", "diamond", "admin"):
            # تحقق من 21/30 صفقة رابحة + 50% ربح
            vw = self.get_virtual_wallet(user_id)
            if not vw:
                return False, "⏳ لم تنفّذ صفقات كافية بعد"
            history = vw.get("history", [])
            sells   = [t for t in history if t.get("type") == "sell"]
            last_30 = sells[-30:]
            wins    = [t for t in last_30 if t.get("pnl", 0) > 0]
            if len(wins) >= 21:
                total_pnl = sum(t.get("pnl", 0) for t in sells)
                initial   = 10000
                if total_pnl / initial >= 0.50:
                    return True, "✅ مستحق — 21+ صفقة رابحة و50%+ ربح"
            return False, f"⏳ {len(wins)}/21 صفقة رابحة في آخر 30 صفقة"

        return True, "✅ يمكنك التحديث"

    # ══ نظام قيود التنفيذ الآلي (Q1-Q5) ══════════════════════
    # اليومي:  5 صفقات × 5%  = 25% من المحفظة
    # الأسبوعي: 2 صفقات × 7.5% = 15%
    # الشهري:  3 صفقات × 10% = 30%

    TRADE_LIMITS = {
        "daily":   {"max_trades": 5,  "pct_per_trade": 0.05,  "window_hours": 24},
        "weekly":  {"max_trades": 2,  "pct_per_trade": 0.075, "window_hours": 168},
        "monthly": {"max_trades": 3,  "pct_per_trade": 0.10,  "window_hours": 720},
    }

    def get_trade_log(self, user_id: int) -> dict:
        """سجل الصفقات المنفَّذة حسب النوع."""
        return self._get_user(user_id).get("auto_trade_log", {
            "daily": [], "weekly": [], "monthly": []})

    def save_trade_log(self, user_id: int, log: dict):
        """يحفظ سجل الصفقات."""
        ud = self._get_user(user_id)
        ud["auto_trade_log"] = log
        self._set_user(user_id, ud)
        if self._redis_ok:
            try: self._redis_set_user(user_id, ud)
            except: pass

    def can_execute_trade(self, user_id: int, symbol: str,
                          scan_type: str, portfolio_value: float) -> tuple:
        """
        يتحقق إذا كان يمكن تنفيذ صفقة جديدة.
        يُعيد (can_trade: bool, amount_usd: float, reason: str)
        """
        import time as _t
        limits = self.TRADE_LIMITS.get(scan_type)
        if not limits:
            return False, 0, f"نوع مسح غير معروف: {scan_type}"

        log = self.get_trade_log(user_id)
        scan_log = log.get(scan_type, [])
        now = _t.time()
        window = limits["window_hours"] * 3600

        # تنظيف السجلات القديمة خارج النافذة الزمنية
        scan_log = [t for t in scan_log if now - t.get("ts", 0) < window]

        # فحص عدد الصفقات
        if len(scan_log) >= limits["max_trades"]:
            return False, 0, (f"وصلت للحد الأقصى {limits['max_trades']} "
                               f"صفقات {scan_type} في هذه الفترة")

        # فحص تكرار نفس العملة في نفس نوع المسح (Q4)
        traded_symbols = [t.get("symbol") for t in scan_log]
        if symbol in traded_symbols:
            return False, 0, (f"العملة {symbol} مُنفَّذة بالفعل في "
                               f"مسح {scan_type} هذه الفترة")

        # حساب المبلغ
        amount = portfolio_value * limits["pct_per_trade"]
        return True, amount, "✅ مسموح"

    def record_auto_trade(self, user_id: int, symbol: str,
                           scan_type: str, amount: float):
        """يُسجّل صفقة آلية منفَّذة."""
        import time as _t
        log = self.get_trade_log(user_id)
        if scan_type not in log:
            log[scan_type] = []
        log[scan_type].append({
            "symbol":    symbol,
            "amount":    amount,
            "ts":        _t.time(),
            "scan_type": scan_type,
        })
        self.save_trade_log(user_id, log)

    def get_virtual_wallet(self, user_id: int) -> dict:
        """يقرأ المحفظة من Redis مباشرة (مفتاح منفصل = لا يُفقَد عند Restart)."""
        if self._redis_ok:
            try:
                raw = self._redis.get(f"raed:vw:{user_id}")
                if raw:
                    import json as _j
                    return _j.loads(raw)
            except Exception:
                pass
        # fallback للـ _state
        return self._state.get("wallets", {}).get(str(user_id), {})

    def save_virtual_wallet(self, user_id: int, wallet_data: dict):
        """يحفظ المحفظة في Redis (مفتاح منفصل) + _state."""
        import json as _j
        if self._redis_ok:
            try:
                self._redis.set(
                    f"raed:vw:{user_id}",
                    _j.dumps(wallet_data, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"save_virtual_wallet Redis: {e}")
        # حفظ في _state أيضاً كـ fallback
        if "wallets" not in self._state:
            self._state["wallets"] = {}
        self._state["wallets"][str(user_id)] = wallet_data
        self.save()  # حفظ في الملف أيضاً

    def get_autotrade_users(self) -> list:
        result = []
        for uid_str, data in self._state.get("users", {}).items():
            if isinstance(data, dict) and data.get("autotrade_on"):
                try: result.append(int(uid_str))
                except ValueError: pass
        return result

    def list_premium_users(self) -> List[dict]:
        result = []
        for uid_str, data in self._state.get("users", {}).items():
            tier = data.get("tier", "free") if isinstance(data, dict) else "free"
            if tier != "free":
                result.append({"user_id": int(uid_str), "tier": tier})
        return result

    # ═══ Exchange credentials ═══════════════════════════════
    def save_exchange_credentials(self, user_id: int, exchange_name: str,
                                    api_key: str, api_secret: str,
                                    passphrase: str = "", testnet: bool = False):
        ud = self._get_user(user_id)
        ud["exchange"] = {
            "name":       exchange_name,
            "key":        self._encrypt(api_key),
            "secret":     self._encrypt(api_secret),
            "passphrase": self._encrypt(passphrase) if passphrase else "",
            "testnet":    testnet,
            "saved_at":   time.time(),
        }
        self._set_user(user_id, ud)

    def load_exchange_credentials(self, user_id: int) -> Optional[dict]:
        ud = self._get_user(user_id)
        ex = ud.get("exchange")
        if not ex: return None
        return {
            "exchange_name": ex.get("name", ""),
            "api_key":       self._decrypt(ex.get("key",       "")),
            "api_secret":    self._decrypt(ex.get("secret",    "")),
            "passphrase":    self._decrypt(ex.get("passphrase","")) if ex.get("passphrase") else "",
            "testnet":       ex.get("testnet", False),
            "saved_at":      ex.get("saved_at", 0),
        }

    def delete_exchange_credentials(self, user_id: int):
        ud = self._get_user(user_id)
        ud.pop("exchange", None)
        self._set_user(user_id, ud)

    def get_all_exchange_users(self) -> list:
        result = []
        for uid_str, data in self._state.get("users", {}).items():
            if isinstance(data, dict) and data.get("exchange"):
                try: result.append(int(uid_str))
                except ValueError: pass
        return result

    def get_redis_status(self) -> str:
        return "🟢 Redis متصل" if self._redis_ok else "🟡 ملف محلي"

    # ═══ Profile ════════════════════════════════════════════
    def format_profile_ar(self, user_id: int) -> str:
        """
        ✅ إصلاح #2: عرض معلومات الباقة الصحيحة للمستخدم
        مع قائمة الأوامر المتاحة لباقته
        """
        tier      = self.get_tier(user_id)
        tier_info = TIERS[tier]
        owner     = self._get_owner_id()
        is_owner  = owner and user_id == owner
        futures   = self.get_futures_enabled(user_id)
        margin    = self.get_margin_enabled(user_id)
        portfolio = self.get_user_portfolio(user_id)
        coins_lim = "∞ غير محدود" if tier == "admin" else str(self.coin_limit(user_id))
        exchanges = self.allowed_exchanges(user_id)
        ex_str    = ", ".join(e.upper() for e in exchanges) if exchanges else "❌ لا ربط حقيقي"

        if tier == "custom":
            cfg = self.get_custom_config(user_id)
            tier_display = cfg.get("label", "⚙️ خاصة")
        else:
            tier_display = "👑 مالك" if is_owner else tier_info["name"]

        # قائمة الأوامر المتاحة لهذه الباقة
        available_cmds = [
            f"/{cmd}" for cmd, req in CMD_TIER.items()
            if self.can_use_command(user_id, cmd)
            and req != "admin"  # لا نعرض أوامر المدير للمستخدمين
        ]
        cmds_str = "  ".join(sorted(available_cmds)) if available_cmds else "—"

        lines = [
            f"👤 *ملف المستخدم*",
            f"• المعرّف: `{user_id}`",
            f"• الباقة: {tier_display}",
            f"• حجم المحفظة: ${portfolio:,.0f}",
            f"• حد العملات: {coins_lim}",
            f"• المنصات: {ex_str}",
            f"• Futures: {'✅' if futures else '❌'}",
            f"• Margin: {'✅' if margin else '❌'}",
            f"• التخزين: {self.get_redis_status()}",
            f"\n📋 *أوامرك المتاحة:*\n{cmds_str}",
        ]
        return "\n".join(lines)

    # ═══ Helpers ════════════════════════════════════════════
    def _get_owner_id(self) -> Optional[int]:
        try:
            val = os.environ.get("OWNER_CHAT_ID", "").strip()
            return int(val) if val and val.isdigit() else None
        except Exception: return None

    def _get_admin_ids(self) -> set:
        try:
            val = os.environ.get("ADMIN_USERS", "").strip()
            return {int(x.strip()) for x in val.split(",")
                    if x.strip().isdigit()} if val else set()
        except Exception: return set()

    def _get_user(self, user_id: int) -> dict:
        """
        ✅ إصلاح #1: يبحث أولاً في Redis ثم في _state كـ fallback
        يمنع فقدان بيانات المستخدمين عند إعادة التشغيل
        """
        if self._redis_ok:
            rd = self._redis_get_user(user_id)
            if rd:
                # حفظ نسخة في _state أيضاً كـ backup
                self._state.setdefault("users", {})[str(user_id)] = rd
                return dict(rd)
        # fallback إلى _state (ملف محلي أو ذاكرة)
        return dict(self._state.setdefault("users", {}).get(str(user_id), {}))

    def _set_user(self, user_id: int, data: dict):
        """يحفظ في Redis و _state معاً دائماً."""
        if self._redis_ok:
            self._redis_set_user(user_id, data)
        self._state.setdefault("users", {})[str(user_id)] = data
        self.save()

    def _encrypt(self, text: str) -> str:
        if not text: return ""
        try:
            key   = (os.environ.get("ENCRYPTION_KEY", "raed2024") * 100)[:len(text)]
            xored = bytes(ord(a) ^ ord(b) for a, b in zip(text, key))
            return base64.b64encode(xored).decode()
        except Exception: return text

    def _decrypt(self, text: str) -> str:
        if not text: return ""
        try:
            xored = base64.b64decode(text.encode())
            key   = (os.environ.get("ENCRYPTION_KEY", "raed2024") * 100)[:len(xored)]
            return bytes(b ^ ord(k) for b, k in zip(xored, key)).decode()
        except Exception: return text


# Singleton
state_manager = StateManager()

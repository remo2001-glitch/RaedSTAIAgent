"""
🔴 رائد — Kill Switch (الطبقة 8)
👤 Human Override / Approval Layer (الطبقة 6)
📋 Audit Logger (الطبقة 9)
"""

import json
import time
import logging
import asyncio
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIT_FILE = Path("logs/audit.jsonl")
AUDIT_FILE.parent.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
# KILL SWITCH
# ══════════════════════════════════════════════════════════════

class KillReason(Enum):
    DRAWDOWN_EXCEEDED    = "تجاوز حد الـ Drawdown"
    DAILY_LOSS_EXCEEDED  = "تجاوز حد الخسارة اليومية"
    API_ERROR            = "أخطاء متكررة في API"
    DATA_ANOMALY         = "شذوذ في البيانات"
    MODEL_DRIFT          = "تراجع حاد في دقة النموذج"
    MANUAL               = "إيقاف يدوي من المستخدم"
    FLASH_CRASH          = "انهيار مفاجئ في السوق"
    EXECUTION_FAILURE    = "فشل متكرر في التنفيذ"


@dataclass
class KillSwitchState:
    active:       bool       = False
    reason:       str        = ""
    triggered_at: float      = 0.0
    triggered_by: str        = "system"
    auto_resume:  bool       = False
    resume_at:    float      = 0.0   # unix timestamp


class KillSwitch:
    """
    يوقف التداول فورياً عند الاستدعاء.
    يُبلّغ المستخدم فوراً عبر تيليجرام.
    """

    def __init__(self):
        self.state  = KillSwitchState()
        self._hooks: List[Callable] = []   # دوال تُستدعى عند التفعيل

    def register_hook(self, fn: Callable):
        """سجّل دالة تُستدعى عند تفعيل الـ Kill Switch (مثل إرسال تيليجرام)."""
        self._hooks.append(fn)

    def trigger(self, reason: KillReason, triggered_by: str = "system",
                auto_resume_hours: int = 0):
        if self.state.active:
            return   # مفعّل بالفعل

        self.state.active       = True
        self.state.reason       = reason.value
        self.state.triggered_at = time.time()
        self.state.triggered_by = triggered_by

        if auto_resume_hours > 0:
            self.state.auto_resume = True
            self.state.resume_at   = time.time() + auto_resume_hours * 3600

        audit_logger.log_event("kill_switch_triggered", {
            "reason": reason.value, "by": triggered_by,
        })

        logger.critical(f"🔴 KILL SWITCH: {reason.value}")

        # استدعاء الـ hooks
        for fn in self._hooks:
            try:
                if asyncio.iscoroutinefunction(fn):
                    asyncio.create_task(fn(self.state))
                else:
                    fn(self.state)
            except Exception as e:
                logger.error(f"Kill hook error: {e}")

    def reset(self, reset_by: str = "user"):
        if not self.state.active:
            return
        audit_logger.log_event("kill_switch_reset", {"by": reset_by})
        self.state = KillSwitchState()
        logger.info(f"✅ Kill Switch reset by {reset_by}")

    def check_auto_resume(self):
        if (self.state.active and self.state.auto_resume
                and time.time() >= self.state.resume_at):
            self.reset("auto_resume")

    @property
    def is_active(self) -> bool:
        self.check_auto_resume()
        return self.state.active

    def status_ar(self) -> str:
        if not self.state.active:
            return "✅ نظام التداول يعمل بشكل طبيعي"
        elapsed = (time.time() - self.state.triggered_at) / 60
        msg = (f"🔴 *Kill Switch مفعّل*\n"
               f"السبب: {self.state.reason}\n"
               f"منذ: {elapsed:.0f} دقيقة\n"
               f"بواسطة: {self.state.triggered_by}")
        if self.state.auto_resume:
            remaining = max(0, self.state.resume_at - time.time()) / 3600
            msg += f"\nإعادة تشغيل تلقائية بعد: {remaining:.1f} ساعة"
        return msg

    # ── فحوصات تلقائية ──────────────────────────────────────
    def check_drawdown(self, drawdown_pct: float, limit_pct: float = 0.15):
        if drawdown_pct >= limit_pct and not self.state.active:
            self.trigger(KillReason.DRAWDOWN_EXCEEDED, "risk_engine", auto_resume_hours=24)

    def check_api_errors(self, error_count: int, threshold: int = 10):
        if error_count >= threshold and not self.state.active:
            self.trigger(KillReason.API_ERROR, "system", auto_resume_hours=1)

    def check_flash_crash(self, price_change_pct: float, threshold: float = -20):
        if price_change_pct <= threshold and not self.state.active:
            self.trigger(KillReason.FLASH_CRASH, "system", auto_resume_hours=6)


# ══════════════════════════════════════════════════════════════
# HUMAN OVERRIDE / APPROVAL LAYER
# ══════════════════════════════════════════════════════════════

class OverrideReason(Enum):
    HIGH_RISK       = "صفقة عالية المخاطر"
    MACRO_EVENT     = "حدث ماكرو مهم قادم"
    LARGE_SIZE      = "حجم صفقة كبير"
    STRATEGY_CHANGE = "تغيير إعدادات الاستراتيجية"
    MODEL_RESET     = "إعادة تهيئة النموذج"
    MANUAL_REQUEST  = "طلب يدوي"


@dataclass
class PendingApproval:
    approval_id: str
    reason:      OverrideReason
    description: str
    data:        Dict
    requested_at: float = field(default_factory=time.time)
    expires_at:  float  = 0.0
    callback:    Optional[Callable] = None


class HumanOverrideLayer:
    """
    يعترض العمليات الحساسة ويطلب موافقة المستخدم.
    الطلبات تنتهي صلاحيتها إذا لم يُرد خلال timeout.
    """

    def __init__(self, timeout_minutes: int = 15):
        self.timeout_seconds = timeout_minutes * 60
        self._pending: Dict[str, PendingApproval] = {}
        self._notify_fn: Optional[Callable] = None

    def set_notify_fn(self, fn: Callable):
        """دالة ترسل الإشعار للمستخدم عبر تيليجرام."""
        self._notify_fn = fn

    def needs_approval(self, risk_score: float, size_usd: float,
                       confidence: float, macro_event: bool = False) -> Optional[OverrideReason]:
        """يُحدد إذا كانت العملية تحتاج موافقة."""
        if macro_event:
            return OverrideReason.MACRO_EVENT
        if size_usd > 2000:
            return OverrideReason.LARGE_SIZE
        if risk_score > 0.7:
            return OverrideReason.HIGH_RISK
        if confidence < 0.70 and size_usd > 500:
            return OverrideReason.HIGH_RISK
        return None

    async def request_approval(self, approval_id: str, reason: OverrideReason,
                                description: str, data: Dict,
                                callback: Optional[Callable] = None) -> str:
        """يطلب موافقة ويُعيد approval_id للمستخدم."""
        pending = PendingApproval(
            approval_id=approval_id,
            reason=reason,
            description=description,
            data=data,
            expires_at=time.time() + self.timeout_seconds,
            callback=callback,
        )
        self._pending[approval_id] = pending

        audit_logger.log_event("approval_requested", {
            "id": approval_id, "reason": reason.value})

        if self._notify_fn:
            msg = self._format_approval_request(pending)
            await self._notify_fn(msg, approval_id)

        return approval_id

    async def approve(self, approval_id: str, approved_by: str = "user") -> bool:
        pending = self._pending.get(approval_id)
        if not pending:
            return False
        if time.time() > pending.expires_at:
            del self._pending[approval_id]
            return False

        audit_logger.log_event("approval_granted", {
            "id": approval_id, "by": approved_by})

        if pending.callback:
            try:
                await pending.callback(approved=True, data=pending.data)
            except Exception as e:
                logger.error(f"Approval callback error: {e}")

        del self._pending[approval_id]
        return True

    async def reject(self, approval_id: str, rejected_by: str = "user") -> bool:
        pending = self._pending.pop(approval_id, None)
        if not pending:
            return False
        audit_logger.log_event("approval_rejected", {
            "id": approval_id, "by": rejected_by})
        if pending.callback:
            try:
                await pending.callback(approved=False, data=pending.data)
            except Exception as e:
                logger.error(f"Rejection callback error: {e}")
        return True

    def cleanup_expired(self):
        now = time.time()
        expired = [k for k, v in self._pending.items() if now > v.expires_at]
        for k in expired:
            logger.info(f"Approval {k} expired")
            del self._pending[k]

    def _format_approval_request(self, p: PendingApproval) -> str:
        remaining = max(0, p.expires_at - time.time()) / 60
        return (
            f"👤 *طلب موافقة*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 السبب: {p.reason.value}\n"
            f"📝 التفاصيل: {p.description}\n"
            f"⏰ ينتهي خلال: {remaining:.0f} دقيقة\n\n"
            f"الرمز: `{p.approval_id}`\n"
            f"للموافقة: /approve {p.approval_id}\n"
            f"للرفض: /reject {p.approval_id}"
        )

    def pending_list_ar(self) -> str:
        self.cleanup_expired()
        if not self._pending:
            return "✅ لا يوجد طلبات موافقة معلقة"
        lines = ["👤 *طلبات الموافقة المعلقة*\n"]
        for pid, p in self._pending.items():
            rem = max(0, p.expires_at - time.time()) / 60
            lines.append(f"• `{pid}` — {p.reason.value} ({rem:.0f}د متبقية)")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# AUDIT LOGGER
# ══════════════════════════════════════════════════════════════

class AuditLogger:
    """
    يسجل كل قرار وحدث وخطأ مع السبب — قابل للمراجعة.
    يكتب JSONL بحيث كل سطر = حدث.
    """

    def log_event(self, event_type: str, data: Dict,
                  level: str = "info"):
        entry = {
            "ts":         time.time(),
            "time_utc":   _utc_str(),
            "type":       event_type,
            "level":      level,
            **data,
        }
        try:
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Audit write fail: {e}")

        if level == "error":
            logger.error(f"[AUDIT] {event_type}: {data}")
        elif level == "warning":
            logger.warning(f"[AUDIT] {event_type}: {data}")

    def log_trade(self, symbol: str, direction: str, size: float,
                  confidence: float, regime: str, reason: str):
        self.log_event("trade_signal", {
            "symbol":     symbol,
            "direction":  direction,
            "size_usd":   size,
            "confidence": round(confidence, 3),
            "regime":     regime,
            "reason":     reason,
        })

    def log_trade_result(self, symbol: str, pnl: float, pnl_pct: float,
                          hold_hours: float, exit_reason: str):
        self.log_event("trade_result", {
            "symbol":      symbol,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "hold_hours":  round(hold_hours, 1),
            "exit_reason": exit_reason,
        })

    def log_error(self, source: str, error: str, context: Dict = None):
        self.log_event("error", {
            "source":  source,
            "error":   str(error),
            "context": context or {},
        }, level="error")

    def get_recent(self, n: int = 50, event_type: str = None) -> List[Dict]:
        """يقرأ آخر N حدث من ملف الـ audit."""
        try:
            lines = AUDIT_FILE.read_text(encoding="utf-8").strip().split("\n")
            events = [json.loads(l) for l in lines if l]
            if event_type:
                events = [e for e in events if e.get("type") == event_type]
            return events[-n:]
        except Exception:
            return []

    def pnl_summary(self) -> Dict:
        """ملخص الأداء من سجل الصفقات."""
        trades = self.get_recent(1000, "trade_result")
        if not trades:
            return {"trades": 0, "total_pnl": 0, "win_rate": 0}
        pnls    = [t["pnl"] for t in trades]
        wins    = [p for p in pnls if p > 0]
        return {
            "trades":    len(pnls),
            "total_pnl": round(sum(pnls), 2),
            "win_rate":  round(len(wins) / len(pnls) * 100, 1),
            "avg_win":   round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss":  round(sum(p for p in pnls if p < 0) / max(len(pnls)-len(wins), 1), 2),
        }

    def format_weekly_report_ar(self) -> str:
        s = self.pnl_summary()
        return (
            f"📊 *التقرير الأسبوعي — رائد التداول الذكي*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 إجمالي الصفقات: {s['trades']}\n"
            f"💰 صافي الربح/الخسارة: ${s['total_pnl']:+,.2f}\n"
            f"✅ نسبة الفوز: {s['win_rate']:.1f}%\n"
            f"📈 متوسط الربح: ${s['avg_win']:,.2f}\n"
            f"📉 متوسط الخسارة: ${abs(s['avg_loss']):,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 رائد التداول الذكي"
        )


def _utc_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# Singletons
kill_switch    = KillSwitch()
human_override = HumanOverrideLayer()
audit_logger   = AuditLogger()

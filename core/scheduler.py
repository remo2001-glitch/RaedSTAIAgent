"""
⏰ رائد — Scheduler المتكامل v2
جدول المسح والتقارير:
• كل 4 ساعات: مسح شامل + اقتناص فرص + تنفيذ آلي
• كل اثنين ١ ظهر (السعودية): تقرير أسبوعي
• يوم ٣ من كل شهر ١ ظهر: تقرير شهري

توقيت المسح الرباعي (بتوقيت السعودية UTC+3):
  01:00 | 05:00 | 09:00 | 13:00 | 17:00 | 21:00
= 22:00 | 02:00 | 06:00 | 10:00 | 14:00 | 18:00 UTC

الإصلاحات:
- حماية من فوات الدقيقة بنافذة ±1 دقيقة
- إعادة التشغيل الآمنة (لا يُفوت المسح بعد restart)
- معالجة أخطاء send_fn بشكل صحيح
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── ثوابت التوقيت ────────────────────────────────────────────
REPORT_HOUR_UTC   = 10    # ١ ظهر السعودية
REPORT_MINUTE_UTC = 0
WEEKLY_WEEKDAY    = 0     # الاثنين
MONTHLY_DAY       = 3

# ساعات المسح الرباعي بتوقيت UTC
SCAN_HOURS_UTC = {22, 2, 6, 10, 14, 18}


class Scheduler:

    def __init__(self, send_fn: Callable):
        self.send_fn              = send_fn
        self._running             = False
        self._task: Optional[asyncio.Task] = None
        self._weekly_report_fn:   Optional[Callable] = None
        self._monthly_report_fn:  Optional[Callable] = None
        self._scan_fn:            Optional[Callable]  = None

        # نُخزّن الـ timestamp بدلاً من الساعة/اليوم/الشهر فقط
        # لتجنّب مشكلة إعادة التشغيل في نفس الساعة
        self._last_scan_ts:    float = 0.0
        self._last_weekly_ts:  float = 0.0
        self._last_monthly_ts: float = 0.0

    # ── التسجيل ──────────────────────────────────────────────
    def register_weekly(self, fn: Callable):
        self._weekly_report_fn = fn

    def register_monthly(self, fn: Callable):
        self._monthly_report_fn = fn

    def register_scan(self, fn: Callable):
        self._scan_fn = fn

    # ── التشغيل والإيقاف ─────────────────────────────────────
    def start(self):
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._loop())
            logger.info("✅ Scheduler بدأ — مسح كل 4 ساعات")

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("🛑 Scheduler أوقف")

    # ── الحلقة الرئيسية ──────────────────────────────────────
    async def _loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                await self._check_scan(now)
                await self._check_weekly(now)
                await self._check_monthly(now)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(60)

    # ── المسح الرباعي ────────────────────────────────────────
    async def _check_scan(self, now: datetime):
        """
        يُشغّل المسح كل 4 ساعات.
        نافذة 2 دقيقة (0-1) لتجنّب فوات الدقيقة.
        """
        if now.hour not in SCAN_HOURS_UTC:
            return
        if now.minute > 1:
            return
        # تأكد من مرور على الأقل 3 ساعات منذ آخر مسح
        elapsed = time.time() - self._last_scan_ts
        if elapsed < 10_800:   # 3 ساعات بالثواني
            return
        self._last_scan_ts = time.time()
        asyncio.create_task(self._run_scan(now))

    async def _run_scan(self, now: datetime):
        ksa_hour = (now.hour + 3) % 24
        session  = _get_session_name(ksa_hour)
        logger.info(f"🔍 مسح {session} — {ksa_hour:02d}:00 KSA")
        try:
            if self._scan_fn:
                report = await self._scan_fn(session=session, ksa_hour=ksa_hour)
                if report:
                    await self._safe_send(report)
                    logger.info(f"✅ تقرير المسح أُرسل — {session}")
        except Exception as e:
            logger.error(f"Scan error ({session}): {e}")

    # ── التقارير الدورية ─────────────────────────────────────
    async def _check_weekly(self, now: datetime):
        if (now.weekday() != WEEKLY_WEEKDAY
                or now.hour != REPORT_HOUR_UTC
                or now.minute > 1):
            return
        elapsed = time.time() - self._last_weekly_ts
        if elapsed < 86_400 * 6:   # 6 أيام على الأقل
            return
        self._last_weekly_ts = time.time()
        asyncio.create_task(self._send_weekly())

    async def _check_monthly(self, now: datetime):
        if (now.day != MONTHLY_DAY
                or now.hour != REPORT_HOUR_UTC
                or now.minute > 1):
            return
        elapsed = time.time() - self._last_monthly_ts
        if elapsed < 86_400 * 25:   # 25 يوماً على الأقل
            return
        self._last_monthly_ts = time.time()
        asyncio.create_task(self._send_monthly())

    async def _send_weekly(self):
        try:
            if self._weekly_report_fn:
                report = await self._weekly_report_fn()
                await self._safe_send(report)
                logger.info("📊 التقرير الأسبوعي أُرسل")
        except Exception as e:
            logger.error(f"Weekly report error: {e}")

    async def _send_monthly(self):
        try:
            if self._monthly_report_fn:
                report = await self._monthly_report_fn()
                await self._safe_send(report)
                logger.info("📅 التقرير الشهري أُرسل")
        except Exception as e:
            logger.error(f"Monthly report error: {e}")

    async def _safe_send(self, message: str):
        """إرسال آمن مع معالجة الأخطاء."""
        try:
            await self.send_fn(message)
        except Exception as e:
            logger.error(f"Scheduler send_fn error: {e}")

    # ── نصوص توضيحية ─────────────────────────────────────────
    def next_scan_ar(self) -> str:
        now = datetime.now(timezone.utc)
        candidate = None
        for h in sorted(SCAN_HOURS_UTC):
            if h > now.hour or (h == now.hour and now.minute < 58):
                candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
                break
        if not candidate:
            # الساعة التالية غداً
            next_h = min(SCAN_HOURS_UTC)
            candidate = (now + timedelta(days=1)).replace(
                hour=next_h, minute=0, second=0, microsecond=0)
        ksa_h = (candidate.hour + 3) % 24
        hours = max(0, (candidate - now).total_seconds() / 3600)
        return (f"المسح القادم: {ksa_h:02d}:00 KSA — "
                f"بعد {hours:.0f} ساعة ({_get_session_name(ksa_h)})")

    def next_weekly_ar(self) -> str:
        now  = datetime.now(timezone.utc)
        days = (WEEKLY_WEEKDAY - now.weekday()) % 7 or 7
        nxt  = (now + timedelta(days=days)).replace(
            hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
        hours = max(0, (nxt - now).total_seconds() / 3600)
        return (f"التقرير الأسبوعي: "
                f"{nxt.strftime('%Y-%m-%d')} الاثنين ١ ظهراً — بعد {hours:.0f} ساعة")

    def next_monthly_ar(self) -> str:
        now = datetime.now(timezone.utc)
        if (now.day < MONTHLY_DAY
                or (now.day == MONTHLY_DAY and now.hour < REPORT_HOUR_UTC)):
            try:
                nxt = now.replace(day=MONTHLY_DAY,
                                   hour=REPORT_HOUR_UTC, minute=0, second=0)
            except ValueError:
                nxt = None
        else:
            nxt = None
        if not nxt:
            m = now.month % 12 + 1
            y = now.year + (1 if now.month == 12 else 0)
            try:
                nxt = now.replace(year=y, month=m, day=MONTHLY_DAY,
                                   hour=REPORT_HOUR_UTC, minute=0, second=0)
            except ValueError:
                return "التقرير الشهري: يوم ٣ من الشهر القادم ١ ظهراً"
        hours = max(0, (nxt - now).total_seconds() / 3600)
        return (f"التقرير الشهري: "
                f"{nxt.strftime('%Y-%m-%d')} يوم ٣ ١ ظهراً — بعد {hours:.0f} ساعة")


# ── Helper ────────────────────────────────────────────────────
def _get_session_name(ksa_hour: int) -> str:
    if   1  <= ksa_hour < 5:   return "جلسة آسيا (منتصف الليل)"
    elif 5  <= ksa_hour < 9:   return "جلسة آسيا (صباح)"
    elif 9  <= ksa_hour < 13:  return "جلسة أوروبا (صباح)"
    elif 13 <= ksa_hour < 17:  return "جلسة أوروبا (ذروة)"
    elif 17 <= ksa_hour < 21:  return "جلسة أمريكا (افتتاح)"
    else:                       return "جلسة أمريكا (مساء)"

"""
⏰ رائد — Scheduler المتكامل
جدول المسح والتقارير:
• كل 4 ساعات: مسح شامل + اقتناص فرص + تنفيذ آلي
• كل اثنين ١ ظهر (السعودية): تقرير أسبوعي
• يوم ٣ من كل شهر ١ ظهر: تقرير شهري

توقيت المسح الرباعي (بتوقيت السعودية UTC+3):
  01:00 | 05:00 | 09:00 | 13:00 | 17:00 | 21:00
= 22:00 | 02:00 | 06:00 | 10:00 | 14:00 | 18:00 UTC
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── ثوابت التوقيت ────────────────────────────────────────────
REPORT_HOUR_UTC   = 10    # ١ ظهر السعودية
REPORT_MINUTE_UTC = 0
WEEKLY_WEEKDAY    = 0     # الاثنين
MONTHLY_DAY       = 3

# ساعات المسح الرباعي بتوقيت UTC
SCAN_HOURS_UTC = {22, 2, 6, 10, 14, 18}   # كل 4 ساعات


class Scheduler:

    def __init__(self, send_fn: Callable):
        self.send_fn              = send_fn
        self._running             = False
        self._task: Optional[asyncio.Task] = None
        self._weekly_report_fn:   Optional[Callable] = None
        self._monthly_report_fn:  Optional[Callable] = None
        self._scan_fn:            Optional[Callable] = None
        self._last_scan_hour:     int  = -1   # منع التكرار
        self._last_weekly_day:    int  = -1
        self._last_monthly_month: int  = -1

    # ── التسجيل ──────────────────────────────────────────────
    def register_weekly(self, fn: Callable):
        self._weekly_report_fn = fn

    def register_monthly(self, fn: Callable):
        self._monthly_report_fn = fn

    def register_scan(self, fn: Callable):
        """تسجيل دالة المسح الرباعي."""
        self._scan_fn = fn

    # ── التشغيل والإيقاف ─────────────────────────────────────
    def start(self):
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._loop())
            logger.info("✅ Scheduler started — مسح كل 4 ساعات")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("🛑 Scheduler stopped")

    # ── الحلقة الرئيسية ──────────────────────────────────────
    async def _loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                await self._check_scan(now)
                self._check_weekly(now)
                self._check_monthly(now)
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(60)   # فحص كل دقيقة

    # ── المسح الرباعي ────────────────────────────────────────
    async def _check_scan(self, now: datetime):
        """يُشغّل المسح كل 4 ساعات عند بداية الساعة."""
        if (now.hour in SCAN_HOURS_UTC and
                now.minute == 0 and
                now.hour != self._last_scan_hour):
            self._last_scan_hour = now.hour
            asyncio.create_task(self._run_scan(now))

    async def _run_scan(self, now: datetime):
        """
        المسح الرباعي الشامل:
        ١. مسح أفضل العملات
        ٢. تحليل الفرص
        ٣. تنفيذ آلي للفرص القوية (إذا autotrade مفعّل)
        ٤. إرسال تقرير للمستخدم
        """
        ksa_hour = (now.hour + 3) % 24
        session  = _get_session_name(ksa_hour)
        logger.info(f"🔍 بدء المسح الرباعي — {session} ({ksa_hour:02d}:00 KSA)")

        try:
            if self._scan_fn:
                report = await self._scan_fn(session=session, ksa_hour=ksa_hour)
                if report:
                    await self.send_fn(report)
                    logger.info(f"✅ تقرير المسح أُرسل — {session}")
        except Exception as e:
            logger.error(f"Scan error ({session}): {e}")

    # ── التقارير الدورية ─────────────────────────────────────
    def _check_weekly(self, now: datetime):
        if (now.weekday() == WEEKLY_WEEKDAY and
                now.hour   == REPORT_HOUR_UTC and
                now.minute == REPORT_MINUTE_UTC and
                now.day    != self._last_weekly_day):
            self._last_weekly_day = now.day
            asyncio.create_task(self._send_weekly())

    def _check_monthly(self, now: datetime):
        if (now.day    == MONTHLY_DAY and
                now.hour   == REPORT_HOUR_UTC and
                now.minute == REPORT_MINUTE_UTC and
                now.month  != self._last_monthly_month):
            self._last_monthly_month = now.month
            asyncio.create_task(self._send_monthly())

    async def _send_weekly(self):
        try:
            if self._weekly_report_fn:
                report = await self._weekly_report_fn()
                await self.send_fn(report)
                logger.info("📊 Weekly report sent")
        except Exception as e:
            logger.error(f"Weekly report error: {e}")

    async def _send_monthly(self):
        try:
            if self._monthly_report_fn:
                report = await self._monthly_report_fn()
                await self.send_fn(report)
                logger.info("📅 Monthly report sent")
        except Exception as e:
            logger.error(f"Monthly report error: {e}")

    # ── نصوص توضيحية ─────────────────────────────────────────
    def next_scan_ar(self) -> str:
        now  = datetime.now(timezone.utc)
        for h in sorted(SCAN_HOURS_UTC):
            if h > now.hour or (h == now.hour and now.minute < 55):
                nxt = now.replace(hour=h, minute=0, second=0, microsecond=0)
                break
        else:
            nxt = (now + timedelta(days=1)).replace(
                hour=min(SCAN_HOURS_UTC), minute=0, second=0)
        ksa_h = (nxt.hour + 3) % 24
        hours = (nxt - now).total_seconds() / 3600
        return (f"المسح القادم: {ksa_h:02d}:00 KSA — "
                f"بعد {hours:.0f} ساعة ({_get_session_name(ksa_h)})")

    def next_weekly_ar(self) -> str:
        now  = datetime.now(timezone.utc)
        days = (WEEKLY_WEEKDAY - now.weekday()) % 7 or 7
        nxt  = (now + timedelta(days=days)).replace(
            hour=REPORT_HOUR_UTC, minute=0, second=0)
        hours = (nxt - now).total_seconds() / 3600
        return (f"التقرير الأسبوعي القادم: "
                f"{nxt.strftime('%Y-%m-%d')} الاثنين ١ ظهراً — بعد {hours:.0f} ساعة")

    def next_monthly_ar(self) -> str:
        now = datetime.now(timezone.utc)
        if (now.day < MONTHLY_DAY or
                (now.day == MONTHLY_DAY and now.hour < REPORT_HOUR_UTC)):
            nxt = now.replace(day=MONTHLY_DAY,
                               hour=REPORT_HOUR_UTC, minute=0, second=0)
        else:
            m = now.month % 12 + 1
            y = now.year + (1 if now.month == 12 else 0)
            nxt = now.replace(year=y, month=m, day=MONTHLY_DAY,
                               hour=REPORT_HOUR_UTC, minute=0, second=0)
        hours = (nxt - now).total_seconds() / 3600
        return (f"التقرير الشهري القادم: "
                f"{nxt.strftime('%Y-%m-%d')} يوم ٣ ١ ظهراً — بعد {hours:.0f} ساعة")


# ── Helper ────────────────────────────────────────────────────
def _get_session_name(ksa_hour: int) -> str:
    """يُحدد جلسة السوق حسب التوقيت السعودي."""
    if   1  <= ksa_hour < 5:   return "جلسة آسيا (منتصف الليل)"
    elif 5  <= ksa_hour < 9:   return "جلسة آسيا (صباح)"
    elif 9  <= ksa_hour < 13:  return "جلسة أوروبا (صباح)"
    elif 13 <= ksa_hour < 17:  return "جلسة أوروبا (ذروة)"
    elif 17 <= ksa_hour < 21:  return "جلسة أمريكا (افتتاح)"
    else:                       return "جلسة أمريكا (مساء)"

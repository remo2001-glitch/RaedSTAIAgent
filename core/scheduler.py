"""
⏰ رائد — Scheduler
تقرير أسبوعي: كل اثنين الساعة ١٠:٠٠ UTC (١ ظهر السعودية UTC+3)
تقرير شهري:  يوم ٣ من كل شهر الساعة ١٠:٠٠ UTC
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ١ ظهر بتوقيت السعودية = ١٠:٠٠ UTC
REPORT_HOUR_UTC   = 10
REPORT_MINUTE_UTC = 0
WEEKLY_WEEKDAY    = 0   # الاثنين (0=Mon)
MONTHLY_DAY       = 3   # يوم ٣ من الشهر


class Scheduler:
    def __init__(self, send_fn: Callable):
        """
        send_fn: دالة ترسل رسالة للمستخدم عبر تيليجرام
        """
        self.send_fn   = send_fn
        self._running  = False
        self._task: Optional[asyncio.Task] = None
        self._weekly_report_fn:  Optional[Callable] = None
        self._monthly_report_fn: Optional[Callable] = None

    def register_weekly(self, fn: Callable):
        self._weekly_report_fn = fn

    def register_monthly(self, fn: Callable):
        self._monthly_report_fn = fn

    def start(self):
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._loop())
            logger.info("✅ Scheduler started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("🛑 Scheduler stopped")

    async def _loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                self._check_weekly(now)
                self._check_monthly(now)
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(60)   # فحص كل دقيقة

    def _check_weekly(self, now: datetime):
        if (now.weekday() == WEEKLY_WEEKDAY and
                now.hour   == REPORT_HOUR_UTC and
                now.minute == REPORT_MINUTE_UTC):
            asyncio.create_task(self._send_weekly())

    def _check_monthly(self, now: datetime):
        if (now.day    == MONTHLY_DAY and
                now.hour   == REPORT_HOUR_UTC and
                now.minute == REPORT_MINUTE_UTC):
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

    def next_weekly_ar(self) -> str:
        now   = datetime.now(timezone.utc)
        days  = (WEEKLY_WEEKDAY - now.weekday()) % 7 or 7
        nxt   = now + timedelta(days=days)
        nxt   = nxt.replace(hour=REPORT_HOUR_UTC, minute=0, second=0)
        hours = (nxt - now).total_seconds() / 3600
        return f"التقرير الأسبوعي القادم: {nxt.strftime('%Y-%m-%d')} الاثنين ١ ظهراً — بعد {hours:.0f} ساعة"

    def next_monthly_ar(self) -> str:
        now = datetime.now(timezone.utc)
        if now.day < MONTHLY_DAY or (now.day == MONTHLY_DAY and
                                       now.hour < REPORT_HOUR_UTC):
            nxt = now.replace(day=MONTHLY_DAY, hour=REPORT_HOUR_UTC, minute=0)
        else:
            if now.month == 12:
                nxt = now.replace(year=now.year+1, month=1,
                                   day=MONTHLY_DAY, hour=REPORT_HOUR_UTC, minute=0)
            else:
                nxt = now.replace(month=now.month+1,
                                   day=MONTHLY_DAY, hour=REPORT_HOUR_UTC, minute=0)
        hours = (nxt - now).total_seconds() / 3600
        return f"التقرير الشهري القادم: {nxt.strftime('%Y-%m-%d')} يوم ٣ ١ ظهراً — بعد {hours:.0f} ساعة"

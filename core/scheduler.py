"""
⏰ رائد — Scheduler المتكامل v2
جدول المسح والتقارير:
• كل 4 ساعات: مسح شامل + اقتناص فرص + تنفيذ آلي
• كل اثنين 1 ظهر (السعودية): تقرير أسبوعي
• يوم 3 من كل شهر 1 ظهر: تقرير شهري

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
REPORT_HOUR_UTC   = 10    # 1 ظهر السعودية
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

    async def _check_all_limit_orders(self):
        """يفحص جميع Limit Orders المعلقة لجميع المستخدمين."""
        try:
            if not hasattr(self, "_engine") or not self._engine:
                return
            engine = self._engine
            # تحقق من كل OrderManager نشط
            for user_id, info in getattr(engine, "_live_users", {}).items():
                om = info.get("order_manager")
                if om and hasattr(om, "_check_limit_orders"):
                    await om._check_limit_orders(self._notify_user)
                if om and hasattr(om, "_check_trailing_and_protect"):
                    await om._check_trailing_and_protect(self._notify_user)
        except Exception as e:
            logger.debug(f"_check_all_limit_orders: {e}")

    async def _notify_user(self, user_id: int, message: str):
        """يُرسل إشعار لمستخدم محدد."""
        try:
            await self.send_fn(message, user_id=user_id)
        except Exception as e:
            logger.debug(f"_notify_user {user_id}: {e}")

    def set_engine(self, engine):
        """يُسجِّل المحرك للوصول لـ Order Managers."""
        self._engine = engine

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
        tick = 0
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                await self._check_scan(now)
                await self._check_weekly(now)
                await self._check_monthly(now)
                await self._check_coins_update(now)
                await self._check_month_end_review(now)
                # فحص Limit Orders كل 30 ثانية
                if tick % 1 == 0:
                    await self._check_all_limit_orders()
                tick += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(30)


    async def _check_month_end_review(self, now: datetime):
        """
        النقطة 9: مراجعة الصفقات في آخر يوم من كل شهر.
        يُرسل تقرير التعلم لكل مستخدم نشط.
        """
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day != last_day or now.hour != REPORT_HOUR_UTC or now.minute > 1:
            return
        if not hasattr(self, "_last_review_ts"):
            self._last_review_ts = 0.0
        if time.time() - self._last_review_ts < 86400 * 25:
            return
        self._last_review_ts = time.time()
        asyncio.create_task(self._send_monthly_review())

    async def _send_monthly_review(self):
        """يُرسل تقرير التعلم الشهري لكل مستخدم."""
        try:
            engine = getattr(self, "_engine", None)
            if not engine: return
            for user_id, info in getattr(engine, "_live_users", {}).items():
                om = info.get("order_manager")
                if not om or not hasattr(om, "get_lessons_summary"): continue
                summary = om.get_lessons_summary(user_id)
                if summary.get("total", 0) == 0: continue
                best  = summary.get("best", {})
                worst = summary.get("worst", {})
                msg = (
                    f"📚 *التقرير الشهري — الدروس المستفادة*\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 إجمالي الصفقات: {summary['total']}\n"
                    f"✅ رابحة: {summary['wins']} | ❌ خاسرة: {summary['losses']}\n"
                    f"🎯 نسبة النجاح: {summary['win_rate']:.1f}%\n\n"
                    f"🏆 أفضل صفقة: {best.get('symbol','-')} "
                    f"{float(best.get('pnl_pct',0)):+.1f}%\n"
                    f"📉 أسوأ صفقة: {worst.get('symbol','-')} "
                    f"{float(worst.get('pnl_pct',0)):+.1f}%\n\n"
                    f"💡 رائد تعلم من هذه الصفقات لتحسين توصياته القادمة"
                )
                await self._notify_user(user_id, msg)
            logger.info("📚 تقرير المراجعة الشهرية أُرسل")
        except Exception as e:
            logger.error(f"monthly_review: {e}")

    async def _check_coins_update(self, now):
        """يُحدِّث قائمة العملات شهرياً."""
        import time as _time
        now_ts = _time.time()  # دائماً timestamp float
        if not hasattr(self, "_last_coins_upd"):
            self._last_coins_upd = 0
        if now_ts - self._last_coins_upd < 30 * 24 * 3600:
            return
        try:
            from core.coins_list import update_coins_list_from_api
            if hasattr(self.engine, "session") and self.engine.session:
                ok = await update_coins_list_from_api(self.engine.session)
                if ok:
                    self._last_coins_upd = now_ts
                    logger.info("✅ Scheduler: قائمة العملات مُحدَّثة شهرياً")
        except Exception as e:
            logger.debug(f"_check_coins_update: {e}")

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
                # T6: إضافة ملخص الصفقات الافتراضية
                report_with_trades = await self._append_trades_summary(report, "weekly")
                await self._safe_send(report_with_trades)
                logger.info("📊 التقرير الأسبوعي أُرسل")
        except Exception as e:
            logger.error(f"Weekly report error: {e}")

    async def _append_trades_summary(self, report: str, period: str) -> str:
        """T6: إضافة ملخص الصفقات والدروس المستفادة للتقرير."""
        try:
            from core.state_manager import state_manager as _sm_sc
            from core.virtual_wallet import VirtualWallet as _VW_sc
            import datetime as _dt

            summaries = []
            for uid in _sm_sc.get_all_user_ids():
                vw_data = _sm_sc.get_virtual_wallet(uid)
                if not vw_data:
                    continue
                vw = _VW_sc(vw_data)
                history = vw.history or []
                # فلتر حسب الفترة
                now_ts = _dt.datetime.now().timestamp()
                cutoff = now_ts - (7 * 86400 if period == "weekly" else 30 * 86400)
                period_trades = [
                    t for t in history
                    if t.get("type") == "sell"
                    and _dt.datetime.fromisoformat(t.get("time","2000-01-01")).timestamp() > cutoff
                ]
                if not period_trades:
                    continue
                wins   = [t for t in period_trades if t.get("pnl", 0) > 0]
                losses = [t for t in period_trades if t.get("pnl", 0) <= 0]
                net    = sum(t.get("pnl", 0) for t in period_trades)
                wr     = len(wins) / max(len(period_trades), 1) * 100
                # دروس مستفادة
                lessons = []
                if losses:
                    worst = min(losses, key=lambda t: t.get("pnl", 0))
                    lessons.append(f"أسوأ صفقة: {worst.get('symbol','-')} {worst.get('pnl_pct',0):+.1f}%")
                if wins:
                    best = max(wins, key=lambda t: t.get("pnl", 0))
                    lessons.append(f"أفضل صفقة: {best.get('symbol','-')} {best.get('pnl_pct',0):+.1f}%")
                # ملاحظات المستخدم
                comments = _sm_sc.get_user_comments(uid)
                recent_comments = [c for c in comments if c.get("ts", 0) > cutoff]

                _period_ar = "الأسبوعي" if period == "weekly" else "الشهري"
                summary = (
                    f"\n📊 *ملخص الصفقات {_period_ar}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"• إجمالي: {len(period_trades)} | رابحة: {len(wins)} | خاسرة: {len(losses)}\n"
                    f"• نسبة الفوز: {wr:.0f}% | صافي: ${net:+,.2f}\n"
                )
                if lessons:
                    summary += "\n💡 *الدروس المستفادة*\n"
                    for l in lessons:
                        summary += f"• {l}\n"
                if recent_comments:
                    summary += "\n💬 *ملاحظاتك المسجَّلة*\n"
                    for c in recent_comments[-3:]:
                        summary += f"• {c.get('text','')[:80]}\n"
                summaries.append((uid, summary))

            if summaries:
                # أرسل لكل مستخدم تقريره الخاص
                if hasattr(self, "_notify_user"):
                    for uid, summary in summaries:
                        await self._notify_user(uid, report + summary)
                    return ""  # تم الإرسال الفردي
            return report
        except Exception as e:
            logger.debug(f"_append_trades_summary: {e}")
            return report

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
        # نستبعد الساعة الحالية دائماً (المسح يعمل الآن)
        for h in sorted(SCAN_HOURS_UTC):
            if h > now.hour:
                candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
                break
        if not candidate:
            # الساعة التالية في اليوم القادم
            next_h = min(SCAN_HOURS_UTC)
            candidate = (now + timedelta(days=1)).replace(
                hour=next_h, minute=0, second=0, microsecond=0)
        ksa_h = (candidate.hour + 3) % 24
        hours = max(1, round((candidate - now).total_seconds() / 3600))
        return (f"⏰ المسح القادم: {ksa_h:02d}:00 KSA — "
                f"بعد {hours} ساعة — {_get_session_name(ksa_h)}")

    def next_weekly_ar(self) -> str:
        now  = datetime.now(timezone.utc)
        days = (WEEKLY_WEEKDAY - now.weekday()) % 7 or 7
        nxt  = (now + timedelta(days=days)).replace(
            hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
        hours = max(0, (nxt - now).total_seconds() / 3600)
        return (f"التقرير الأسبوعي: "
                f"{nxt.strftime('%Y-%m-%d')} الاثنين 1 ظهراً — بعد {hours:.0f} ساعة")

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
                return "التقرير الشهري: يوم 3 من الشهر القادم 1 ظهراً"
        hours = max(0, (nxt - now).total_seconds() / 3600)
        return (f"التقرير الشهري: "
                f"{nxt.strftime('%Y-%m-%d')} يوم 3 1 ظهراً — بعد {hours:.0f} ساعة")


# ── Helper ────────────────────────────────────────────────────
def _get_session_name(ksa_hour: int) -> str:
    if   1  <= ksa_hour < 5:   return "جلسة آسيا منتصف الليل"
    elif 5  <= ksa_hour < 9:   return "جلسة آسيا صباحاً"
    elif 9  <= ksa_hour < 13:  return "جلسة أوروبا صباحاً"
    elif 13 <= ksa_hour < 17:  return "جلسة أوروبا ذروة"
    elif 17 <= ksa_hour < 21:  return "جلسة أمريكا افتتاح"
    else:                       return "جلسة أمريكا مساءً"

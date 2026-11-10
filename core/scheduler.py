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

        # batch71 (مسحا 9 م و1 ص يوم 8–9 نوفمبر لم يصلا): نبضة الحلقة وحالة المسح للحارس و/scanstatus
        self.loop_beat_ts:      float = 0.0
        self.loop_restarts:     int   = 0
        self.scan_started_ts:   float = 0.0
        self.scan_finished_ts:  float = 0.0
        self.scan_ok_ts:        float = 0.0
        self.scan_session:      str   = ""
        self.scan_status:       str   = "لم يبدأ بعد"
        self.last_error:        str   = ""
        self.last_error_ts:     float = 0.0
        self.step_timeouts:     dict  = {}

    # ── التسجيل ──────────────────────────────────────────────

    async def _check_all_limit_orders(self):
        """يفحص جميع Limit Orders المعلقة لجميع المستخدمين."""
        try:
            if not hasattr(self, "_engine") or not self._engine:
                return
            engine = self._engine
            # إصلاح BUG-D: raed_engine يستخدم _user_exchanges وليس _live_users
            for user_id, info in getattr(engine, "_user_exchanges", {}).items():
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

    def _spawn(self, coro):
        """ينشئ مهمة خلفية مع الاحتفاظ بمرجع لها (لا يجمعها GC قبل انتهائها) ويسجّل أخطاءها."""
        if not hasattr(self, "_bg_tasks"):
            self._bg_tasks = set()
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)

        def _done(task):
            self._bg_tasks.discard(task)
            if not task.cancelled() and task.exception():
                logger.error(f"Scheduler background task: {task.exception()}")
        t.add_done_callback(_done)
        return t

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
    async def _step(self, name: str, coro, limit: float):
        """batch71: كل فحص داخل الحلقة بحد زمني — فحص عالق (طلب لا يعود أو قفل لا يُحرَّر) لا يوقف المسوح."""
        try:
            await asyncio.wait_for(coro, timeout=limit)
        except asyncio.TimeoutError:
            self.step_timeouts[name] = self.step_timeouts.get(name, 0) + 1
            self._note_error(f"{name}: تجاوز {int(limit)} ثانية فأُلغي")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._note_error(f"{name}: خطأ غير متوقع (التفاصيل في السجل)")
            logger.error(f"Scheduler {name}: {e}")

    def _note_error(self, txt: str) -> None:
        self.last_error = txt
        self.last_error_ts = time.time()
        logger.warning(f"Scheduler: {txt}")

    async def _loop(self):
        tick = 0
        while self._running:
            self.loop_beat_ts = time.time()
            try:
                now = datetime.now(timezone.utc)
                await self._step("check_scan", self._check_scan(now), 20)
                await self._step("weekly", self._check_weekly(now), 20)
                await self._step("monthly", self._check_monthly(now), 20)
                await self._step("coins_update", self._check_coins_update(now), 60)
                await self._step("month_end", self._check_month_end_review(now), 20)
                # فحص Limit Orders كل 30 ثانية — بحد دقيقة (مربوط بـ OKX)
                if tick % 1 == 0:
                    await self._step("limit_orders", self._check_all_limit_orders(), 60)
                tick += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(30)

    def ensure_loop(self) -> bool:
        """batch71: يعيد تشغيل الحلقة إن ماتت أو توقفت نبضتها أكثر من 5 دقائق. True إن أُعيدت."""
        if not self._running:
            return False
        dead = self._task is None or self._task.done()
        stale = self.loop_beat_ts and (time.time() - self.loop_beat_ts > 300)
        if not (dead or stale):
            return False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._loop())
        self.loop_restarts += 1
        self._note_error("حلقة المجدول توقفت فأُعيد تشغيلها" + (" (مهمة منتهية)" if dead else " (نبضة متوقفة)"))
        return True

    def last_due_scan_ts(self, now_ts: float | None = None) -> float:
        """آخر موعد مسح مستحق (بداية الساعة) حتى الآن."""
        now = datetime.fromtimestamp(now_ts or time.time(), timezone.utc)
        for back in range(0, 25):
            c = (now - timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
            if c.hour in SCAN_HOURS_UTC and c <= now:
                return c.timestamp()
        return 0.0


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
        self._spawn(self._send_monthly_review())

    async def _send_monthly_review(self):
        """يُرسل تقرير التعلم الشهري لكل مستخدم."""
        try:
            engine = getattr(self, "_engine", None)
            if not engine: return
            # إصلاح BUG-D: raed_engine يستخدم _user_exchanges وليس _live_users
            for user_id, info in getattr(engine, "_user_exchanges", {}).items():
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
            # إصلاح BUG-C: self._engine وليس self.engine
            if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "session") and self._engine.session:
                ok = await update_coins_list_from_api(self._engine.session)
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
        self._spawn(self._run_scan(now))

    SCAN_TIMEOUT_SEC = 25 * 60

    async def _run_scan(self, now: datetime):
        ksa_hour = (now.hour + 3) % 24
        session  = _get_session_name(ksa_hour)
        logger.info(f"🔍 مسح {session} — {ksa_hour:02d}:00 KSA")
        self.scan_started_ts = time.time()
        self.scan_session = f"{session} ({ksa_hour:02d}:00 KSA)"
        self.scan_status = "جارٍ"
        try:
            if self._scan_fn:
                # batch71: حد زمني للمسح كله — لا مسح عالق بلا نهاية
                report = await asyncio.wait_for(self._scan_fn(session=session, ksa_hour=ksa_hour),
                                                timeout=self.SCAN_TIMEOUT_SEC)
                if report:
                    await self._safe_send(report)
                    logger.info(f"✅ تقرير المسح أُرسل — {session}")
                self.scan_ok_ts = time.time()
                self.scan_status = "اكتمل وأُرسل" if report else "اكتمل بلا تقرير"
        except asyncio.TimeoutError:
            self.scan_status = f"تجاوز {self.SCAN_TIMEOUT_SEC // 60} دقيقة فأُلغي"
            self._note_error(f"مسح {self.scan_session}: تجاوز {self.SCAN_TIMEOUT_SEC // 60} دقيقة")
        except Exception as e:
            self.scan_status = "فشل"
            self._note_error(f"مسح {self.scan_session}: خطأ غير متوقع (التفاصيل في السجل)")
            logger.error(f"Scan error ({session}): {e}")
        finally:
            self.scan_finished_ts = time.time()

    def status_text_ar(self) -> str:
        """batch71: /scanstatus (للمالك)."""
        def _t(ts):
            if not ts:
                return "—"
            return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=3)).strftime("%m-%d %H:%M KSA")
        now = time.time()
        beat = (f"قبل {int(now - self.loop_beat_ts)} ثانية" if self.loop_beat_ts else "—")
        dur = (f"{(self.scan_finished_ts - self.scan_started_ts) / 60:.1f} دقيقة"
               if self.scan_finished_ts >= self.scan_started_ts > 0 else
               (f"جارٍ منذ {(now - self.scan_started_ts) / 60:.1f} دقيقة" if self.scan_started_ts else "—"))
        L = ["🛰️ حالة المسح", "━━━━━━━━━━━━━━━━━━",
             f"• نبضة المجدول: {beat}" + (f" | أُعيد تشغيلها {self.loop_restarts} مرة" if self.loop_restarts else ""),
             f"• آخر مسح: {self.scan_session or '—'} — {self.scan_status}",
             f"• بدأ: {_t(self.scan_started_ts)} | المدة: {dur}",
             f"• آخر مسح ناجح: {_t(self.scan_ok_ts)}",
             f"• آخر خطأ: {self.last_error or 'لا يوجد'}" + (f" ({_t(self.last_error_ts)})" if self.last_error else ""),
             ]
        if self.step_timeouts:
            L.append("• فحوص أُلغيت لتجاوز الوقت: " + "، ".join(f"{k} ×{v}" for k, v in self.step_timeouts.items()))
        L.append(self.next_scan_ar())
        return "\n".join(L)

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
        self._spawn(self._send_weekly())

    async def _check_monthly(self, now: datetime):
        if (now.day != MONTHLY_DAY
                or now.hour != REPORT_HOUR_UTC
                or now.minute > 1):
            return
        elapsed = time.time() - self._last_monthly_ts
        if elapsed < 86_400 * 25:   # 25 يوماً على الأقل
            return
        self._last_monthly_ts = time.time()
        self._spawn(self._send_monthly())

    async def _send_weekly(self):
        await self._send_period_reports("weekly")

    async def _send_monthly(self):
        await self._send_period_reports("monthly")

    async def _send_period_reports(self, period: str):
        """batch55 (#536–#553): تقرير شخصي لكل مشترك من صفقاته هو (افتراضية + حقيقية) للفترة نفسها،
        بصيغ /stats ذاتها. قسم «حالة النظام» (المخاطر/النموذج) للمالك فقط. المستلمون: من لديه محفظة
        افتراضية أو صفقات حقيقية أو حساب منصة، والمالك دائماً — ومن لا صفقات له يصله ذلك صراحة."""
        _name = "الأسبوعي" if period == "weekly" else "الشهري"
        try:
            import os as _os
            from core.state_manager import state_manager as _sm
            from core import period_report as _pr
            fn = self._weekly_report_fn if period == "weekly" else self._monthly_report_fn
            system_txt = ""
            if fn:
                try:
                    system_txt = (await fn()) or ""
                except Exception as _e:
                    logger.debug(f"system report {period}: {_e}")
            r = None
            try:
                r = _sm._redis if getattr(_sm, "_redis_ok", False) else None
            except Exception:
                r = None
            try:
                owner = int(_os.environ.get("OWNER_CHAT_ID", "") or 0) or None
            except ValueError:
                owner = None
            sent = 0
            for uid in _pr.eligible_user_ids(_sm, r, owner):
                try:
                    try:
                        has_ex = bool((_sm._get_user(uid) or {}).get("exchange"))
                    except Exception:
                        has_ex = False
                    try:
                        comments = _sm.get_user_comments(uid)
                    except Exception:
                        comments = []
                    text = _pr.build_user_report(uid, period, _sm.get_virtual_wallet(uid), r,
                                                 has_exchange=has_ex, comments=comments)
                    if owner and uid == owner and system_txt:
                        text += "\n\n" + system_txt
                    await self._notify_user(uid, text)
                    sent += 1
                    try:   # batch73 (G2): الملاحظات المعروضة لا تتكرر في التقرير التالي
                        import time as _t73
                        _sm.mark_comments_reported(uid, _t73.time())
                    except Exception:
                        pass
                except Exception as _e:
                    logger.debug(f"period report {period} user={uid}: {_e}")
            logger.info(f"📊 التقرير {_name} أُرسل لـ {sent} مستخدم")
        except Exception as e:
            logger.error(f"{_name} report error: {e}")

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
        from core.event_risk import _ar_count
        # batch68j: كان round() فيظهر «بعد 3 ساعات» بين 05:00 و09:00 حين يتأخر إرسال التقرير ≥30 دقيقة
        _mins = max(1, int((candidate - now).total_seconds() // 60))
        _h, _m = divmod(_mins, 60)
        if _m >= 55:   # دقائق قليلة بعد بدء المسح → ساعات كاملة
            _h, _m = _h + 1, 0
        if _h and _m >= 5:
            _left = f"{_ar_count(_h, 'ساعة', 'ساعتين', 'ساعات', 'ساعة')} و{_m} دقيقة"
        elif _h:
            _left = _ar_count(_h, 'ساعة', 'ساعتين', 'ساعات', 'ساعة')
        else:
            _left = f"{_m} دقيقة"
        return (f"⏰ المسح القادم: {ksa_h:02d}:00 KSA — "
                f"بعد {_left} — {_get_session_name(ksa_h)}")

    def next_weekly_ar(self) -> str:
        now  = datetime.now(timezone.utc)
        days = (WEEKLY_WEEKDAY - now.weekday()) % 7 or 7
        nxt  = (now + timedelta(days=days)).replace(
            hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
        hours = max(0, (nxt - now).total_seconds() / 3600)
        # الصيغة بالأيام والساعات (كان «بعد 169 ساعة») والمنطقة الزمنية مذكورة صراحةً
        from core.event_risk import _ar_count
        _h = int(round(hours))
        if _h >= 48:
            _d, _r = divmod(_h, 24)
            _after_w = "بعد " + _ar_count(_d, "يوم", "يومين", "أيام", "يوماً") + (
                " و" + _ar_count(_r, "ساعة", "ساعتين", "ساعات", "ساعة") if _r else "")
        else:
            _after_w = f"بعد {_h} ساعة"
        return (f"التقرير الأسبوعي: "
                f"{nxt.strftime('%Y-%m-%d')} الاثنين 1 ظهراً (بتوقيت السعودية) — {_after_w}")

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
        _d, _r = divmod(int(round(hours)), 24)
        _after = (("بعد يومين" if _d == 2 else f"بعد {_d} {'أيام' if 3 <= _d <= 10 else 'يوماً'}") + (f" و{_r} ساعة" if _r else "")) if hours >= 48 else f"بعد {hours:.0f} ساعة"
        return (f"التقرير الشهري: "
                f"{nxt.strftime('%Y-%m-%d')} يوم 3 1 ظهراً — {_after}")


# ── Helper ────────────────────────────────────────────────────
def _get_session_name(ksa_hour: int) -> str:
    if   1  <= ksa_hour < 5:   return "جلسة آسيا منتصف الليل"
    elif 5  <= ksa_hour < 9:   return "جلسة آسيا صباحاً"
    elif 9  <= ksa_hour < 13:  return "جلسة أوروبا صباحاً"
    elif 13 <= ksa_hour < 17:  return "ذروة جلسة أوروبا"
    elif 17 <= ksa_hour < 21:  return "افتتاح جلسة أمريكا"
    else:                       return "جلسة أمريكا مساءً"

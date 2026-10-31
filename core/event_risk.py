"""
📅 رائد — Event Risk Filter
يرصد الأحداث الماكرو والتنظيمية القادمة ويخفف التعرض تلقائياً.
FOMC · CPI · NFP · SEC · Halving · Expiry
"""

import time
import logging
import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class EventSeverity(Enum):
    LOW      = "low"       # تأثير محدود
    MEDIUM   = "medium"    # تأثير متوسط — تقليل 30%
    HIGH     = "high"      # تأثير كبير — تقليل 60%
    CRITICAL = "critical"  # صدمة محتملة — تجنب كامل


SEVERITY_AR = {
    EventSeverity.LOW:      "🟡 تأثير محدود",
    EventSeverity.MEDIUM:   "🟠 تأثير متوسط",
    EventSeverity.HIGH:     "🔴 تأثير كبير",
    EventSeverity.CRITICAL: "⛔ صدمة محتملة",
}

SEVERITY_EXPOSURE = {
    EventSeverity.LOW:      0.80,   # تقليل 20%
    EventSeverity.MEDIUM:   0.50,   # تقليل 50%
    EventSeverity.HIGH:     0.25,   # تقليل 75%
    EventSeverity.CRITICAL: 0.00,   # لا تداول
}


@dataclass
class MarketEvent:
    name:        str
    name_ar:     str
    severity:    EventSeverity
    event_time:  float          # Unix timestamp
    hours_before: int = 4       # ساعات قبل الحدث تبدأ القيود
    hours_after:  int = 2       # ساعات بعد الحدث تنتهي القيود
    crypto_impact: float = 0.5  # 0–1 درجة تأثير على الكريبتو
    source:      str = "manual"
    # event_scope_fix: "all" = حدث ماكرو يؤثر على كل الأصول؛ "crypto" = خاص
    # بسوق الكريبتو (مثل انتهاء خيارات BTC) فلا يُعرَض في خطة أصول مُرمَّزة فقط.
    scope:       str = "all"


@dataclass
class EventRiskState:
    active_events:       List[MarketEvent]
    max_severity:        Optional[EventSeverity]
    exposure_multiplier: float    # 0–1 (1=طبيعي)
    trading_allowed:     bool
    next_clear_time:     float    # متى تعود الأوضاع طبيعية
    message_ar:          str


# ── تقويم الأحداث الثابتة (يُحدَّث يدوياً + جلب تلقائي) ──────────────────────
def _get_recurring_events() -> List[MarketEvent]:
    """
    أحداث دورية ثابتة — تُضاف تلقائياً بناء على التقويم.
    يُمكن تحديثها من investing.com economic calendar.
    """
    now   = time.time()
    day   = 86_400
    week  = 7 * day
    month = 30 * day

    events = []

    # ── FOMC — جدول 2026 الرسمي (يوم القرار، 2:00م نيويورك = 18:00 UTC) ──
    # events_calendar_fix: كان الموعد يُحسَب "أقرب يوم خميس" دائماً (أي كل أسبوع!)
    # بينما اجتماعات FOMC ثمانية فقط في السنة. الآن: جدول فعلي لعام 2026؛
    # وإن انتهى الجدول لا يُعرَض موعد مُخمَّن (أفضل من موعد خاطئ).
    next_fomc = _next_fomc_time()
    if next_fomc:
        events.append(MarketEvent(
            name="FOMC Meeting", name_ar="اجتماع الفيدرالي الأمريكي",
            severity=EventSeverity.HIGH,
            event_time=next_fomc,
            hours_before=6, hours_after=3,
            crypto_impact=0.85,
        ))

    # ── CPI — موعد تقريبي (منتصف الشهر، أقرب أربعاء لليوم 13) ──────────
    # لا نملك جدول BLS الرسمي هنا؛ يُوسَم صراحةً "تقريبي" كي لا يُعرَض كموعد مؤكد.
    # official_calendar_fix (بند 13): مواعيد BLS الرسمية (08:30 نيويورك) من الجدول أدناه؛
    # التقريب يُستخدم فقط بعد انتهاء الجدول، ويبقى موسوماً «تقريبي».
    next_cpi = _next_official_time(_BLS_CPI_RELEASES)
    _cpi_official = next_cpi is not None
    if next_cpi is None:
        next_cpi = _approx_cpi_time()
    events.append(MarketEvent(
        name="US CPI Release",
        name_ar=("مؤشر التضخم الأمريكي CPI" if _cpi_official else "مؤشر التضخم الأمريكي CPI (موعد تقريبي)"),
        severity=EventSeverity.HIGH,
        event_time=next_cpi,
        hours_before=3, hours_after=2,
        crypto_impact=0.80,
    ))

    # ── NFP (أول جمعة من كل شهر، 8:30 نيويورك) ───────────────────────────
    next_nfp = _next_official_time(_BLS_NFP_RELEASES)
    if next_nfp is None:
        next_nfp = _first_weekday_of_month(4, hour_utc=13, minute=30)
    events.append(MarketEvent(
        name="Non-Farm Payrolls", name_ar="الوظائف غير الزراعية NFP",
        severity=EventSeverity.HIGH,  # batch51 (قرار رحال 2026-10-31): كان MEDIUM؛ يتساوى مع CPI/FOMC (الحجم 25% في النافذة، و0.7 قبل أسبوع)
        event_time=next_nfp,
        hours_before=2, hours_after=1,
        crypto_impact=0.60,
    ))

    # ── Bitcoin Options Expiry (آخر جمعة من الشهر، 08:00 UTC — Deribit) ──
    # كان from_end_of_month يُتجاهَل فيظهر "أقرب جمعة" (خلل موثَّق: 162 ساعة).
    next_expiry = _last_weekday_of_month(4, hour_utc=8)
    events.append(MarketEvent(
        name="BTC Options Expiry", name_ar="انتهاء عقود خيارات BTC",
        severity=EventSeverity.MEDIUM,
        event_time=next_expiry,
        hours_before=4, hours_after=2,
        crypto_impact=0.90,
        source="deribit",
        scope="crypto",
    ))

    return [e for e in events if e.event_time > now - 3600]


class EventRiskFilter:
    """
    يرصد الأحداث القادمة ويُقرر مستوى التعرض الآمن.
    يُدمج: أحداث دورية ثابتة + أحداث تنظيمية يدوية + كشف الأخبار.
    """

    def __init__(self):
        self._manual_events: List[MarketEvent] = []
        self._news_events:   List[MarketEvent] = []
        self._last_refresh   = 0.0
        self._refresh_interval = 3600   # ساعة

    # ═══════════════════════════════════════════════════════════
    # 1. تقييم مستوى الخطر الحالي
    # ═══════════════════════════════════════════════════════════
    def assess(self) -> EventRiskState:
        """يُعيد الحالة الكاملة لمخاطر الأحداث الآن."""
        all_events = (
            _get_recurring_events() +
            self._manual_events +
            self._news_events
        )

        now    = time.time()
        active = []

        for event in all_events:
            window_start = event.event_time - event.hours_before * 3600
            window_end   = event.event_time + event.hours_after  * 3600
            if window_start <= now <= window_end:
                active.append(event)

        # M#115: فحص الأحداث القادمة في 168 ساعة
        upcoming_high = [
            e for e in all_events
            if e.event_time > now
            and e.event_time - now <= 168 * 3600
            and e.severity in (EventSeverity.HIGH, EventSeverity.CRITICAL)
        ]

        if not active:
            if upcoming_high:
                # يوجد أحداث عالية المخاطر قادمة — لا نقول "لا أحداث"!
                soonest  = min(upcoming_high, key=lambda e: e.event_time)
                hrs_away = (soonest.event_time - now) / 3600
                return EventRiskState(
                    active_events=[],
                    max_severity=None,
                    exposure_multiplier=0.7,
                    trading_allowed=True,
                    next_clear_time=self._next_event_window(all_events, now),
                    message_ar=(
                        f"⚠️ حدث عالي المخاطر قادم خلال {hrs_away:.0f} ساعة:\n"
                        f"• {soonest.name_ar}\n"
                        f"💡 تقليل الحجم والمخاطرة حتى انتهاء الحدث"
                    ),
                )
            return EventRiskState(
                active_events=[],
                max_severity=None,
                exposure_multiplier=1.0,
                trading_allowed=True,
                next_clear_time=self._next_event_window(all_events, now),
                message_ar="✅ لا أحداث عالية المخاطر خلال الـ 7 أيام القادمة — التداول طبيعي",
            )

        # أشد حدث
        severity_order = [EventSeverity.LOW, EventSeverity.MEDIUM,
                          EventSeverity.HIGH, EventSeverity.CRITICAL]
        max_sev = max(active, key=lambda e: severity_order.index(e.severity)).severity

        multiplier = SEVERITY_EXPOSURE[max_sev]
        allowed    = multiplier > 0

        # أقرب وقت تنتهي فيه القيود
        clear_time = max(
            e.event_time + e.hours_after * 3600 for e in active
        )

        return EventRiskState(
            active_events=active,
            max_severity=max_sev,
            exposure_multiplier=multiplier,
            trading_allowed=allowed,
            next_clear_time=clear_time,
            message_ar=self._format_state_ar(active, max_sev, multiplier, clear_time),
        )

    # ═══════════════════════════════════════════════════════════
    # 2. فحص سريع للتداول
    # ═══════════════════════════════════════════════════════════
    def get_exposure_multiplier(self) -> Tuple[float, str]:
        """
        يُعيد (multiplier, reason) — الاستخدام الأسرع.
        """
        state = self.assess()
        if state.exposure_multiplier == 1.0:
            return 1.0, ""
        reason = f"حدث نشط: {state.active_events[0].name_ar}" if state.active_events else ""
        return state.exposure_multiplier, reason

    # ═══════════════════════════════════════════════════════════
    # 3. إضافة حدث يدوي (تنظيمي / أخبار)
    # ═══════════════════════════════════════════════════════════
    def add_manual_event(self, name: str, name_ar: str,
                          severity: str, hours_from_now: float,
                          hours_before: int = 2, hours_after: int = 1,
                          crypto_impact: float = 0.7):
        sev_map = {
            "low": EventSeverity.LOW, "medium": EventSeverity.MEDIUM,
            "high": EventSeverity.HIGH, "critical": EventSeverity.CRITICAL,
        }
        sev = sev_map.get(severity.lower(), EventSeverity.MEDIUM)
        event = MarketEvent(
            name=name, name_ar=name_ar,
            severity=sev,
            event_time=time.time() + hours_from_now * 3600,
            hours_before=hours_before,
            hours_after=hours_after,
            crypto_impact=crypto_impact,
            source="manual",
        )
        self._manual_events.append(event)
        logger.info(f"📅 حدث مضاف: {name_ar} — {sev.value}")

    # ═══════════════════════════════════════════════════════════
    # 4. كشف تلقائي من الأخبار
    # ═══════════════════════════════════════════════════════════
    def ingest_news_events(self, news_items: List[Dict]):
        """
        يُحلل الأخبار ويكشف الأحداث الماكرو الحقيقية فقط.
        الأخبار العادية (مهما كانت) لا تُوقف التداول.
        فقط الأحداث الحرجة الحقيقية (اختراق, حظر, انهيار) تؤثر.
        """
        # CRITICAL فقط: أحداث تُهدد السوق فعلاً
        keywords_critical = [
            "exchange hack", "exchange hacked", "massive hack",
            "trading suspended", "exchange shutdown", "crypto ban",
            "sec lawsuit", "doj arrest", "market crash",
            "flash crash", "rug pull",
        ]
        # HIGH: أحداث ماكرو مُثبَّتة بكلمات سياق واضحة
        keywords_high = [
            "fomc meeting today", "fed rate decision",
            "cpi data today", "inflation report",
            "bitcoin etf approval", "btc etf rejected",
        ]
        # لا نُصنِّف الأخبار العادية — ETF loss, price drop, etc. = معلومات عادية

        new_events = []
        for item in news_items:
            title = item.get("title", "").lower()
            sev   = None

            # نتحقق من الكلمات الحرجة كاملةً (لا كلمة واحدة منقطعة)
            if any(k in title for k in keywords_critical):
                sev = EventSeverity.HIGH   # أقصى ما نسمح به من الأخبار = HIGH
            elif any(k in title for k in keywords_high):
                sev = EventSeverity.MEDIUM

            if sev:
                event = MarketEvent(
                    name=item.get("title", "News Event")[:80],
                    name_ar=item.get("title", "خبر مهم")[:80],
                    severity=sev,
                    event_time=time.time(),
                    hours_before=0,
                    hours_after=1,   # ساعة واحدة فقط
                    crypto_impact=0.6,
                    source="news_auto",
                )
                new_events.append(event)

        # لا نُفعِّل الحدث إلا إذا وجدنا أحداثاً حقيقية
        self._news_events = new_events
        if new_events:
            logger.info(f"📰 أحداث من الأخبار: {len(new_events)}")

    # ═══════════════════════════════════════════════════════════
    # 5. قائمة الأحداث القادمة
    # ═══════════════════════════════════════════════════════════
    def upcoming_events(self, hours_ahead: int = 48,
                        asset_scope: str = "all") -> List[MarketEvent]:
        """قائمة الأحداث المتوقعة في الـ N ساعة القادمة.
        asset_scope="tokenized" يستبعد الأحداث الخاصة بالكريبتو فقط."""
        all_events = (
            _get_recurring_events() +
            self._manual_events +
            self._news_events
        )
        now    = time.time()
        cutoff = now + hours_ahead * 3600
        return [e for e in all_events
                if now <= e.event_time <= cutoff
                and not (asset_scope == "tokenized" and getattr(e, "scope", "all") == "crypto")]

    # ═══════════════════════════════════════════════════════════
    # 6. تنسيق التقارير
    # ═══════════════════════════════════════════════════════════
    def _format_state_ar(self, active: List[MarketEvent],
                          severity: EventSeverity,
                          multiplier: float, clear_time: float) -> str:
        remaining = max(0, clear_time - time.time()) / 60
        lines = [
            f"📅 *تحذير أحداث — Event Risk Filter*",
            f"━━━━━━━━━━━━━━━━━━",
            f"{SEVERITY_AR[severity]}",
            f"تخفيض التعرض إلى: {multiplier:.0%}",
            f"الأحداث النشطة:",
        ]
        for e in active:
            lines.append(f"• {e.name_ar} ({SEVERITY_AR[e.severity]})")
        lines += [
            f"",
            f"⏰ تعود الأوضاع طبيعية بعد: {remaining:.0f} دقيقة",
        ]
        return "\n".join(lines)

    def format_upcoming_ar(self, hours: int = 168, asset_scope: str = "all") -> str:
        events = self.upcoming_events(hours, asset_scope=asset_scope)
        if not events:
            return f"✅ لا أحداث مهمة خلال الـ {hours//24} أيام القادمة ({hours} ساعة)"

        lines = [f"📅 *الأحداث القادمة — {hours} ساعة*", "━━━━━━━━━━━━━━━━━━"]
        for e in sorted(events, key=lambda x: x.event_time):
            in_hours = (e.event_time - time.time()) / 3600
            sign = "⏰" if in_hours > 6 else "⚠️"
            # events_hours_rounding_fix: هذا السطر كان يُقرِّب بالبتر الدائم
            # (int() يحذف الكسر دائماً نحو الأسفل)، بينما تحذير "حدث عالي
            # المخاطر قادم خلال..." في assess() (أعلى في نفس الرسالة) يُقرِّب
            # للأقرب (.0f). لنفس القيمة الدقيقة (مثال: 160.6 ساعة)، هذا ينتج
            # حتماً فارق ساعة كاملة بين القسمين (161 مقابل 160) — ليس بسبب
            # فارق توقيت بين استدعاءين منفصلين لـtime.time()، بل بسبب طريقتي
            # تقريب مختلفتين تماماً لنفس نوع القيمة. موثَّق فعلياً: "اجتماع
            # الفيدرالي... خلال 161 ساعة" ثم "...بعد 160 ساعة" لنفس الحدث في
            # نفس الرسالة. الإصلاح: نفس طريقة التقريب (للأقرب) في كلا القسمين.
            # events_days_fmt_fix (جولة 35): الأحداث الشهرية بالساعات فقط (253 / 585 ساعة)
            # يصعب قراءتها؛ ≥48 ساعة تُعرض بالأيام مع التاريخ.
            _when = _fmt_after_ar(in_hours, e.event_time)
            lines.append(
                f"{sign} {e.name_ar} — {_when} ({SEVERITY_AR[e.severity]})"
            )
            recommendation = _event_recommendation(e.severity, int(in_hours))
            if recommendation:
                lines.append(f"  💡 {recommendation}")
        return "\n".join(lines)

    def _next_event_window(self, events: List[MarketEvent], now: float) -> float:
        future = [e.event_time - e.hours_before * 3600
                  for e in events if e.event_time > now]
        return min(future) if future else now + 86400


# ── Helpers ────────────────────────────────────────────────────────────────────
def _ar_count(n: int, one: str, two: str, few: str, many: str) -> str:
    """عدد + معدود بقواعد العربية: 1 يوم، 2 يومان/يومين، 3-10 أيام، 11+ يوماً."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {few}"
    return f"{n} {many}"


def _fmt_after_ar(in_hours: float, event_ts: float = 0.0) -> str:
    """'بعد N ساعة' لأقل من 48 ساعة، وإلا 'بعد D يوماً (YYYY-MM-DD)'."""
    h = max(0, round(in_hours))
    if h < 48:
        return "بعد " + (_ar_count(h, "ساعة", "ساعتين", "ساعات", "ساعة") if h else "0 ساعة")
    d, r = divmod(h, 24)
    txt = "بعد " + _ar_count(d, "يوم", "يومين", "أيام", "يوماً")
    if r:
        txt += " و" + _ar_count(r, "ساعة", "ساعتين", "ساعات", "ساعة")
    try:
        if event_ts:
            import time as _t
            txt += " — " + _t.strftime("%Y-%m-%d", _t.gmtime(event_ts))
    except Exception:
        pass
    return txt


# جدول قرارات FOMC لعام 2026 (اليوم الثاني من الاجتماع) — 18:00 UTC
_FOMC_2026_DECISIONS = [
    (2026, 1, 28), (2026, 3, 18), (2026, 4, 29), (2026, 6, 17),
    (2026, 7, 29), (2026, 9, 16), (2026, 10, 28), (2026, 12, 9),
]


# جداول BLS الرسمية (مصدر: bls.gov/schedule/news_release — الإصدار 08:30 بتوقيت نيويورك).
# تُحوَّل إلى UTC تلقائياً مع مراعاة التوقيت الصيفي. تُحدَّث سنوياً عند نشر جدول جديد.
_BLS_CPI_RELEASES = [
    (2026, 11, 10), (2026, 12, 10),
    (2027, 1, 13), (2027, 2, 11), (2027, 3, 10), (2027, 4, 13), (2027, 5, 12), (2027, 6, 10),
    (2027, 7, 14), (2027, 8, 11), (2027, 9, 14), (2027, 10, 14), (2027, 11, 10), (2027, 12, 10),
]
_BLS_NFP_RELEASES = [
    (2026, 11, 6), (2026, 12, 4),
    (2027, 1, 8), (2027, 2, 5), (2027, 3, 5), (2027, 4, 2), (2027, 5, 7), (2027, 6, 4),
    (2027, 7, 2), (2027, 8, 6), (2027, 9, 3), (2027, 10, 8), (2027, 11, 5), (2027, 12, 3),
]


def _ny_0830_to_utc_ts(y: int, m: int, d: int) -> float:
    """08:30 بتوقيت نيويورك (يراعي DST) → Unix timestamp."""
    import datetime
    from zoneinfo import ZoneInfo
    dt = datetime.datetime(y, m, d, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    return dt.timestamp()


def _next_official_time(table) -> Optional[float]:
    """أول موعد رسمي في الجدول لم يَحِن بعد (أو None إن انتهى الجدول)."""
    now = time.time()
    for y, m, d in table:
        ts = _ny_0830_to_utc_ts(y, m, d)
        if ts > now - 3600 and ts > now:
            return ts
    return None


def _utc_now():
    import datetime
    return datetime.datetime.utcnow()


def _to_ts(dt) -> float:
    import datetime
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def _next_fomc_time() -> Optional[float]:
    """أقرب قرار FOMC قادم: 14:00 بتوقيت نيويورك (يراعي DST: 18:00 UTC صيفاً / 19:00 شتاءً)."""
    import datetime
    from zoneinfo import ZoneInfo
    now = time.time()
    for y, m, d in _FOMC_2026_DECISIONS:
        ts = datetime.datetime(y, m, d, 14, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
        if ts > now:
            return ts
    return None


def _first_weekday_of_month(weekday: int, hour_utc: int = 12, minute: int = 0) -> float:
    """أقرب "أول <weekday>" في شهر قادم (أو الحالي إن لم يَحِن بعد)."""
    import datetime
    now = _utc_now()
    y, m = now.year, now.month
    for _ in range(3):
        first = datetime.datetime(y, m, 1, hour_utc, minute)
        first += datetime.timedelta(days=(weekday - first.weekday()) % 7)
        if first > now:
            return _to_ts(first)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return _to_ts(first)


def _last_weekday_of_month(weekday: int, hour_utc: int = 8, minute: int = 0) -> float:
    """أقرب "آخر <weekday>" في الشهر (الحالي إن لم يَحِن، وإلا القادم)."""
    import datetime
    now = _utc_now()
    y, m = now.year, now.month
    for _ in range(3):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = datetime.datetime(ny, nm, 1, hour_utc, minute) - datetime.timedelta(days=1)
        last -= datetime.timedelta(days=(last.weekday() - weekday) % 7)
        if last > now:
            return _to_ts(last)
        y, m = ny, nm
    return _to_ts(last)


def _approx_cpi_time() -> float:
    """موعد تقريبي لـ CPI: أقرب أربعاء لليوم 13 من الشهر (13:30 UTC)."""
    import datetime
    now = _utc_now()
    y, m = now.year, now.month
    for _ in range(3):
        base = datetime.datetime(y, m, 13, 13, 30)
        delta = (2 - base.weekday())
        if delta > 3:
            delta -= 7
        if delta < -3:
            delta += 7
        dt = base + datetime.timedelta(days=delta)
        if dt > now:
            return _to_ts(dt)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return _to_ts(dt)

def _next_occurrence_of_weekday(weekday: int, hour_utc: int = 12,
                                  skip_weeks: int = 0,
                                  from_end_of_month: bool = False) -> float:
    """
    weekday: 0=Mon … 6=Sun
    يُعيد Unix timestamp لأقرب تكرار قادم.
    """
    import datetime
    now  = datetime.datetime.utcnow()
    days_ahead = weekday - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    days_ahead += skip_weeks * 7

    target = now + datetime.timedelta(days=days_ahead)
    target = target.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    return target.timestamp()


def _event_recommendation(severity: EventSeverity, in_hours: float) -> str:
    """توصية إجرائية حسب شدة الحدث والوقت المتبقي."""
    if in_hours <= 2:
        if severity in (EventSeverity.HIGH, EventSeverity.CRITICAL):
            return "أغلق المراكز المفتوحة الآن"
        return "لا تفتح مراكز جديدة"
    elif in_hours <= 6:
        if severity == EventSeverity.HIGH:
            return "قلّل حجم المراكز 50% قبل الحدث"
        elif severity == EventSeverity.MEDIUM:
            return "قلّل حجم المراكز 30% قبل الحدث"
    elif in_hours <= 24:
        if severity == EventSeverity.HIGH:
            return "استعد بتقليل التعرض قبل 6 ساعات من الحدث"
    return ""


# Singleton
event_risk_filter = EventRiskFilter()

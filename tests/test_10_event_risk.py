"""نافذة الأحداث الكبيرة: سقف 70% قبلها، 50% داخلها (قبل الصدور وبعده حتى نهاية النافذة)، 100% بعدها — الدفعتان 66 و73."""
from _util import chk, src
import datetime
from unittest import mock
import core.event_risk as er


def mult_at(y, mo, d, hh, mm):
    t = datetime.datetime(y, mo, d, hh, mm, tzinfo=datetime.timezone.utc).timestamp()
    with mock.patch.object(er.time, "time", return_value=t):
        return er.EventRiskFilter().get_exposure_multiplier()[0]


def text_at(y, mo, d, hh, mm, hours):
    t = datetime.datetime(y, mo, d, hh, mm, tzinfo=datetime.timezone.utc).timestamp()
    with mock.patch.object(er.time, "time", return_value=t):
        return er.EventRiskFilter().format_upcoming_ar(hours)


# CPI 10 نوفمبر 2026 13:30 UTC (16:30 KSA) — نافذته 3 ساعات قبل/ساعتان بعد
chk("K1 CPI قبل النافذة 70%", mult_at(2026, 11, 10, 10, 10) == 0.7)
chk("K2 داخل النافذة قبل الصدور 50%", mult_at(2026, 11, 10, 11, 0) == 0.5)
chk("K3 بعد الصدور بـ10 د 50%", mult_at(2026, 11, 10, 13, 40) == 0.5)
chk("K4 بعد الصدور بـ40 د 50% (مسح 17:10 KSA)", mult_at(2026, 11, 10, 14, 10) == 0.5)
chk("K5 قبل نهاية النافذة 50%", mult_at(2026, 11, 10, 15, 25) == 0.5)
chk("K6 بعد نهاية النافذة 100%", mult_at(2026, 11, 10, 15, 35) == 1.0)
chk("K7 NFP 4 ديسمبر بعد الصدور بنصف ساعة 50%", mult_at(2026, 12, 4, 14, 0) == 0.5)
chk("K8 بعد نافذة NFP: 70% (FOMC 9 ديسمبر خلال 7 أيام)", mult_at(2026, 12, 4, 15, 5) == 0.7)
chk("K9 يوم هادئ 100%", mult_at(2026, 11, 12, 6, 0) == 1.0)
up = text_at(2026, 11, 10, 3, 40, 168)
chk("K10 نص نافذة CPI من إعداده", "من 3 ساعات قبله حتى ساعتين بعده" in up and "من ساعتين قبله حتى ساعة بعده" not in up, up)
up2 = text_at(2026, 11, 10, 14, 10, 24 * 40)
chk("K11 الحدث الجاري ظاهر بنهاية نافذته", "صدر قبل 40 دقيقة — نافذة الحدث (سقف 50%) حتى 18:30" in up2, up2)
s = src("core/event_risk.py")
chk("K12 لا شرط الإسقاط لحظة الصدور", "ts > now - 3600 and ts > now" not in s.replace("كان `ts > now - 3600 and ts > now`", ""))
chk("K13 جدولا BLS مرتبان", er._BLS_CPI_RELEASES == sorted(er._BLS_CPI_RELEASES) and er._BLS_NFP_RELEASES == sorted(er._BLS_NFP_RELEASES))
chk("K14 جدول منتهٍ → None", er._next_official_time([(2020, 1, 1)]) is None)

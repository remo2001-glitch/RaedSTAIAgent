"""الدفعة 101 (رحال 2026-11-20 23:15 «١. ب ٢. نعم موافق»):
WW1 (ب) — /quicksignal في السوق الهابط (Futures) يعرض خطة Short نفسها التي في /signal، ووسم نوع الأمر من جهة الخطة.
VV1 — انتهاء مهلة إرسال رسالة ⇒ محاولة ثانية واحدة. عرض/تشغيل فقط — لا يمس منطق القرار."""
import asyncio
from _util import chk, src
import handlers.analysis as A
import main as M
from telegram.error import TimedOut, NetworkError, BadRequest, Forbidden, RetryAfter

L = A._qs_entry_label_b101
# حالة 23:14: خطة شراء (أهداف فوق الدخول) والسعر الحي دون الدخول — كانت «🔴 Limit Sell»
lbl = L(1709.08, 1610.52, 1862.28, True, False)
chk("WW1a خطة شراء والسعر دونها ⇒ مراقبة شراء بعد استعادة المستوى (لا Limit Sell)",
    "Limit Sell" not in lbl and "شراء فقط بعد استعادة $1,709.08" in lbl, lbl)
chk("WW1b خطة شراء تحت السعر ⇒ كما كانت", L(95, 100, 110, True, False) == " — ⚪ مستوى مراقبة (Limit Buy فقط عند تفعيل الإشارة)"
    and L(95, 100, 110, False, False) == " — 🟢 Limit Buy (انتظر تراجعاً للدخول)")
chk("WW1c خطة Short فوق السعر ⇒ Short عند المقاومة بعد شمعة رفض", "Short عند المقاومة بعد شمعة رفض" in L(1884.2, 1610.52, 1772.46, True, True))
chk("WW1d خطة بيع محلية فوق السعر ⇒ Limit Sell كما كانت", L(105, 100, 95, False, False) == " — 🔴 Limit Sell (انتظر ارتداداً للدخول)")
chk("WW1e خطة Short والسعر فوقها ⇒ مراقبة لا بيع", "Short فقط بعد كسر" in L(95, 100, 90, True, True))

s = src("handlers/analysis.py")
chk("WW1f المستويات المشتركة تحسب تقييم Short في Futures (نفس /signal)", 'tech_extra=({"bb_mkt": "Futures"} if futures else None)' in s
    and '"short": (meta.get("short_b68")' in s and "futures=_use_futures_qs)" in s)
chk("WW1g الشرط: Futures + ليست توصية شراء + سوق هابط أو بيع", "if _use_futures_qs and not _is_buy_dir101 else None" in s
    and "(is_bearish or _is_sell_dir)" in s)
chk("WW1h المستويات من تقييم Short نفسه", 'entry, sl = float(_qs_short101["entry"]), float(_qs_short101["sl"])' in s)
chk("WW1i سطر يحيل الحكم إلى /signal", "حكم التفعيل والتأكيدات في /signal" in s)

# ── VV1 ────────────────────────────────────────────────────────
R = M._vv1_retryable
chk("VV1a مهلة وانقطاع ⇒ إعادة؛ أخطاء الطلب ⇒ لا", R(TimedOut()) and R(NetworkError("x")) and not R(BadRequest("x"))
    and not R(Forbidden("x")) and not R(RetryAfter(3)) and not R(ValueError()))

CALLS = []


class _Base:
    def __init__(self, plan): self.plan = plan
    async def send_message(self, *a, **k):
        CALLS.append(1)
        x = self.plan.pop(0)
        if isinstance(x, BaseException):
            raise x
        return x
    async def get_updates(self, *a, **k): return []


M.VV1_RETRY_DELAY = 0
B = M._tt1_bot_class(_Base)
CALLS.clear()
chk("VV1b مهلة ثم نجاح ⇒ الرسالة تصل بمحاولة ثانية", asyncio.run(B([TimedOut(), "ok"]).send_message(1, "t")) == "ok" and len(CALLS) == 2)


def _err(plan):
    CALLS.clear()
    try:
        asyncio.run(B(plan).send_message(1, "t"))
        return "ok"
    except Exception as e:
        return type(e).__name__


chk("VV1c مهلتان ⇒ الخطأ يظهر (محاولة ثانية واحدة فقط)", _err([TimedOut(), TimedOut(), "ok"]) == "TimedOut" and len(CALLS) == 2)
chk("VV1d BadRequest ⇒ لا إعادة", _err([BadRequest("Can't parse entities"), "ok"]) == "BadRequest" and len(CALLS) == 1)

"""الدفعة 106 (رحال 2026-11-21 15:13 «نعم يمكنك تنفيذه»): AI1 — تنبيهات الحساب الحقيقي تُسجَّل «أُرسل» بعد نجاح الإرسال فقط،
وسطر سجل لكل تنبيه (أُرسل/محجوب/فشل)، وميل الهبوط يعود إلى 🔔 Short، وbroadcast_fn يعيد نتيجة الإرسال الفردي."""
import asyncio
import types
from _util import chk, src
import core.real_alert_b103 as RA


class _FakeSM:
    def __init__(self): self.kv = {}
    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = str(v)


FSM = _FakeSM()
RA._sm = lambda: FSM
RA.owner_uid = lambda: 777
eng = types.SimpleNamespace(get_user_exchange=lambda u: None)
CALLS = []
RES = [False, True]


async def _send(text, user_id=None, parse_mode=None):
    CALLS.append(text)
    return RES.pop(0)


n = {"sym": "BZ", "side": "short", "score": 0.73, "px": 62.0, "missing": "تأكيدات Short 1/4 (الحد الأدنى 2)"}
s1 = asyncio.run(RA.send_near_b103(eng, _send, [n]))
chk("AI1a فشل الإرسال ⇒ لا تسجيل «أُرسل»", s1 == 0 and not any("near:BZ" in k for k in FSM.kv), FSM.kv)
s2 = asyncio.run(RA.send_near_b103(eng, _send, [n]))
chk("AI1b المسح التالي يعيد المحاولة وينجح ثم يُسجَّل", s2 == 1 and len(CALLS) == 2 and any("near:BZ:short" in k for k in FSM.kv))
RES[:] = [True]
s3 = asyncio.run(RA.send_near_b103(eng, _send, [n]))
chk("AI1c بعد النجاح يُحجب 24 ساعة", s3 == 0 and len(CALLS) == 2)
FSM.kv.clear()
RES[:] = [False, True]
o = {"sym": "BZ", "side": "short", "px": 61.9, "sl": 66.0, "tp1": 58.0, "tp2": 56.0, "rr": 1.1, "conf": 0.7, "n_conf": 2, "size_frac": 0.05}
r1 = asyncio.run(RA.send_opp_b103(eng, _send, o))
r2 = asyncio.run(RA.send_opp_b103(eng, _send, o))
chk("AI1d (أ) الشيء نفسه: فشل ⇒ يُعاد في المرة التالية", r1 is False and r2 is True)
st = src("core/auto_trader_b68.py")
chk("AI1e ميل الهبوط في 🔔 Short لم يعد مُعلَّقاً", '\n                "conf": float(sh.get("bear_conf") or 0),   # AI1 (b106)' in st)
chk("AI1f سطر سجل للمرشحين في كل دورة", 'logger.info(f"AC1 b103 near: {len(_nr)} مرشح "' in st)
sr = src("core/real_alert_b103.py")
chk("AI1g سطر سجل لكل تنبيه: أُرسل/محجوب/فشل", sr.count(": أُرسل للمالك") == 2 and sr.count(": محجوب — أُرسل خلال") == 2
    and sr.count(": فشل الإرسال — ") == 2)
chk("AI1h broadcast_fn يعيد نتيجة الإرسال الفردي", "return await _send_one(int(target), message, parse_mode, reply_markup)" in src("main.py"))

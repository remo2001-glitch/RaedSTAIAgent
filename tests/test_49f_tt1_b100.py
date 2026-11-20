"""الدفعة 100 (رحال 2026-11-20 20:24 «نعم موافق»): TT1 — صمود الاتصال بتيليجرام عند البدء + حارس التجمّد.
تشغيل فقط — لا يمس منطق القرار."""
import asyncio
import types
from _util import chk, src
import main as M
from telegram.error import TimedOut, NetworkError, InvalidToken

s = src("main.py")

# ── (أ) المهل ─────────────────────────────────────────────────
chk("TT1a1 مهلة القراءة 30ث والاتصال 20ث", M.TT1_TIMEOUTS["read_timeout"] == 30.0 and M.TT1_TIMEOUTS["connect_timeout"] == 20.0)
chk("TT1a2 المهل للطلبات وللاستقبال معاً", "request=HTTPXRequest(connection_pool_size=256, **TT1_TIMEOUTS)" in s
    and "get_updates_request=HTTPXRequest(**TT1_TIMEOUTS)" in s)
chk("TT1a3 خطأ الشبكة يُعاد ورمز خاطئ لا", M._tt1_is_network_error(TimedOut()) and M._tt1_is_network_error(NetworkError("x"))
    and not M._tt1_is_network_error(InvalidToken()) and not M._tt1_is_network_error(ValueError()))

# ── (أ) حلقة المحاولة ─────────────────────────────────────────
SLEEPS = []


class _App:
    def __init__(self, plan):
        self.plan = plan

    def run_polling(self, **k):
        act = self.plan.pop(0)
        if isinstance(act, BaseException):
            raise act
        if act == "postinit-then-fail":
            M._TT1["loop"] = 1.0
            raise TimedOut()


def _run(plan, max_attempts=""):
    SLEEPS.clear()
    built = []
    o_build, o_sleep = M.build_app, M.time.sleep

    def _b():
        built.append(1)
        return _App(plan)
    M.build_app, M.time.sleep = _b, SLEEPS.append
    import os
    os.environ["TT1_MAX_ATTEMPTS"] = max_attempts
    try:
        M.main()
        return "ok", len(built)
    except Exception as e:
        return type(e).__name__, len(built)
    finally:
        M.build_app, M.time.sleep = o_build, o_sleep
        M._TT1["loop"] = 0.0


plan = [TimedOut(), TimedOut(), NetworkError("x"), TimedOut(), "ok"]
r = _run(plan)
chk("TT1b1 تأخر تيليجرام ⇒ يعيد المحاولة ثم يعمل (لا انهيار)", r == ("ok", 5), r)
chk("TT1b2 الانتظار 15ث يتضاعف حتى 60ث", SLEEPS == [15, 30, 60, 60], SLEEPS)
chk("TT1b3 رمز خاطئ ⇒ ينهار فوراً كما كان", _run([InvalidToken()]) == ("InvalidToken", 1))
chk("TT1b4 خطأ برمجي ⇒ ينهار فوراً", _run([ValueError("bug")]) == ("ValueError", 1))
chk("TT1b5 خطأ شبكة بعد بدء post_init ⇒ لا تكرار للمهام (ينهار)", _run(["postinit-then-fail"]) == ("TimedOut", 1))
chk("TT1b6 حلقة أحداث جديدة لكل محاولة", "asyncio.set_event_loop(asyncio.new_event_loop())" in s)

# ── (ب) الحارس ───────────────────────────────────────────────
EXITS = []
o_exit = M._tt1_freeze_exit
M._tt1_freeze_exit = lambda why, dump_tasks=False: (EXITS.append((why, dump_tasks)), M._TT1.__setitem__("stopping", True))


class _Base:
    async def get_updates(self, *a, **k):
        return []


B = M._tt1_bot_class(_Base)()
M._TT1["poll"] = 0.0
asyncio.run(B.get_updates())
chk("TT1c1 كل استقبال ناجح يسجّل الوقت", M._TT1["poll"] > 0)

M._TT1.update(poll=M.time.time() - 200, stopping=False)
asyncio.run(asyncio.wait_for(M._tt1_heartbeat(), 5))
chk("TT1c2 استقبال متوقف 3 دقائق والحلقة حيّة ⇒ تدخّل الحارس مع مكان التعليق",
    len(EXITS) == 1 and EXITS[0][1] and "لا استقبال" in EXITS[0][0], EXITS)

EXITS.clear()
M._TT1.update(poll=0.0, stopping=False)


async def _one():
    t = asyncio.create_task(M._tt1_heartbeat())
    await asyncio.sleep(0.05)
    M._TT1["stopping"] = True
    t.cancel()
asyncio.run(_one())
chk("TT1c3 قبل أول استقبال (البدء قد يطول) لا تدخّل", EXITS == [] and M._TT1["loop"] > 0)
M._tt1_freeze_exit = o_exit
chk("TT1c4 خيط مستقل يراقب الحلقة 180ث", M.TT1_STALL_SEC == 180 and "time.time() - lp > TT1_STALL_SEC" in s
    and 'name="tt1-watchdog", daemon=True' in s)
chk("TT1c5 الخروج يكتب مكان التعليق ثم os._exit", "faulthandler.dump_traceback(file=sys.stderr, all_threads=True)" in s
    and "os._exit(3)" in s)
chk("TT1c6 يبدأ أول post_init ويتوقف في post_shutdown", "    _tt1_start_heartbeat(app)   # TT1" in s
    and '    _TT1["stopping"] = True     # TT1' in s)

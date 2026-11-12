"""أدوات الاختبار المشتركة — تُشغَّل من جذر المستودع أو من مجلد tests (بلا pytest)."""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("BOT_TOKEN", "x")


def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " " + str(detail)[:600]))
    return bool(cond)


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()

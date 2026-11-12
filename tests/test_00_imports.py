"""كل الوحدات تُستورد وتُترجم بلا أخطاء."""
from _util import chk, ROOT
import os, py_compile, importlib, pkgutil

bad = []
for d, _, files in os.walk(ROOT):
    if any(x in d for x in (".git", "venv", "__pycache__")):
        continue
    for f in files:
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(d, f), doraise=True)
            except Exception as e:
                bad.append((f, str(e)[:120]))
chk("compile: كل ملفات .py", not bad, bad)

for pkg in ("core", "handlers", "security"):
    errs = []
    for m in pkgutil.iter_modules([os.path.join(ROOT, pkg)]):
        try:
            importlib.import_module(f"{pkg}.{m.name}")
        except Exception as e:
            errs.append((m.name, type(e).__name__, str(e)[:100]))
    chk(f"import: {pkg}", not errs, errs)
try:
    import main  # noqa
    chk("import: main", hasattr(main, "build_app") and hasattr(main, "cmd_start"))
except Exception as e:
    chk("import: main", False, e)

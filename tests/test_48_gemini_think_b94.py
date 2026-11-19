"""الدفعة 94 (رحال 2026-11-19 23:28 «نعم موافق»): LL1 مهلة قراءة الشريط ومستوى التفكير «low»، LL2 سطر الدقة من سعر Groq."""
import asyncio
import io
import json
import os
import urllib.error
import urllib.request
from _util import chk, src
from core.news import NewsEngine

os.environ["GEMINI_API_KEY"] = "k"
os.environ["GEMINI_VISION_MODEL"] = "gemini-3.8-flash"
ne = NewsEngine.__new__(NewsEngine)
NewsEngine._gemini_models_cache_b52 = None
NewsEngine._gemini_cooldown_b54 = {}
NewsEngine._gemini_no_thinking_b92 = set()
NewsEngine._gemini_think_level_b94 = {}
CALLS = []
OK = json.dumps({"candidates": [{"content": {"parts": [{"text": "SYMBOL: ANTUSDT\nPRICE: 98.82 " + "x" * 30}]}, "finishReason": "STOP"}]})


def fake(req, context=None, timeout=None):
    body = json.loads(req.data.decode())
    lvl = (body["generationConfig"].get("thinkingConfig") or {}).get("thinkingLevel")
    CALLS.append((lvl, timeout))
    if lvl == "minimal":
        raise urllib.error.HTTPError("u", 400, "e", {}, io.BytesIO(b'{"error":{"message":"Thinking level MINIMAL is not supported for this model."}}'))

    class R:
        def read(self): return OK.encode()
    return R()


urllib.request.urlopen = fake
run = lambda: asyncio.run(ne._gemini_vision_b52([b"i"], "s", "u", max_tokens=200, timeout=30, thinking_level="minimal",
                                                 budget=45.0, retry_5xx=False))
txt, st = run()
chk("LL1b رفض minimal ⇒ الإعادة بـ low (لا حذف الإعداد)", st == "ok" and [c[0] for c in CALLS] == ["minimal", "low"], CALLS)
chk("LL1b «low» يُتذكَّر", NewsEngine._gemini_think_level_b94.get("gemini-3.8-flash") == "low"
    and "gemini-3.8-flash" not in NewsEngine._gemini_no_thinking_b92)
CALLS.clear()
run()
chk("LL1b الطلب التالي يبدأ بـ low مباشرة", [c[0] for c in CALLS] == ["low"], CALLS)
chk("LL1a مهلة الطلب 30ث", all(c[1] == 30 for c in CALLS), CALLS)
s = src("core/news.py")
chk("LL1a قراءة الشريط وإعادتها 30ث / 45ث و35ث", "timeout=30,\n                thinking_level=\"minimal\", budget=45.0" in s
    and "timeout=30,\n                    thinking_level=\"minimal\", budget=35.0" in s)

import handlers.analysis as A
a = src("handlers/analysis.py")
chk("LL2 سعر القراءة الجزئية في سطر الدقة", 'or float(_img_px_b93 or 0),' in a and "_live_px_chart if not _px_mismatch_b93 else 0.0" in a)
chk("LL2 النص", A._chart_accuracy_line_px(98.82, 97.69).startswith("✅ دقة القراءة: آخر سعر في الصورة $98.82 قريب من السعر الحي (فرق 1.2%)")
    and A._chart_accuracy_line_px(98.82, 0) == "")

"""الدفعة 92 (رحال 2026-11-19 «نعم تفضل»): JJ1 (أ–هـ) قراءة الشريط عبر Gemini/Groq، JJ2 السيناريوان، JJ3 هدف الاختراق."""
import asyncio
import io
import json
import os
import time
import urllib.error
import urllib.request
from _util import chk, src
import handlers.analysis as A
import core.chart_vision as CV
from core.news import NewsEngine

os.environ["GEMINI_API_KEY"] = "k"
os.environ["GEMINI_VISION_MODEL"] = "gemini-3.6-flash,gemini-3.8-flash,gemini-3.7-flash"
ne = NewsEngine.__new__(NewsEngine)
NewsEngine._gemini_models_cache_b52 = None
NewsEngine._gemini_cooldown_b54 = {}
NewsEngine._gemini_no_thinking_b92 = set()

CALLS = []
OK = json.dumps({"candidates": [{"content": {"parts": [{"text": "SYMBOL: ANTUSDT\nMARKET: PERP\nPRICE: 95.98 " + "x" * 30}]},
                                 "finishReason": "STOP"}]})


def _err(code, body=""):
    return urllib.error.HTTPError("u", code, "e", {}, io.BytesIO(body.encode()))


class _R:
    def __init__(self, b): self.b = b
    def read(self): return self.b.encode()


def fake_urlopen(req, context=None, timeout=None):
    body = json.loads(req.data.decode())
    model = req.full_url.split("/models/")[1].split(":")[0]
    thk = "thinkingConfig" in body["generationConfig"]
    CALLS.append((model, thk, timeout))
    if model == "gemini-3.6-flash":
        raise _err(503)
    if model == "gemini-3.8-flash":
        if thk:
            raise _err(400, '{"error":{"message":"Thinking level MINIMAL is not supported for this model."}}')
        raise TimeoutError("The read operation timed out")
    return _R(OK)


urllib.request.urlopen = fake_urlopen
t0 = time.time()
txt, st = asyncio.run(ne._gemini_vision_b52([b"img"], "s", "u", max_tokens=200, timeout=12,
                                            thinking_level="minimal", budget=40.0, retry_5xx=False))
chk("JJ1b 503 بلا إعادة ⇒ التالي فوراً، ثم 3.7 ينجح ضمن السقف", st == "ok" and "ANTUSDT" in txt
    and [c[0] for c in CALLS].count("gemini-3.6-flash") == 1 and CALLS[-1][0] == "gemini-3.7-flash" and time.time() - t0 < 10, CALLS)
chk("JJ1a «thinking غير مدعوم» يُتذكَّر", "gemini-3.8-flash" in NewsEngine._gemini_no_thinking_b92)
chk("JJ1b مهلة الطلب ≤ 12ث", all(c[2] <= 12 for c in CALLS), CALLS)
CALLS.clear()
NewsEngine._gemini_models_cache_b52 = ["gemini-3.8-flash"]
NewsEngine._gemini_cooldown_b54 = {}
asyncio.run(ne._gemini_vision_b52([b"img"], "s", "u", max_tokens=200, timeout=12, thinking_level="minimal",
                                  budget=40.0, retry_5xx=False))
chk("JJ1a لا يُرسل thinkingConfig مجدداً للنموذج نفسه", CALLS and CALLS[0] == ("gemini-3.8-flash", False, CALLS[0][2]), CALLS)
chk("JJ1e سبب الفشل مقروء", NewsEngine._gemini_last_reason_b92 in ("", "انتهت مهلة Gemini", "ضغط عالٍ لدى Gemini"))
s = src("core/news.py")
chk("JJ1b قراءة الشريط: 12ث / 40ث / بلا إعادة 503", "timeout=12,\n                thinking_level=\"minimal\", budget=40.0, retry_5xx=False" in s)

# ── JJ1 (ج): احتياط Groq للشريط ────────────────────────────────────
chk("JJ1c المكتشف القديم المهمل حُذف والاحتياط موجود", "async def detect_market_type_from_image" not in s and "async def read_strip_groq_b92" in s
    and "return await self._strip_only_b92(image_data)" in s)
try:
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (1159, 2576), "black").save(b, format="JPEG")
    crop = CV.crop_top_strip_b92(b.getvalue())
    w, h = Image.open(io.BytesIO(crop)).size
    chk("JJ1c قصّ أعلى الشاشة مصغّراً", w == 800 and 240 <= h <= 260, (w, h))
except ImportError:
    chk("JJ1c (Pillow غير متاح — تخطٍّ)", True)
a = src("handlers/analysis.py")
chk("JJ1c القراءة الجزئية لا تُعامَل شريطاً كاملاً", '_strip_only_b92, _legend_b51 = True, {}' in a and "_leg_mkt_b91 = _leg_mkt_b92" in a
    and "قراءة جزئية للشريط العلوي (الرمز ونوع السوق فقط)" in a)
chk("JJ1d تعليمات البند 0 تذكر «العقود الدائمة»", "«العقود الدائمة» أو Perpetual أو Swap أو «عقود آجلة» = Futures/Perp" in s)

# ── JJ2/JJ3 ────────────────────────────────────────────────────
T = """5- التوصية (مشروطة): انتظار — الدخول فقط بعد إغلاق شمعة يومية فوق مستوى 117.80 (خط السوبر ترند) مع تأكيد الحجم، أو شراء عند ارتداد من الدعم 92.91 مع ظهور شمعة انعكاسية.
6- نقطة الدخول المقترحة بالسعر الدقيق (مشروطة بتأكيد): 92.91 (عند الدعم) أو 117.80 (عند الاختراق).
7- وقف الخسارة بالسعر الدقيق: 85.96 (تحت الحد السفلي لبولينجر).
8- هدف الربح الأول والثاني بالسعر الدقيق:
   - الهدف الأول: 117.80 (خط السوبر ترند).
   - الهدف الثاني: 130.80 (خط EMA70)."""
out = A._chart_rr_block_b53(T, "USDT", 95.98, (), leg={})[0]
chk("JJ2 سيناريوان منفصلان بلا شريط", "ℹ️ سيناريوان منفصلان: الدخول 92.91 ارتداد عند الدعم" in out and "تحت مستوى التفعيل" not in out, out)
chk("JJ3 هدف سيناريو الاختراق", "ℹ️ سيناريو الدخول البديل 117.8: لا هدف أول مستقل له — الهدف الأول 117.8 يخص سيناريو الدخول 92.91؛ أقرب هدف بعده 130.8" in out
    and "يساوي الهدف الأول" not in out, out)
T1 = T.replace(" أو شراء عند ارتداد من الدعم 92.91 مع ظهور شمعة انعكاسية", "").replace("92.91 (عند الدعم) أو 117.80 (عند الاختراق)", "92.91")
out1 = A._chart_rr_block_b53(T1, "USDT", 95.98, (), leg={})[0]
chk("JJ2b دخول وحيد تحت التفعيل يبقى تحذيراً", "تحت مستوى التفعيل 117.8" in out1, out1)
T2 = T.replace("6- نقطة الدخول المقترحة بالسعر الدقيق (مشروطة بتأكيد): 92.91 (عند الدعم) أو 117.80 (عند الاختراق).", "6- نقطة الدخول: 117.80")
chk("JJ3b دخول وحيد = الهدف الأول يبقى تحذيراً", "يساوي الهدف الأول" in A._chart_rr_block_b53(T2, "USDT", 95.98, (), leg={})[0])

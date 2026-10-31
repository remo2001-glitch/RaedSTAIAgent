"""
batch51 — قراءة الشارت على مرحلتين (دقة الأرقام + حقائق محسوبة برمجياً).

المرحلة 1: قصّ شريط المؤشرات العلوي ولوحات المؤشرات السفلية وتكبيرهما، ثم طلب «نسخ حرفي» للقيم (بلا تفسير).
المرحلة 2: التحليل الحر يستلم «حقائق مؤكدة» تُحسب هنا برمجياً (علاقة السعر بالمستويات، تقاطع MACD، حالة KDJ/RSI…)
           فلا يناقضها، وبعدها تُصحَّح الأرقام شبه المطابقة (رقم زائد/ناقص) لقيم الشارت الفعلية.
كل الدوال نقية (بلا شبكة) عدا الاعتماد الاختياري على Pillow للقص؛ أي فشل يعيد السلوك السابق دون كسر.
"""
from __future__ import annotations

import io
import re
from typing import Dict, List, Optional, Tuple

# مفاتيح مرحلة النسخ الحرفي
LEGEND_KEYS = (
    "TIMEFRAME", "PRICE", "CHANGE_24H", "VWAP", "BOLL_MID", "BOLL_UB", "BOLL_LB",
    "RESISTANCE", "SUPPORT", "SUPERTREND_GREEN", "SUPERTREND_RED", "SAR",
    "RSI_A", "RSI_B", "RSI_C", "MACD_DIFF", "MACD_DEA", "MACD_HIST",
    "KDJ_K", "KDJ_D", "KDJ_J", "VOL", "VOL_MA5",
)

_LEVEL_KEYS = ("VWAP", "BOLL_MID", "BOLL_UB", "BOLL_LB", "RESISTANCE", "SUPPORT",
               "SUPERTREND_GREEN", "SUPERTREND_RED", "SAR")


def crop_legend_images(image_bytes: bytes) -> List[bytes]:
    """يقصّ منطقتين مكبّرتين: (أعلى 0–40%: السعر وشريط المؤشرات) و(55–92%: لوحات RSI/MACD/KDJ).
    يعيد قائمة JPEG؛ فارغة إن غاب Pillow أو فشلت الصورة (فيعمل النظام كما كان)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = im.size
        if w < 300 or h < 600:
            return []
        out = []
        for a, b in ((0.0, 0.40), (0.55, 0.92)):
            part = im.crop((0, int(h * a), w, int(h * b)))
            scale = 1.6 if w < 1300 else 1.0
            if scale != 1.0:
                part = part.resize((int(part.width * scale), int(part.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            part.save(buf, format="JPEG", quality=90)
            out.append(buf.getvalue())
        return out
    except Exception:
        return []


def legend_prompt() -> str:
    keys = "\n".join(f"{k}: " for k in LEGEND_KEYS)
    return (
        "انسخ حرفياً القيم الظاهرة في الصور المقصوصة لشارت تداول، دون أي تفسير. "
        "اكتب كل مفتاح في سطر بالشكل KEY: value، وانسخ الأرقام بكل منازلها وأصفارها كما تظهر بالضبط. "
        "إن لم تظهر القيمة اكتب -- . المفاتيح:\n" + keys + "\n"
        "ملاحظات: TIMEFRAME هو الإطار المظلَّل (مثل 1W أو 1D أو 4H أو 15m). PRICE هو السعر الكبير الحالي. "
        "SUPERTREND_GREEN قيمة SUPERTREND بالأخضر وSUPERTREND_RED بالأحمر. "
        "RSI_A وRSI_B وRSI_C هي قيم RSI الثلاث بالترتيب الظاهر (مثل RSI6 وRSI12 وRSI24) بالشكل RSI6=37.39. "
        "MACD_HIST هي قيمة STICK. VOL هي قيمة VOL الأولى وVOL_MA5 هي MA5 في سطر الحجم. "
        "لا تضف أي نص آخر."
    )


_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _to_float(s: str) -> Optional[float]:
    try:
        m = _NUM.search(s or "")
        if not m:
            return None
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_legend(text: str) -> Dict[str, object]:
    """يحلّل ناتج المرحلة 1 إلى قاموس: قيم رقمية + النصوص الأصلية (raw) للمستويات (لحفظ تنسيق الأرقام)."""
    res: Dict[str, object] = {"raw": {}, "rsi": []}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().upper().replace(" ", "_")
        v = v.strip()
        if k not in LEGEND_KEYS or not v or v.startswith("--"):
            continue
        if k == "TIMEFRAME":
            res["TIMEFRAME"] = v.strip("[] ").upper()
            continue
        if k in ("RSI_A", "RSI_B", "RSI_C"):
            m = re.match(r"\s*(RSI\s*\d+)\s*[=:]?\s*([-+]?\d[\d.,]*)", v, re.I)
            if m:
                try:
                    res["rsi"].append((m.group(1).replace(" ", "").upper(), float(m.group(2).replace(",", ""))))
                except ValueError:
                    pass
            continue
        m = _NUM.search(v)
        if not m:
            continue
        raw = m.group(0).replace(",", "")
        f = _to_float(v)
        if f is None:
            continue
        if k in ("VOL", "VOL_MA5") and v.lower().rstrip().endswith("k"):
            f *= 1000.0
        res[k] = f
        res["raw"][k] = raw  # type: ignore[index]
    return res


def legend_is_usable(leg: Dict[str, object]) -> bool:
    n = sum(1 for k in _LEVEL_KEYS if k in leg)
    return n >= 3 or ("PRICE" in leg and n >= 1)


def _rel(price: float, level: float) -> str:
    return "فوق" if price > level else "تحت"


def build_facts(leg: Dict[str, object], ref_price: float = 0.0, quote_unit: str = "") -> str:
    """حقائق مؤكدة تُمرَّر للنموذج في المرحلة 2 — كلها محسوبة برمجياً من القيم المنسوخة."""
    if not leg:
        return ""
    price = float(ref_price or 0) or float(leg.get("PRICE", 0) or 0)
    unit = f" {quote_unit}" if quote_unit and quote_unit != "USDT" else ""
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}

    def fmt(k):
        return raw.get(k) or (f"{leg[k]:g}" if k in leg else "")

    lines: List[str] = []
    bull = bear = 0
    tf = leg.get("TIMEFRAME")
    if tf:
        lines.append(f"الإطار الزمني المحدد في الشارت: {tf}")
    names = {"VWAP": "VWAP", "BOLL_MID": "منتصف البولنجر", "BOLL_UB": "الحد العلوي للبولنجر",
             "BOLL_LB": "الحد السفلي للبولنجر", "RESISTANCE": "RESISTANCE (قمة النطاق المرسوم)",
             "SUPPORT": "SUPPORT"}
    for k, nm in names.items():
        if k in leg:
            line = f"{nm} = {fmt(k)}{unit}"
            if price > 0:
                d = (price / float(leg[k]) - 1) * 100 if float(leg[k]) else 0
                line += f" (السعر {_rel(price, float(leg[k]))} المستوى بنحو {abs(d):.1f}%)"
                if k in ("VWAP", "BOLL_MID"):
                    if price > float(leg[k]):
                        bull += 1
                    else:
                        bear += 1
            lines.append(line)
    for k, nm in (("SUPERTREND_GREEN", "SuperTrend الأخضر"), ("SUPERTREND_RED", "SuperTrend الأحمر"), ("SAR", "SAR")):
        if k in leg:
            v = float(leg[k])
            if price > 0:
                if v < price:
                    lines.append(f"{nm} = {fmt(k)}{unit} تحت السعر → إشارة صاعدة (دعم متحرك) — ليس مقاومة")
                    bull += 1
                else:
                    lines.append(f"{nm} = {fmt(k)}{unit} فوق السعر → إشارة هابطة (مقاومة متحركة) — ليس دعماً")
                    bear += 1
            else:
                lines.append(f"{nm} = {fmt(k)}{unit}")
    if "MACD_DIFF" in leg and "MACD_DEA" in leg:
        d, e = float(leg["MACD_DIFF"]), float(leg["MACD_DEA"])
        h = leg.get("MACD_HIST")
        if d > e:
            lines.append(f"MACD: DIFF {fmt('MACD_DIFF')} فوق DEA {fmt('MACD_DEA')} → زخم صاعد")
            bull += 1
        else:
            lines.append(f"MACD: DIFF {fmt('MACD_DIFF')} تحت DEA {fmt('MACD_DEA')} → زخم هابط")
            bear += 1
        if h is not None:
            lines[-1] += f" (STICK {'موجب' if float(h) > 0 else 'سالب'} {fmt('MACD_HIST')})"
    if "KDJ_K" in leg and "KDJ_D" in leg:
        k_, d_ = float(leg["KDJ_K"]), float(leg["KDJ_D"])
        j_ = leg.get("KDJ_J")
        s = f"KDJ: K {k_:.2f} {'فوق' if k_ > d_ else 'تحت'} D {d_:.2f}"
        if j_ is not None:
            s += f"، J {float(j_):.2f}"
            if float(j_) < 0 or float(j_) < 20 and k_ < 25:
                s += " → منطقة ذروة بيع (احتمال ارتداد) — لا تصفها «إشارة بيع» فقط"
            elif float(j_) > 100 or (float(j_) > 80 and k_ > 75):
                s += " → منطقة ذروة شراء"
        lines.append(s)
        if k_ > d_:
            bull += 1
        else:
            bear += 1
    for nm, v in (leg.get("rsi") or []):
        z = ("ذروة بيع (<30)" if v < 30 else "قرب ذروة البيع" if v < 35
             else "ذروة شراء (>70)" if v > 70 else "محايد")
        lines.append(f"{nm} = {v:.2f} ({z})")
    if "VOL" in leg and "VOL_MA5" in leg and float(leg["VOL_MA5"]) > 0:
        r = float(leg["VOL"]) / float(leg["VOL_MA5"])
        lines.append(f"حجم الشمعة الأخيرة {'أقل' if r < 1 else 'أعلى'} من متوسطه MA5 (نسبة {r:.2f}x) — MA5 هنا متوسط حجم لا سعر")
    if bull or bear:
        lines.append(f"ميزان المؤشرات المقروءة: {bull} صاعد مقابل {bear} هابط — اجعل وصف الاتجاه العام متسقاً معه "
                     f"(لا «هابط بقوة» إذا كان السعر فوق SuperTrend/دعم متحرك)")
    return "\n".join(lines)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fix_near_miss_levels(text: str, leg: Dict[str, object]) -> Tuple[str, int]:
    """يصحّح أرقاماً في نص التحليل تخالف قيمة منسوخة من الشارت برقم زائد/ناقص/مبدَّل (مسافة تحرير ≤ 2) وضمن 8% منها.
    لا يلمس رقماً يطابق أي قيمة فعلية، ولا يعدّل عند التباس (أكثر من مرشح متقارب). يعيد (النص، عدد التصحيحات)."""
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}
    cands = []
    # PRICE خارج المرشحين: أي رقم قريب من السعر الحالي قد يكون مستوى اقترحه التحليل عمداً لا خطأ قراءة
    for k in _LEVEL_KEYS:
        if k in raw and k in leg:
            cands.append((str(raw[k]), float(leg[k])))
    if len(cands) < 2:
        return text, 0
    exact = {round(v, 12) for _s, v in cands}
    count = 0

    def _sub(m):
        nonlocal count
        tok = m.group(0)
        if "." not in tok or len(tok.split(".")[1]) < 3:
            return tok
        try:
            val = float(tok.replace(",", ""))
        except ValueError:
            return tok
        if round(val, 12) in exact or val <= 0:
            return tok
        near = []
        for s, v in cands:
            if v > 0 and abs(val / v - 1) <= 0.08:
                _dt = _digits(tok.replace(",", ""))
                dist = _edit_distance(_dt, _digits(s))
                # أخطاء القراءة المرصودة: رقم زائد أو بدل رقم (طول ≥ طول القيمة الفعلية)؛ الأرقام الأقصر
                # تُعدّ تقريباً مقصوداً من التحليل (مثل 0.0284 تحت دعم 0.02845) فلا تُمسّ.
                if dist <= 2 and len(_dt) >= len(_digits(s)):
                    near.append((dist, s))
        near.sort(key=lambda x: x[0])
        # يُصحَّح فقط عند مرشح وحيد أو أقرب بفارق واضح (مسافة أصغر) — الالتباس يُترك كما هو
        if near and (len(near) == 1 or near[0][0] < near[1][0]):
            count += 1
            return near[0][1]
        return tok

    out = re.sub(r"(?<![\d.])\d[\d,]*\.\d{3,}(?![\d])", _sub, text)
    return out, count


def round_indicator_lines(text: str) -> str:
    """بنود 10/11 (RSI/KDJ): الأرقام الطويلة تُعرض بمنزلتين (37.3988948 → 37.40) بدل نسخ سبع منازل بلا فائدة."""
    out = []
    for ln in (text or "").split("\n"):
        if re.match(r"\s*1[01]\s*[-–.)]", ln):
            ln = re.sub(r"(?<![\d.])(-?\d+\.\d{4,})(?!\d)", lambda m: f"{float(m.group(1)):.2f}", ln)
        out.append(ln)
    return "\n".join(out)

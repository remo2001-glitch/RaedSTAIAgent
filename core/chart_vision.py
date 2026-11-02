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
    # batch53
    "SYMBOL", "CHART_HIGH", "CHART_LOW", "PERF_7D", "PERF_30D", "AXIS_TICKS",
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
        "ملاحظات: TIMEFRAME هو الإطار المظلَّل (مثل 1W أو 1D أو 4H أو 15m؛ وفي الواجهة العربية «ي»=يوم «س»=ساعة «د»=دقيقة «أ»=أسبوع، فاكتب ما يظهر كما هو مثل «1 ي»). PRICE هو السعر الكبير الحالي. "
        "SUPERTREND_GREEN قيمة SUPERTREND بالأخضر وSUPERTREND_RED بالأحمر. "
        "RSI_A وRSI_B وRSI_C هي قيم RSI الثلاث بالترتيب الظاهر (مثل RSI6 وRSI12 وRSI24) بالشكل RSI6=37.39. "
        "MACD_HIST هي قيمة STICK. VOL هي قيمة VOL الأولى وVOL_MA5 هي MA5 في سطر الحجم. "
        "SYMBOL هو اسم الزوج في أعلى الشاشة بالشكل OKB/BTC. "
        "CHART_HIGH وCHART_LOW هما أعلى وأدنى سعر مكتوبين كتسمية على الشارت نفسه (مثل 0.0016590 و0.0013195). "
        "PERF_7D وPERF_30D نسبتا الأداء في الشريط السفلي (7 أيام و30 يوماً) مع الإشارة: ‎+1.37%‎ للأخضر و‎-7.28%‎ للأحمر (علامة - بعد الرقم تعني سالب). "
        "AXIS_TICKS هي أرقام محور السعر الرأسي على يمين الشارت مفصولة بفواصل. "
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
        if k == "SYMBOL":
            m = re.search(r"([A-Za-z0-9]{2,12})\s*[/\-]\s*([A-Za-z0-9]{2,8})", v)
            if m:
                res["SYMBOL"] = (m.group(1).upper(), m.group(2).upper())
            continue
        if k == "AXIS_TICKS":
            ticks = []
            for tk in re.findall(r"\d[\d,]*\.?\d*", v):
                try:
                    ticks.append(float(tk.replace(",", "")))
                except ValueError:
                    pass
            if ticks:
                res["AXIS_TICKS"] = ticks
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
    for k, lab in (("CHART_HIGH", "أعلى قمة مكتوبة على الشارت"), ("CHART_LOW", "أدنى قاع مكتوب على الشارت")):
        if k in leg:
            lines.append(f"{lab} = {fmt(k)}{unit} — استعمل القاع المكتوب هدفاً لسيناريو كسر الدعم، ولا تسمِّ خط SUPPORT «القاع»")
    if "MACD_DIFF" in leg and "MACD_DEA" in leg and float(leg["MACD_DIFF"]) < 0 and float(leg["MACD_DEA"]) < 0:
        lines.append("MACD: DIFF وDEA كلاهما تحت الصفر — لا تصف الزخم «صاعداً» بل «تحسّن دون تأكيد»")
    _tsf = trend_summary_fact(leg, price)
    if _tsf:
        lines.append("ملخص الاتجاه: " + _tsf)
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


# ───────────────────────── batch53 ─────────────────────────
_NAME_KEYS = (
    # (regex للاسم في النص، مفاتيح القيم الصحيحة له)
    (r"SuperTrend|سوبر\s?ترند|السوبر\s?ترند", ("SUPERTREND_RED", "SUPERTREND_GREEN")),
    (r"\bSAR\b", ("SAR",)),
    (r"\bVWAP\b", ("VWAP",)),
    (r"منتصف\s+(?:نطاق\s+)?البولنجر|منتصف\s+BOLL|BOLL\s*MID", ("BOLL_MID",)),
    (r"الحد\s+العلوي\s+(?:لـ?\s*)?(?:ال)?بولنجر|BOLL\s*UB", ("BOLL_UB",)),
    (r"الحد\s+السفلي\s+(?:لـ?\s*)?(?:ال)?بولنجر|BOLL\s*LB", ("BOLL_LB",)),
    (r"\bSUPPORT\b", ("SUPPORT",)),
    (r"\bRESISTANCE\b", ("RESISTANCE",)),
)
_NUM_TOKEN = re.compile(r"(?<![\d.])\d[\d,]*\.\d{3,}(?![\d])")


def _lvl_values(leg: Dict[str, object]) -> Dict[str, float]:
    out = {}
    for k in _LEVEL_KEYS:
        if k in leg:
            try:
                out[k] = float(leg[k])
            except (TypeError, ValueError):
                pass
    return out


def verify_name_value(text: str, leg: Dict[str, object]) -> Tuple[str, int]:
    """batch53 (#389): اسم مؤشر مقروناً بقيمة خط آخر (SuperTrend: 0.0012586 وهي منتصف البولنجر) → القيمة الصحيحة لاسمه.
    يصحّح فقط حين يطابق الرقم تماماً قيمة مفتاح آخر ولا يطابق أياً من قيم الاسم نفسه."""
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}
    vals = _lvl_values(leg)
    if len(vals) < 3:
        return text, 0
    count = 0
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines):
        for pat, keys in _NAME_KEYS:
            own = [k for k in keys if k in vals]
            if not own:
                continue
            for m in list(re.finditer(pat, ln)):
                tail = ln[m.end(): m.end() + 40]
                nm = _NUM_TOKEN.search(tail)
                if not nm:
                    continue
                # لا نتجاوز اسماً آخر بين الاسم والرقم
                between = tail[: nm.start()]
                if any(re.search(p2, between) for p2, _k in _NAME_KEYS if p2 != pat):
                    continue
                try:
                    num = float(nm.group(0).replace(",", ""))
                except ValueError:
                    continue
                if any(abs(num - vals[k]) < 1e-12 * max(1.0, abs(num)) for k in own):
                    continue
                other = [k for k, v in vals.items() if k not in own and abs(num - v) < 1e-12 * max(1.0, abs(num))]
                if not other:
                    continue
                # الأقرب للسعر عند تعدد قيم الاسم (SuperTrend أخضر/أحمر): قيمة واحدة فقط عادةً
                repl = raw.get(own[0]) or f"{vals[own[0]]:g}"
                start = m.end() + nm.start()
                ln = ln[:start] + repl + ln[start + len(nm.group(0)):]
                count += 1
                break
        lines[i] = ln
    return "\n".join(lines), count


def _item_span(lines: List[str], num: int) -> Tuple[int, int]:
    """نطاق أسطر البند المرقَّم num (من سطره حتى قبل البند التالي)."""
    start = -1
    for i, ln in enumerate(lines):
        mm = re.match(r"\s*(\d{1,2})\s*[-–.)]", ln)
        if mm and int(mm.group(1)) == num:
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        mm = re.match(r"\s*(\d{1,2})\s*[-–.)]", lines[j])
        if mm and int(mm.group(1)) != num:
            end = j
            break
    return start, end


def replace_item(text: str, num: int, new_lines: List[str]) -> str:
    lines = (text or "").split("\n")
    a, b = _item_span(lines, num)
    if a < 0:
        return text
    return "\n".join(lines[:a] + new_lines + lines[b:])


def build_levels_item(leg: Dict[str, object], price: float, quote_unit: str = "") -> List[str]:
    """batch53 (#390/#394/#397): بند 2 من الكود — كل الخطوط المقروءة مرتبة تنازلياً بالنسبة للسعر الحالي (فوقه/تحته)."""
    vals = _lvl_values(leg)
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}
    if len(vals) < 3 or price <= 0:
        return []
    unit = f" {quote_unit}" if quote_unit and quote_unit != "USDT" else ""
    nm = {"RESISTANCE": "RESISTANCE (مقاومة الشارت)", "SUPPORT": "SUPPORT (دعم الشارت)", "VWAP": "VWAP",
          "BOLL_MID": "منتصف البولنجر", "BOLL_UB": "الحد العلوي للبولنجر", "BOLL_LB": "الحد السفلي للبولنجر",
          "SUPERTREND_RED": "SuperTrend (الأحمر)", "SUPERTREND_GREEN": "SuperTrend (الأخضر)", "SAR": "SAR"}
    items = []
    for k, v in vals.items():
        items.append((v, nm.get(k, k), raw.get(k) or f"{v:g}", k))
    for k, lab in (("CHART_HIGH", "أعلى قمة مكتوبة على الشارت"), ("CHART_LOW", "أدنى قاع مكتوب على الشارت")):
        if k in leg:
            try:
                v = float(leg[k])
                items.append((v, lab, raw.get(k) or f"{v:g}", k))
            except (TypeError, ValueError):
                pass
    items.sort(key=lambda x: -x[0])
    out = ["2- مستويات الدعم والمقاومة (من الخطوط المقروءة، مرتبة من الأعلى):"]
    for v, lab, rs, k in items:
        d = (v / price - 1) * 100.0
        side = "فوق السعر" if v > price else "تحت السعر"
        role = ""
        if k in ("SUPERTREND_RED", "SAR", "SUPERTREND_GREEN"):
            role = " — مقاومة متحركة" if v > price else " — دعم متحرك"
        out.append(f"   • {lab}: {rs}{unit} ({side} بنحو {abs(d):.1f}%){role}")
    return out


def build_rsi_item(leg: Dict[str, object]) -> List[str]:
    rs = leg.get("rsi") or []
    if not rs:
        return []
    parts = []
    for nm, v in rs:
        z = ("ذروة بيع" if v < 30 else "قرب ذروة البيع" if v < 35 else "ذروة شراء" if v > 70 else "محايد")
        parts.append(f"{nm} = {v:.2f} ({z})")
    s = "10- مؤشر RSI: " + " | ".join(parts)
    if len(rs) >= 2:
        spread = rs[0][1] - rs[-1][1]
        if spread > 15:
            s += " — الأقصر أعلى بكثير من الأطول: زخم قصير الأجل يتسارع"
        elif spread < -15:
            s += " — الأقصر أدنى بكثير من الأطول: زخم قصير الأجل يضعف"
    return [s]


def build_macd_item(leg: Dict[str, object]) -> List[str]:
    if "MACD_DIFF" not in leg or "MACD_DEA" not in leg:
        return []
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}
    d, e = float(leg["MACD_DIFF"]), float(leg["MACD_DEA"])
    rd = raw.get("MACD_DIFF") or f"{d:g}"
    re_ = raw.get("MACD_DEA") or f"{e:g}"
    s = f"12- مؤشر MACD: DIFF {rd} {'فوق' if d > e else 'تحت'} DEA {re_}"
    h = leg.get("MACD_HIST")
    if h is not None:
        s += f"، STICK {'موجب' if float(h) > 0 else 'سالب'} ({raw.get('MACD_HIST') or h})"
    if d < 0 and e < 0:
        s += " — كلاهما تحت الصفر: " + ("تحسّن في الزخم دون تأكيد صعود" if d > e else "زخم هابط")
    elif d > 0 and e > 0:
        s += " — كلاهما فوق الصفر: " + ("زخم صاعد" if d > e else "تباطؤ صعود")
    return [s]


def fix_circular_breakdown(text: str, leg: Dict[str, object]) -> Tuple[str, int]:
    """batch53 (#391): «كسر X ... نحو X» (هدف الكسر هو مستوى الكسر نفسه) → نحو أدنى قاع مكتوب على الشارت إن وُجد وكان أدنى."""
    if "CHART_LOW" not in leg:
        return text, 0
    raw = leg.get("raw", {}) if isinstance(leg.get("raw"), dict) else {}
    low = float(leg["CHART_LOW"])
    low_s = raw.get("CHART_LOW") or f"{low:g}"
    count = 0

    def _sub(m):
        nonlocal count
        a, b = m.group(2), m.group(4)
        try:
            if float(a.replace(",", "")) == float(b.replace(",", "")) and low < float(a.replace(",", "")):
                count += 1
                return f"{m.group(1)}{a}{m.group(3)}{low_s}"
        except ValueError:
            pass
        return m.group(0)

    out = re.sub(r"(كسر[^\n]{0,160}?)(\d[\d,]*\.\d{3,})([^\n]{0,120}?(?:نحو|باتجاه|إلى)\s*)(\d[\d,]*\.\d{3,})", _sub, text or "")
    return out, count


def axis_tick_targets(text: str, leg: Dict[str, object]) -> List[float]:
    """batch53 (#398): أسعار في بند الأهداف (8) تطابق رقماً من محور السعر ولا تطابق خطاً مسمّى ولا قمة/قاعاً مكتوباً."""
    ticks = leg.get("AXIS_TICKS") or []
    if not ticks:
        return []
    named = list(_lvl_values(leg).values())
    for k in ("CHART_HIGH", "CHART_LOW", "PRICE"):
        if k in leg:
            try:
                named.append(float(leg[k]))
            except (TypeError, ValueError):
                pass
    lines = (text or "").split("\n")
    a, b = _item_span(lines, 8)
    if a < 0:
        return []
    out = []
    for tok in _NUM_TOKEN.findall("\n".join(lines[a:b])):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        if any(abs(v - t) < 1e-12 * max(1.0, abs(v)) for t in ticks) and not any(abs(v - n) < 1e-12 * max(1.0, abs(v)) for n in named):
            out.append(v)
    return out


_MA_PHRASES = (
    (r"خطوط\s+المتوسطات(?:\s+المتحركة)?", "خطوط المؤشرات المعروضة"),
    (r"خط\s+المتوسطات(?:\s+المتحركة)?", "خطوط المؤشرات المعروضة"),
    (r"المتوسطات\s+المتحركة", "خطوط المؤشرات المعروضة"),
)


def strip_unseen_mas(text: str, leg: Dict[str, object]) -> Tuple[str, int]:
    """batch53 (#350/#359): النموذج يخترع EMA/MA وهي غير ظاهرة في الشريط المنسوخ → تُستبدل العبارات العامة، وأرقام EMA/MA تُزال."""
    if not leg:
        return text, 0
    count = 0
    out = text or ""
    for pat, rep in _MA_PHRASES:
        out, n = re.subn(pat, rep, out)
        count += n
    out, n = re.subn(r"\s*\(?\b(?:EMA|MA|SMA)\s*\d+(?:\s*(?:و|,|،|/)\s*(?:EMA|MA|SMA)?\s*\d+)*\)?", "", out)
    count += n
    return out, count


def trend_summary_fact(leg: Dict[str, object], price: float) -> str:
    """batch53 (#393/#400/#401): جملة اتجاه محسوبة من علاقة السعر بكل الخطوط + أداء 7/30 يوماً."""
    vals = _lvl_values(leg)
    if price <= 0 or len(vals) < 3:
        return ""
    above = [k for k in ("VWAP", "BOLL_MID") if k in vals and price > vals[k]]
    below = [k for k in ("VWAP", "BOLL_MID", "SUPERTREND_RED", "SAR") if k in vals and price < vals[k]]
    parts = []
    if below:
        parts.append(f"السعر تحت {len(below)} من خطوط الاتجاه/الوقف المقروءة")
    if above:
        parts.append(f"وفوق {len(above)} (VWAP/منتصف البولنجر)")
    p30 = leg.get("PERF_30D")
    p7 = leg.get("PERF_7D")
    if p30 is not None and p7 is not None:
        parts.append(f"الأداء: 7 أيام {float(p7):+.2f}% و30 يوماً {float(p30):+.2f}%")
        if float(p30) < -5 and float(p7) > 0:
            parts.append("الصورة: ارتداد قصير داخل اتجاه أكبر هابط — لا تصفها «قناة صاعدة» ولا «صاعد قوي»")
        elif float(p30) < -5 and float(p7) < 0 and len(below) >= 3:
            parts.append("الصورة: هبوط قائم على الإطارين — لا تصفه «ضعيفاً» ولا «جانبياً»")
    return "؛ ".join(parts)

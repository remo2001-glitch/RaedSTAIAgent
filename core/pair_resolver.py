"""
🔗 رائد — Pair Resolver (تطوير #188 — Phase 2)
═══════════════════════════════════════════════
التصميم النهائي المُتَّفَق عليه:

- التقرير الأساسي لأي رمز (حتى "ETHBTC"/"SOLBTC") هو دائماً
  {BASE}/USDT — نفس المسار المُختبَر بعمق (150+ إصلاح)، لكل المستخدمين،
  بدون أي رسائل رفض/تنبيه منفصلة (لا مشاكل ترتيب كـ#194).

- إذا الرمز بصيغة BASE+BTC/ETH (مثل "ETHBTC")، وكانت الباقة "diamond"/"admin"،
  وكان الزوج متوفراً مباشرة على OKX → تُضاف **فقرة/سطر إضافي** في
  نهاية التقرير (أو نهاية سطر العملة في القوائم متعددة العملات):
  سعر الزوج + دعم/مقاومة بوحدته (للأوامر المفردة فقط).

- إن لم تكن الباقة كافية، أو الزوج غير متوفر → سطر توضيحي قصير واحد
  (بدون "🔒" مُنفِّر) للأوامر المفردة فقط — لا شيء في القوائم
  متعددة العملات (لتجنّب الإطالة/التكرار).

الاستخدام من أي handler:

    from core.pair_resolver import resolve_symbol, build_pair_addon_lines, build_pair_addon_inline

    # أوامر مفردة (/signal, /analyze, /quicksignal):
    resolution = await resolve_symbol(raw_symbol, tier, engine.data_layer)
    symbol = resolution.base          # دائماً يُستخدَم للتقرير الأساسي (USDT)
    ... بناء التقرير العادي بالكامل (بدون أي تغيير) ...
    addon_lines = await build_pair_addon_lines(resolution, engine.data_layer)
    if addon_lines:
        lines.extend(addon_lines)     # أو دمجها حسب بنية التقرير

    # أوامر متعددة العملات (/weekly, /monthly):
    resolutions = {sym: await resolve_symbol(sym, tier, engine.data_layer) for sym in symbols}
    ...
    suffix = await build_pair_addon_inline(resolutions[sym], engine.data_layer)
    line += suffix   # "" إن لا إضافة
"""

from typing import List, Optional, Tuple

from core.data_layer import _clean_symbol

# عملات التسعير المدعومة لإضافة #188 — يمكن التوسعة لاحقاً
QUOTE_CURRENCIES: Tuple[str, ...] = ("BTC", "ETH")

# الباقات المسموح لها بفقرة/سطر الزوج الإضافي (ماسي وأعلى فقط)
_ALLOWED_TIERS = ("diamond", "admin")


def parse_quote_pair(symbol: str) -> Optional[Tuple[str, str]]:
    """
    يكتشف إن كان الرمز بصيغة BASE+QUOTE حيث QUOTE في (BTC, ETH)
    وBASE عملة مختلفة (>=2 أحرف) وليست نفس QUOTE.

    أمثلة:
        "ETHBTC"  -> ("ETH", "BTC")
        "SOLBTC"  -> ("SOL", "BTC")
        "BTC"     -> None  (لا base متبقٍ)
        "ETH"     -> None
        "ETHUSDT" -> None  (لا تنتهي بـ BTC/ETH)
    """
    sym = symbol.upper().strip().replace("/", "").replace("-", "")
    for quote in QUOTE_CURRENCIES:
        if sym.endswith(quote) and len(sym) > len(quote):
            base = sym[: -len(quote)]
            if len(base) >= 2 and base != quote:
                return base, quote
    return None


def _fmt_pair_price(price: float, quote: str) -> str:
    """تنسيق سعر زوج BTC/ETH — بدون '$'، مع لاحقة الوحدة (دقة موحّدة مع handlers.analysis)."""
    from core.thresholds import quote_decimals
    if price <= 0:        return f"0 {quote}"
    return f"{price:,.{quote_decimals(price)}f} {quote}"


def _pair_price_decimals(value: float) -> int:
    """عدد المنازل العشرية لسعر زوج (مصدر واحد: core.thresholds.quote_decimals)."""
    from core.thresholds import quote_decimals
    return quote_decimals(value)


def _fmt_pair_price_pair(a: float, b: float, quote: str) -> Tuple[str, str]:
    """إصلاح #195: تنسيق زوج قيم (دعم/مقاومة) بنفس عدد المنازل
    العشرية — أصغرهما يحدِّد الدقة لكليهما (يتجاوب مع _fmt_price_pair
    في handlers/analysis.py)."""
    dec = max(_pair_price_decimals(a), _pair_price_decimals(b))
    return f"{a:,.{dec}f} {quote}", f"{b:,.{dec}f} {quote}"


def _fmt_usdt_price(price: float) -> str:
    """تنسيق سعر بالدولار — للفقرة المرجعية عندما يكون التقرير
    الأساسي بوحدة BTC/ETH (الزوج هو الأساس)."""
    if price <= 0:        return "$0"
    elif price >= 10:     return f"${price:,.2f}"
    elif price >= 1:      return f"${price:,.4f}"
    elif price >= 0.01:   return f"${price:.6f}"
    elif price >= 0.001:  return f"${price:.7f}"
    elif price >= 1e-6:   return f"${price:.8f}"
    else:                 return f"${price:.10f}"


class PairResolution:
    """نتيجة تحليل الرمز — إصلاح/تطوير #188 (Phase 2)."""

    __slots__ = ("base", "quote", "is_pair_request", "eligible_tier", "pair_available")

    def __init__(
        self,
        base: str,
        quote: Optional[str] = None,
        is_pair_request: bool = False,
        eligible_tier: bool = False,
        pair_available: bool = False,
    ):
        self.base = base
        # عملة التسعير المُكتشَفة من الرمز (BTC/ETH) أو None لرمز عادي
        self.quote = quote
        # True إذا الرمز كان بصيغة BASE+BTC/ETH (بصرف النظر عن الأهلية)
        self.is_pair_request = is_pair_request
        # True إذا الباقة ماسي/admin
        self.eligible_tier = eligible_tier
        # True إذا الزوج متوفر مباشرة على OKX (يُفحَص فقط إذا eligible_tier)
        self.pair_available = pair_available


# Z1: خريطة مرادفات في pair_resolver — طبقة دفاع ثانية
_SYMBOL_ALIASES_PR = {
    "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
    "FACEBOOK": "META", "FB": "META",
    "MICROSOFT": "MSFT", "AMAZON": "AMZN",
    "APPLE": "AAPL", "TESLA": "TSLA",
    "NVIDIA": "NVDA", "COINBASE": "COIN",
    "SPACEX": "SPCX", "MICROSTRATEGY": "MSTR",
    "OPENAI": "OPENAI", "CHATGPT": "OPENAI",
    "GOLD": "XAU", "XAUUSD": "XAU",
    "BITCOIN": "BTC", "ETHEREUM": "ETH",
    "SOLANA": "SOL", "DOGECOIN": "DOGE",
    "BINANCE": "BNB", "RIPPLE": "XRP",
    "CARDANO": "ADA", "AVALANCHE": "AVAX",
    "TRON": "TRX", "CHAINLINK": "LINK",
}


async def resolve_symbol(raw_symbol: str, tier: str, data_layer) -> PairResolution:
    """
    نقطة الدخول الموحَّدة لكل الأوامر (إصلاح/تطوير #188 — Phase 2).

    - رمز عادي (BTC, ETH, SHIB, BTCUSDT...) → PairResolution بسيطة،
      base=_clean_symbol(raw)، is_pair_request=False. التقرير الأساسي
      يُبنى بدون أي تغيير عن السابق.
    - رمز بصيغة BASE+BTC/ETH → base=BASE (يُستخدَم للتقرير الأساسي/USDT
      كما هو)، quote=BTC/ETH، is_pair_request=True. eligible_tier/
      pair_available تُحدِّدان إن كانت إضافة الزوج ستُعرَض.
    """
    # Z1: تطبيع المرادفات قبل أي معالجة
    raw_symbol = _SYMBOL_ALIASES_PR.get(raw_symbol.upper().strip(), raw_symbol)

    parsed = parse_quote_pair(raw_symbol)
    if not parsed:
        return PairResolution(base=_clean_symbol(raw_symbol))

    base, quote = parsed
    eligible = tier in _ALLOWED_TIERS

    available = False
    if eligible:
        try:
            available = await data_layer.check_okx_pair(base, quote)
        except Exception:
            available = False

    return PairResolution(
        base=base,
        quote=quote,
        is_pair_request=True,
        eligible_tier=eligible,
        pair_available=available,
    )


async def _fetch_pair_full_b53(resolution: PairResolution, data_layer):
    """batch53: سعر الزوج + دعم/مقاومة + ميل الزوج اليومي. المستويات تُختار بمسافة دنيا عن السعر (كانت مقاومة على بُعد 0.19% فلا قيمة لها)
    ثم يُرجع (price, support, resistance, lean_text)."""
    base, quote = resolution.base, resolution.quote
    price_d = await data_layer.get_price(base, quote)
    if not price_d or float(price_d.get("price", 0)) <= 0:
        return None
    price = float(price_d["price"])

    support = resistance = None
    near_sup = None
    lean = ""
    candles = await data_layer.get_ohlcv(base, "1d", 60, quote)
    if isinstance(candles, list) and len(candles) >= 20:
        try:
            def _vals(key, n):
                out = []
                for c in candles[-n:]:
                    v = float(c.get(key, c.get("close", 0)) or 0)
                    if v > 0:
                        out.append(v)
                return out
            closes = _vals("close", len(candles))
            # ATR تقريبي (متوسط المدى النسبي لآخر 14 شمعة)
            rng = [(float(c.get("high", 0) or 0) - float(c.get("low", 0) or 0)) for c in candles[-14:]]
            rng = [r for r in rng if r > 0]
            atr_frac = (sum(rng) / len(rng) / price) if rng and price > 0 else 0.0
            min_gap = max(0.005, 0.5 * atr_frac)

            fs = fr = 0.0
            try:
                from handlers.analysis import _calc_fibonacci
                _fib = _calc_fibonacci(candles)
                fs = float((_fib or {}).get("nearest_support") or 0)
                fr = float((_fib or {}).get("nearest_resistance") or 0)
            except Exception:
                pass
            sup_c, res_c = [], []
            if fs > 0:
                sup_c.append(fs)
            if fr > 0:
                res_c.append(fr)
            for n, ks, kr in ((5, 0.999, 1.001), (10, 0.999, 1.001), (20, 0.99, 1.01)):
                lo, hi = _vals("low", n), _vals("high", n)
                if lo:
                    sup_c.append(min(lo) * ks)
                if hi:
                    res_c.append(max(hi) * kr)
            for c in sup_c:
                if c < price and (price - c) / price >= min_gap:
                    support = c
                    break
            # batch54 (#450): أقرب دعم مباشر تخطّاه الحد الأدنى للمسافة — يُذكر كي لا يختلف عن «دعم الشارت» بلا تفسير
            near_sup = None
            _below = [c for c in sup_c if c < price]
            if _below:
                near_sup = max(_below)
                if support is not None and near_sup <= support:
                    near_sup = None
                elif support is None and (price - near_sup) / price >= min_gap:
                    near_sup = None
            for c in res_c:
                if c > price and (c - price) / price >= min_gap:
                    resistance = c
                    break
            # ميل الزوج اليومي: EMA20/EMA50 وتغير 7 أيام (حقائق محسوبة من شموع الزوج نفسه)
            def _ema(vals, n):
                if len(vals) < n:
                    return 0.0
                k = 2.0 / (n + 1)
                e = sum(vals[:n]) / n
                for v in vals[n:]:
                    e = v * k + e * (1 - k)
                return e
            e20, e50 = _ema(closes, 20), _ema(closes, 50)
            bull = bear = 0
            parts = []
            if e20 > 0:
                if price > e20:
                    bull += 1
                else:
                    bear += 1
                parts.append(f"السعر {'فوق' if price > e20 else 'تحت'} EMA20")
            if e50 > 0:
                if price > e50:
                    bull += 1
                else:
                    bear += 1
                parts.append(f"{'فوق' if price > e50 else 'تحت'} EMA50")
            if len(closes) >= 8 and closes[-8] > 0:
                ch7 = (price / closes[-8] - 1) * 100
                if ch7 >= 0:
                    bull += 1
                else:
                    bear += 1
                parts.append(f"7 أيام {ch7:+.1f}%")
            if bull + bear >= 2:
                word = "🟢 صاعد" if bull > bear else "🔴 هابط" if bear > bull else "⚪ مختلط"
                # batch54 (#451/#459): الأساس (بوحدة الزوج) وعدد المعايير الحاكمة
                lean = (f"• ميل الزوج بوحدة {quote} (يومي، بأغلبية {max(bull, bear)} من {bull + bear} معايير): {word} — "
                        + "، ".join(parts))
        except Exception:
            support = resistance = None
            near_sup = None
    return price, support, resistance, lean, near_sup


async def _fetch_pair_price_and_levels(resolution: PairResolution, data_layer):
    """يجلب سعر الزوج المباشر + دعم/مقاومة (من شموع الزوج). يُعيد
    (price, support_or_None, resistance_or_None) أو None عند الفشل."""
    full = await _fetch_pair_full_b53(resolution, data_layer)
    if full is None:
        return None
    return full[0], full[1], full[2]  # batch54: الدالة الكاملة تُرجع الآن 5 عناصر (الأخير أقرب دعم مباشر)


async def build_pair_addon_lines(resolution: PairResolution, data_layer) -> Optional[List[str]]:
    """
    تطوير #188 (Phase 2) — للأوامر المفردة (/signal, /analyze, /quicksignal):
    قائمة أسطر تُلحَق في نهاية التقرير. None إذا كان الرمز عادياً
    (is_pair_request=False) — لا أي تغيير على التقرير.

    - باقة غير كافية → سطر توضيحي قصير واحد (بدون "🔒").
    - الباقة كافية لكن الزوج غير متوفر على OKX → سطر توضيحي قصير.
    - الباقة كافية والزوج متوفر → فقرة: سعر الزوج + دعم/مقاومة بوحدته.
    """
    if not resolution.is_pair_request:
        return None

    base, quote = resolution.base, resolution.quote

    if not resolution.eligible_tier:
        return [
            "",
            f"📎 لعرض {base}/{quote} مباشرة (سعر ودعم/مقاومة بوحدة "
            f"{quote}): متوفر لباقة الماسي وأعلى — /upgrade",
        ]

    if not resolution.pair_available:
        return ["", f"ℹ️ {base}/{quote} غير متوفر مباشرة على OKX حالياً."]

    try:
        fetched = await _fetch_pair_full_b53(resolution, data_layer)
        if fetched is None:
            return ["", f"ℹ️ {base}/{quote} غير متوفر مباشرة على OKX حالياً."]
        price, support, resistance, _lean = fetched[:4]
        _near_sup = fetched[4] if len(fetched) > 4 else None

        lines = [
            "",
            f"📎 *الزوج المطلوب: {base}/{quote}* (تحليل مبسّط — التقرير أعلاه بالدولار)",
            f"• السعر: {_fmt_pair_price(price, quote)}",
        ]
        if support is not None and resistance is not None:
            _sup_s, _res_s = _fmt_pair_price_pair(support, resistance, quote)
            lines.append(f"• دعم: {_sup_s} | مقاومة: {_res_s}")
            if _near_sup:
                lines.append(f"  ↳ أقرب دعم مباشر {_fmt_pair_price(_near_sup, quote)} على بُعد {(price - _near_sup) / price * 100:.1f}% (أقرب من الحد الأدنى للمسافة فلم يُعتمد دعماً رئيسياً)")
        if _lean:
            lines.append(_lean)
        lines.append(f"💡 التقرير الكامل لهذا الزوج: /quicksignal {base}{quote}")
        return lines
    except Exception:
        return None


async def build_usdt_addendum(resolution: PairResolution, data_layer) -> Optional[List[str]]:
    """
    تطوير #188 (Phase 2.5): عندما يكون التقرير الأساسي بوحدة BTC/ETH
    (الزوج المطلوب هو الأساس — في /quicksignal عند التوفر الكامل)،
    تُضاف فقرة صغيرة مرجعية بسعر العملة الأساسية مقابل USDT.
    """
    base = resolution.base
    try:
        price_d = await data_layer.get_price(base, "USDT")
        if not price_d or float(price_d.get("price", 0)) <= 0:
            return None
        price = float(price_d["price"])
        return ["", f"📎 أيضاً: {base}/USDT = {_fmt_usdt_price(price)}"]
    except Exception:
        return None


async def build_pair_addon_inline(resolution: PairResolution, data_layer) -> str:
    """
    تطوير #188 (Phase 2) — للأوامر متعددة العملات (/weekly, /monthly):
    سطر مُكثَّف (بادئته مسافة) يُلحَق داخل سطر العملة نفسه.
    يُعيد "" دائماً إذا: رمز عادي، باقة غير كافية، أو الزوج غير متوفر
    (صامت في السياق المُكثَّف لتجنّب إطالة/تشويش كل سطر).
    """
    if not resolution.is_pair_request:
        return ""
    if not resolution.eligible_tier or not resolution.pair_available:
        return ""

    base, quote = resolution.base, resolution.quote
    try:
        price_d = await data_layer.get_price(base, quote)
        if not price_d or float(price_d.get("price", 0)) <= 0:
            return ""
        price = float(price_d["price"])
        return f" | {base}/{quote}: {_fmt_pair_price(price, quote)}"
    except Exception:
        return ""

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
    """تنسيق سعر زوج BTC/ETH — بدون '$'، مع لاحقة الوحدة."""
    if price <= 0:        return f"0 {quote}"
    elif price >= 1:      return f"{price:,.4f} {quote}"
    elif price >= 0.001:  return f"{price:.6f} {quote}"
    elif price >= 1e-6:   return f"{price:.8f} {quote}"
    else:                 return f"{price:.10f} {quote}"


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


async def _fetch_pair_price_and_levels(resolution: PairResolution, data_layer):
    """يجلب سعر الزوج المباشر + دعم/مقاومة (من شموع الزوج). يُعيد
    (price, support_or_None, resistance_or_None) أو None عند الفشل."""
    base, quote = resolution.base, resolution.quote
    price_d = await data_layer.get_price(base, quote)
    if not price_d or float(price_d.get("price", 0)) <= 0:
        return None
    price = float(price_d["price"])

    support = resistance = None
    candles = await data_layer.get_ohlcv(base, "1d", 60, quote)
    if isinstance(candles, list) and len(candles) >= 20:
        lows = [
            float(c.get("low", c.get("close", 0))) for c in candles[-20:]
            if float(c.get("low", c.get("close", 0))) > 0
        ]
        highs = [
            float(c.get("high", c.get("close", 0))) for c in candles[-20:]
            if float(c.get("high", c.get("close", 0))) > 0
        ]
        if lows and highs:
            support    = min(lows) * 0.99
            resistance = max(highs) * 1.01

    return price, support, resistance


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
        fetched = await _fetch_pair_price_and_levels(resolution, data_layer)
        if fetched is None:
            return ["", f"ℹ️ {base}/{quote} غير متوفر مباشرة على OKX حالياً."]
        price, support, resistance = fetched

        lines = [
            "",
            f"📎 *أيضاً متوفر: {base}/{quote}*",
            f"• السعر: {_fmt_pair_price(price, quote)}",
        ]
        if support is not None and resistance is not None:
            lines.append(
                f"• دعم: {_fmt_pair_price(support, quote)} | "
                f"مقاومة: {_fmt_pair_price(resistance, quote)}"
            )
        return lines
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

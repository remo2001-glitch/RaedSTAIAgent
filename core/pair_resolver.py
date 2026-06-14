"""
🔗 رائد — Pair Resolver (تطوير #188)
════════════════════════════════════
يدعم تحليل أزواج التداول مباشرة مقابل BTC أو ETH (مثل ETHBTC, SOLBTC, XRPETH)
بدلاً من معاملتها صامتاً كـ "العملة/USDT" (كانت هذه المشكلة #184/#186/#187).

القيود (حسب طلب رحال):
- متوفر فقط لباقتي "diamond" و"admin".
- إن لم يكن الزوج متوفراً مباشرة على OKX → رسالة تنبيه + بديل تلقائي
  (تحليل العملة مقابل USDT).
- إن لم تكن الباقة كافية → رسالة "🔒 ماسي وأعلى فقط" + بديل USDT.

الاستخدام من أي handler:
    from core.pair_resolver import resolve_symbol
    resolution = await resolve_symbol(raw_symbol, tier, engine.data_layer)
    # resolution.base         -> الرمز الأساسي النظيف (مثل "ETH")
    # resolution.quote        -> "BTC"/"ETH" أو None (يعني USDT الافتراضي)
    # resolution.is_pair      -> True إذا سيُحلَّل الزوج فعلياً مقابل quote
    # resolution.display_symbol -> "ETH/BTC" أو "ETH" للعرض في العناوين
    # resolution.notice       -> رسالة تنبيه (إن وُجدت) يجب عرضها للمستخدم
    # resolution.denied       -> True إذا الباقة لا تسمح
    # resolution.denied_message -> رسالة الرفض (إن denied=True)
"""

from typing import Optional, Tuple

from core.data_layer import _clean_symbol

# عملات التسعير المدعومة لأزواج #188 — يمكن التوسعة لاحقاً
QUOTE_CURRENCIES: Tuple[str, ...] = ("BTC", "ETH")

# الباقات المسموح لها بأزواج BTC/ETH (حسب طلب رحال: ماسي وأعلى فقط)
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


class PairResolution:
    """نتيجة تحليل الرمز — تُستخدَم بواسطة كل الـhandlers (إصلاح #188)."""

    __slots__ = (
        "base", "quote", "display_symbol", "regime_symbol",
        "is_pair", "notice", "denied", "denied_message",
    )

    def __init__(
        self,
        base: str,
        quote: Optional[str] = None,
        display_symbol: Optional[str] = None,
        regime_symbol: Optional[str] = None,
        is_pair: bool = False,
        notice: Optional[str] = None,
        denied: bool = False,
        denied_message: Optional[str] = None,
    ):
        self.base = base
        self.quote = quote  # None == USDT (الوضع الافتراضي، بدون تغيير)
        self.display_symbol = display_symbol or base
        # رمز مستقل لكاش regime_detector (#85) — يمنع تلوّث كاش
        # "ETH" العادي بنتيجة محسوبة من شموع ETH/BTC والعكس
        self.regime_symbol = regime_symbol or base
        self.is_pair = is_pair
        self.notice = notice
        self.denied = denied
        self.denied_message = denied_message

    @property
    def quote_or_usdt(self) -> str:
        return self.quote or "USDT"


async def resolve_symbol(raw_symbol: str, tier: str, data_layer) -> PairResolution:
    """
    نقطة الدخول الموحَّدة لكل الأوامر (إصلاح/تطوير #188).

    - رمز عادي (BTC, ETH, SHIB, BTCUSDT...) → سلوك افتراضي تماماً
      كما كان قبل #188 (base=_clean_symbol(raw), quote=None=USDT).
    - رمز بصيغة BASE+BTC/ETH:
        * الباقة < ماسي  → denied=True + رسالة + بديل USDT (base فقط)
        * الزوج غير متوفر على OKX → notice + بديل USDT (base فقط)
        * متوفر + ماسي+ → is_pair=True, quote=BTC/ETH
    """
    parsed = parse_quote_pair(raw_symbol)
    if not parsed:
        # المسار الافتراضي — لا تغيير عن السلوك السابق لـ#188
        return PairResolution(base=_clean_symbol(raw_symbol))

    base, quote = parsed

    # القيد: ماسي + admin فقط (حسب طلب رحال)
    if tier not in _ALLOWED_TIERS:
        return PairResolution(
            base=base,
            denied=True,
            denied_message=(
                f"🔒 تحليل زوج *{base}/{quote}* مباشرة متوفر فقط "
                f"لباقة الماسي وأعلى.\n"
                f"سيتم عرض تحليل *{base}/USDT* بدلاً من ذلك.\n\n"
                f"⬆️ للترقية: /upgrade"
            ),
        )

    # تحقق من توفر الزوج مباشرة على OKX (إصلاح #188)
    available = await data_layer.check_okx_pair(base, quote)
    if not available:
        return PairResolution(
            base=base,
            notice=(
                f"ℹ️ زوج *{base}/{quote}* غير متوفر مباشرة على OKX حالياً.\n"
                f"سيتم عرض تحليل *{base}/USDT* كبديل."
            ),
        )

    return PairResolution(
        base=base,
        quote=quote,
        display_symbol=f"{base}/{quote}",
        regime_symbol=f"{base}{quote}",
        is_pair=True,
    )

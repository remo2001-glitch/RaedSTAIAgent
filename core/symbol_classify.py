"""
تصنيف الأصول المُرمَّزة (X-prefix) — مصدر واحد مشترك.

symbol_classify_fix: كانت القاعدة "الرمز يبدأ بـX وطوله أكثر من 2 ⇒ أصل
مُرمَّز (سهم/صندوق أمريكي على OKX)" مكرَّرة في نحو 40 موضعاً بالمشروع، بينما
قائمة الاستثناء للعملات الرقمية الحقيقية التي تبدأ بـX بالصدفة (XRP وXLM
وXMR…) موجودة في موضع واحد فقط (core/data_layer.py::_cg_id). الأثر الفعلي
المُتحقَّق منه بتشغيل الكود الحي:
  • /signal و/analyze: XRP/XLM/XMR/XTZ تستلم تحذير "سوق NYSE/NASDAQ مغلق
    (عطلة نهاية الأسبوع)" وهي عملات تعمل 24/7؛
  • /trade (تداول حقيقي): مستخدم الباقات الأدنى يُمنَع من XRP بعبارة
    "الأصول المُرمَّزة (مثل XRP) للذهبي وأعلى فقط"، ويُطبَّق عليها سقف وقف
    خسارة 7% بدل 10%؛
  • /signal و/analyze: وسم "أصل اصطناعي/مُرمَّز" وخصم جودة synthetic_weak.

هذا الملف بلا أي استيراد من المشروع عمداً (لا استيراد دائري ممكن).
"""

# عملات رقمية حقيقية تبدأ بـX — ليست أصولاً مُرمَّزة. نفس القائمة التي كانت
# مُضمَّنة في core/data_layer.py::_cg_id (أصبحت تستورد من هنا).
REAL_CRYPTO_X_TICKERS = frozenset({"XRP", "XLM", "XMR", "XTZ", "XEM", "XDC", "XAUT"})

_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")


def base_ticker(sym) -> str:
    """الرمز الأساسي بأحرف كبيرة: يُزيل زوج التداول (/USDT، -USDT) ولاحقة
    العملة المقابلة (USDT/USDC/BUSD/USD) إن وُجدت."""
    if not sym:
        return ""
    s = str(sym).upper().strip().split("/")[0].split("-")[0]
    for q in _QUOTE_SUFFIXES:
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


def is_tokenized_x_ticker(sym) -> bool:
    """هل هذا أصل مُرمَّز بنمط X-prefix (XSPY، XAAPL، XNVDA…)؟

    مطابق تماماً للقاعدة القديمة (تبدأ بـX وطولها > 2) فيما عدا العملات
    الرقمية الحقيقية في REAL_CRYPTO_X_TICKERS، وفيما عدا أن لاحقة زوج
    التداول تُزال أولاً (XRPUSDT تُعامَل كـXRP).
    """
    b = base_ticker(sym)
    return b.startswith("X") and len(b) > 2 and b not in REAL_CRYPTO_X_TICKERS

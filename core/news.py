"""
# T11b_v3: 2026-07-31 (think filter active)
📰 رائد — News Analysis Engine
يجمع الأخبار من CryptoPanic + RSS ويحللها بـ Groq (Llama 3.3 70B)
مجاني تماماً — 30 طلب/دقيقة بدون بطاقة ائتمان
يُنتج: درجة المشاعر · التأثير المتوقع · ملخص عربي احترافي
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

# ─── Groq API (مجاني — يحتاج مفتاح فقط من console.groq.com) ─────────────────
GROQ_API_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL     = "llama-3.3-70b-versatile"   # أفضل نموذج مجاني
GROQ_FALLBACK  = "llama-3.1-8b-instant"       # fallback أسرع (لم يُوقَف)

# مصادر RSS المجانية الموثوقة
RSS_SOURCES = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

SENTIMENT_LABELS = {
    "very_bullish": ("🟢🟢 إيجابي جداً",  1.0),
    "bullish":      ("🟢 إيجابي",          0.65),
    "neutral":      ("⚪ محايد",           0.0),
    "bearish":      ("🔴 سلبي",           -0.65),
    "very_bearish": ("🔴🔴 سلبي جداً",    -1.0),
}



# إصلاح #192: كلمات مفتاحية كريبتو إلزامية
_CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
    "defi", "nft", "stablecoin", "usdt", "usdc", "binance", "coinbase",
    "altcoin", "token", "wallet", "mining", "satoshi", "web3",
    "solana", "sol", "ripple", "xrp", "cardano", "ada", "polkadot",
    "dot", "avalanche", "avax", "chainlink", "link", "uniswap",
    "sec crypto", "cftc", "digital asset", "digital currency",
    "كريبتو", "بيتكوين", "إيثيريوم", "بلوكتشين", "عملة رقمية",
    "تشفير", "منصة تداول", "عملات", "ديفاي",
}

def _is_crypto_news(title: str) -> bool:
    """يتحقق أن الخبر يخص الكريبتو — إصلاح #192."""
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in _CRYPTO_KEYWORDS)


def _translate_news_title(title: str) -> str:
    """ترجمة المصطلحات الشائعة في عناوين الأخبار."""
    if not title:
        return title
    arabic_chars = sum(1 for c in title if "؀" <= c <= "ۿ")
    if arabic_chars > len(title) * 0.3:
        return title
    # إصلاح #122/#129: قاموس موسّع مع دعم الجمع + regex
    import re as _re
    replacements = [
        ("Bitcoins","بيتكوين"),("Ethereums","إيثيريوم"),
        ("ETFs","صناديق ETF"),("approvals","موافقات"),
        ("outflows","تدفقات خروج"),("inflows","تدفقات دخول"),
        ("rallies","ارتفاعات"),("crashes","انهيارات"),
        ("surges","قفزات"),("bans","حظر"),("hacks","اختراقات"),
        ("exchanges","منصات تداول"),("regulations","تنظيمات"),
        ("lawsuits","دعاوى قضائية"),("analysts","محللون"),
        ("markets","أسواق"),("indicators","مؤشرات"),
        ("losses","خسائر"),("gains","مكاسب"),("warnings","تحذيرات"),
        ("charges","تهم قانونية"),("sells","يبيع"),("buys","يشتري"),
        ("drops","يهبط"),("rises","يرتفع"),("falls","يتراجع"),
        ("Bitcoin","بيتكوين"),("Ethereum","إيثيريوم"),
        ("SEC","هيئة SEC"),("ETF","صندوق ETF"),
        ("approval","موافقة"),("approved","وافقت"),
        ("fraud","احتيال"),("billion","مليار"),("million","مليون"),
        ("record","رقم قياسي"),("outflow","تدفق خروج"),
        ("inflow","تدفق دخول"),("rally","ارتفاع"),
        ("crash","انهيار"),("surge","قفزة"),
        ("ban","حظر"),("hack","اختراق"),
        ("exchange","منصة تداول"),("institutional","مؤسسي"),
        ("adoption","تبني"),("regulation","تنظيم"),
        ("lawsuit","دعوى قضائية"),("treasury","خزينة"),
        ("analyst","محلل"),("market","سوق"),("global","عالمي"),
        ("crypto","كريبتو"),("blockchain","بلوكتشين"),
        ("trading","تداول"),("indicator","مؤشر"),
        ("loss","خسارة"),("gain","مكسب"),("warning","تحذير"),
        ("bullish","صعودي"),("bearish","هبوطي"),
        ("sell","بيع"),("buy","شراء"),
        ("drop","هبوط"),("rise","ارتفاع"),("fall","تراجع"),
        # إصلاح #301: كلمات شائعة في عناوين الكريبتو
        ("scoring","محققاً"),("redeemed","استردّ"),("redemption","استرداد"),
        ("sweeps","يكتسح"),("sweep","يكتسح"),("backed","مدعوم"),
        ("supported","مدعوم"),("candidates","مرشحون"),("candidate","مرشح"),
        ("primaries","انتخابات تمهيدية"),("primary","تمهيدي"),
        ("just","للتو"),("someone","شخص"),("physical","مادي"),
        ("copying","يستنسخ"),("perfectly","تماماً"),("trader","متداول"),
        ("key","رئيسي"),("failing","يفشل"),
        ("firms","شركات"),("firm","شركة"),("face","تواجه"),
        ("cutoff","موعد نهائي"),("grace period","فترة سماح"),
        ("ends","تنتهي"),("lows","أدنى مستويات"),("highs","أعلى مستويات"),
        ("holds","يحتفظ"),("hold","يحتفظ"),
        ("sees","يرى"),("said","قال"),("says","يقول"),
        ("scores","يحقق"),("score","يحقق"),
        # إصلاح #328/#329: كلمات إضافية شائعة
        ("slumps","يتراجع بحدة"),("slump","تراجع حاد"),
        ("warns","يحذر"),("warn","يحذر"),("warning","تحذير"),
        ("wave","موجة"),("failures","إخفاقات"),("failure","إخفاق"),
        ("five-year","خمس سنوات"),("year-old","سنة قديم"),
        ("tokenized","مرمَّز"),("debuts","تُطلق"),("debut","إطلاق"),
        ("pays","يدفع"),("rewards","مكافآت"),("reward","مكافأة"),
        ("card","بطاقة"),("visa","فيزا"),("gold","ذهب"),
        ("stablecoin","عملة مستقرة"),("stablecoins","عملات مستقرة"),
        ("low","منخفض"),("price","سعر"),("as","مع"),
        ("charles","تشارلز"),("a ",""),  # حذف أداة التعريف الإنجليزية
        ("an ",""),("the ",""),
    ]

    # إصلاح #962: result مُعرَّف مسبقاً دائماً
    result = title  # تعريف مبكر قبل أي استخدام
    for en, ar in replacements:
        result = _re.sub(
            r"(?<![\w\u0600-\u06FF])" + _re.escape(en) + r"(?![\w\u0600-\u06FF])",
            ar, result, flags=_re.IGNORECASE
        )
    # تنظيف جمع إنجليزي متبقٍّ بعد ترجمة جذره
    result = _re.sub(r"([\u0600-\u06FF]{3,})(?:es|s)\b", lambda m: m.group(1), result)
    # معالجة أنماط خاصة
    import re as _re2
    result = _re2.sub(r'(\d+)-[Yy]ear-[Oo]ld', lambda m: f"{m.group(1)} سنة", result)
    result = _re2.sub(r'(\d+)-[Yy]ear', lambda m: f"{m.group(1)} سنة", result)

    # إصلاح #16: إذا بقي الناتج خليطاً عربي-إنجليزي غير مقروء
    # (ترجمة جزئية بالقاموس) → نُعيد العنوان الإنجليزي الأصلي بالكامل
    # (أوضح للقارئ من خليط "بيتكوين يرتفع despite US inflation")
    words = result.split()
    if words:
        latin_words  = sum(1 for w in words if _re.search(r"[A-Za-z]{3,}", w))
        arabic_words = sum(1 for w in words if _re.search(r"[\u0600-\u06FF]", w))
        if latin_words > 0 and arabic_words > 0:
            latin_ratio = latin_words / len(words)
            if latin_ratio >= 0.15:   # خليط ملحوظ → غير مقروء
                return title           # العنوان الإنجليزي الأصلي كاملاً
    return result


def _normalize_groq_response(data: dict) -> dict:
    """
    إصلاح #229/#240/#244/#245: تطبيع استجابة Groq.
    يكشف المفاتيح الملتصقة ("اتجاهكبير") بالبحث الجزئي.
    """
    if not isinstance(data, dict):
        return {}

    result = {}

    # ── استخراج sentiment بكشف جزئي للمفاتيح ────────────────
    def _find_key_partial(d, patterns):
        """يبحث عن مفتاح يحتوي أياً من الأنماط."""
        for k in d:
            k_clean = str(k).replace(" ", "").replace("_", "")
            for p in patterns:
                if p in k_clean:
                    return k
        return None

    def _val_to_sentiment(val):
        val = str(val).lower()
        if any(w in val for w in ["إيجابيجداً","صعوديقوي","very_bull","verybull"]):
            return "very_bullish"
        if any(w in val for w in ["إيجابي","صعودي","bullish","ايجابي"]):
            return "bullish"
        if any(w in val for w in ["سلبيجداً","هبوطيقوي","very_bear","verybear"]):
            return "very_bearish"
        if any(w in val for w in ["سلبي","هبوطي","bearish"]):
            return "bearish"
        return "neutral"

    # البحث عن sentiment
    sent_key = _find_key_partial(data, ["مشاعر","sentiment","حالة","توجه","اتجاه"])
    if sent_key:
        result["sentiment"] = _val_to_sentiment(data[sent_key])
    else:
        # استنتاج من توصية أو اتجاه
        rec_key = _find_key_partial(data, ["توصية","recommendation","توقع"])
        if rec_key:
            rec_val = str(data[rec_key]).lower()
            if any(w in rec_val for w in ["بيع","sell","هابط","bear"]):
                result["sentiment"] = "bearish"
            elif any(w in rec_val for w in ["شراء","buy","صاعد","bull"]):
                result["sentiment"] = "bullish"
            else:
                result["sentiment"] = "neutral"

    # البحث عن sentiment_score
    score_key = _find_key_partial(data, ["درجة","score","مشاعر_درجة"])
    if score_key:
        try:
            result["sentiment_score"] = float(data[score_key])
        except (ValueError, TypeError):
            result["sentiment_score"] = -0.3 if result.get("sentiment","") == "bearish" else 0.0
    else:
        result["sentiment_score"] = -0.3 if result.get("sentiment","") in ("bearish","very_bearish") else 0.3 if result.get("sentiment","") in ("bullish","very_bullish") else 0.0

    # استخراج summary
    for k in summary_map:
        if k in data and isinstance(data[k], str) and len(data[k]) > 10:
            result["summary_ar"] = data[k]
            break

    # إذا استخرجنا على الأقل sentiment → نكمل الباقي بقيم افتراضية
    if "sentiment" in result:
        result.setdefault("sentiment_score", 0.0)
        result.setdefault("summary_ar", str(data.get("تحليل", data.get("analysis", "")))[:300])
        result.setdefault("impact_level", "medium")
        result.setdefault("key_events", [])
        result.setdefault("affected_coins", ["BTC", "ETH"])
        result.setdefault("market_impact_ar", "")
        result.setdefault("confidence", 0.6)
        result.setdefault("risk_flags", [])
        return result

    return {}


def _rsi_interpretation(rsi: float) -> str:
    """تفسير RSI بمناطق دقيقة."""
    if rsi >= 80:   return "ذروة شراء شديدة جداً — خطر انعكاس حاد"
    if rsi >= 70:   return "ذروة شراء — تحقق من التوقيت"
    if rsi >= 60:   return "قريب من ذروة الشراء — حذر"
    if rsi >= 45:   return "محايد — لا إشارة واضحة"
    if rsi >= 35:   return "قريب من ذروة البيع — مراقبة"
    if rsi >= 25:   return "ذروة بيع — فرصة انعكاس محتملة"
    return "ذروة بيع شديدة — احتمال ارتداد قوي"


def _fear_greed_interpretation(value: int) -> str:
    """تفسير مؤشر Fear & Greed."""
    if value >= 80: return "جشع شديد — سوق مُبالَغ فيه"
    if value >= 60: return "جشع — حذر من التصحيح"
    if value >= 45: return "محايد — ترقب"
    if value >= 25: return "خوف — فرصة تجميع محتملة"
    return "خوف شديد — أدنى مستويات المشاعر"


def _contradiction_analysis(rsi: float, fear_greed: int,
                              regime_desc: str, news_sentiment: float = 0.0) -> str:
    """تحليل التناقض بين المؤشرات."""
    is_bearish = "هابط" in str(regime_desc)
    oversold   = rsi < 35
    high_fear  = fear_greed < 30
    contradictions = []
    if is_bearish and oversold and high_fear:
        contradictions.append("⏳ ذعر + ذروة بيع + هابط = انتظر تأكيد قبل الدخول")
    if is_bearish and oversold and not high_fear:
        contradictions.append("🟡 ذروة بيع رغم الهابط = فرصة محتملة للمتداولين المتقدمين")
    if not is_bearish and fear_greed > 70 and rsi > 65:
        contradictions.append("🔴 طمع + ذروة شراء = خطر انعكاس")
    if news_sentiment > 0.3 and is_bearish:
        contradictions.append("📰 أخبار إيجابية رغم الهبوط = تناقض يستحق المراقبة")
    return "\n".join(contradictions)


def _strip_markdown_headers(text: str) -> str:
    """يحذف headers الـ Markdown وينظف رموز Telegram غير المُغلَقة."""
    if not text: return text
    import re as _re_md
    # حذف headers
    lines = text.split("\n")
    clean = [l for l in lines if not l.startswith("###") and not l.startswith("####")]
    text = "\n".join(clean).strip()
    # T11b_Telegram_fix: إزالة رموز Markdown التي تُسبب خطأ Telegram
    # إزالة ** bold ** و * italic * وغيرها
    text = _re_md.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)  # **bold** → bold
    text = _re_md.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)      # __italic__ → italic
    text = _re_md.sub(r"`{1,3}([^`]+)`{1,3}", r"\1", text)          # `code` → code
    text = _re_md.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)    # [link](url) → link
    text = _re_md.sub(r"^#{1,6}\s+", "", text, flags=_re_md.MULTILINE) # # headers
    return text.strip()


def _strip_mixed_language_artifacts(text: str, price: float = None) -> str:
    """
    إصلاح #94: شبكة أمان حتمية فوق تعليمة الـprompt — تُزيل كلمات/أحرف
    إنجليزية مدمجة وسط النص العربي (مثل "وraises احتمال" -> "و احتمال").
    لا تؤثر على الأرقام أو رموز العملات (BTC, RSI, EMA...) المُحاطة بمسافات.
    """
    if not text:
        return text
    import re
    # إصلاح #94: كلمة إنجليزية مدمجة مباشرة مع كلمة عربية بدون مسافة
    # (مثل "وraises احتمال") — تُحذف/تُفصَل.
    # إصلاح #172: استثناء التطويل (ـ، U+0640) قبل الرمز اللاتيني:
    # صيغة "لـVIRTUAL" أو "لـBTC" مشروعة في اللغة العربية (حرف جر + تطويل + رمز)
    # — تُحذف التطويل وتُضاف مسافة بدل حذف الرمز كاملاً.
    # 1. صيغة "لـSYMBOL": احذف التطويل فقط، أبقِ الرمز مع مسافة قبله
    text = re.sub(r'(?<=[\u0600-\u06FE\u0641-\u06FF])\u0640([a-zA-Z]{2,})', r' \1', text)
    # 2. كلمة إنجليزية ملتصقة بحرف عربي (غير تطويل) من الجهتين — المعالجة العادية
    text = re.sub(r'(?<=[\u0600-\u063F\u0641-\u06FF])([a-zA-Z]{2,})(?=[\u0600-\u063F\u0641-\u06FF\s])', ' ', text)
    # 3. كلمة إنجليزية ملتصقة بحرف عربي من اليمين
    text = re.sub(r'([a-zA-Z]{2,})(?=[\u0600-\u063F\u0641-\u06FF])', r'\1 ', text)
    # إصلاح #126/#137: أحرف صينية/يابانية/كورية (CJK) مدمجة في النص العربي
    # (مثل "ب新闻 متنوعة") — لا وجود مشروع لها في تقرير عربي، تُحذف كاملة
    _CJK = (
        r'\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7a3'
    )
    text = re.sub(rf'(?<=[\u0600-\u06FF])([{_CJK}]+)(?=[\u0600-\u06FF\s]|$)', ' ', text)
    text = re.sub(rf'([{_CJK}]+)(?=[\u0600-\u06FF])', ' ', text)
    text = re.sub(rf'[{_CJK}]+', '', text)  # أي أحرف CJK متبقية في أي موضع
    # تنظيف المسافات المزدوجة الناتجة
    text = re.sub(r' {2,}', ' ', text)

    # إصلاح #119: تحويل الأرقام العربية الشرقية (٠-٩) والفواصل
    # العربية (٫ عشرية، ٬ آلاف) إلى أرقام/فواصل لاتينية — معيار ثابت
    # صريح: الأرقام العربية يجب ألا تظهر أبداً في أي خرج (١٢٣ → 123)
    _AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
    _digit_map = {ar: str(i) for i, ar in enumerate(_AR_DIGITS)}
    _digit_map["٫"] = "."   # الفاصلة العشرية العربية
    _digit_map["٬"] = ","   # فاصل الآلاف العربي
    text = "".join(_digit_map.get(ch, ch) for ch in text)

    # إصلاح #150/#153: لعملات السعر <$1، أي جملة تحتوي "ألف" تكاد تكون
    # هلوسة "أرقام مكتوبة بالحروف" (مثل "كسر سبعين ألف" بدل "0.07") —
    # لا يوجد سياق مالي مشروع يصف مستوى سعري لعملة sub-cent بـ"آلاف".
    # نحذف الجملة كاملة (تدهور رشيق: جملة ناقصة أفضل من رقم خاطئ ×1,000,000)
    if price is not None and 0 < price < 1 and "ألف" in text:
        _sentences = re.split(r'(?<=[.!؟])\s+', text)
        _sentences = [s for s in _sentences if "ألف" not in s]
        text = " ".join(_sentences)
        text = re.sub(r' {2,}', ' ', text)

    # إصلاح #159: تحويل التدوين العلمي (4.978e-06) إلى عشري عادي
    # (0.00000498) — يتوافق مع باقي الأرقام المعروضة بصيغة عشرية
    def _sci_to_decimal(m):
        try:
            val = float(m.group(0))
            exp = int(m.group("exp"))
            decimals = max(2, -exp + 2) if exp < 0 else 0
            out = f"{val:.{decimals}f}"
            if "." in out:
                out = out.rstrip("0").rstrip(".") or "0"
            return out
        except Exception:
            return m.group(0)
    text = re.sub(r'\d+\.?\d*[eE](?P<exp>[-+]?\d+)', _sci_to_decimal, text)

    return text.strip()


def _clean_news_result(result: dict) -> dict:
    """
    إصلاح #131: تطبيق _strip_mixed_language_artifacts على كل الحقول
    النصية في نتيجة /news (كانت تُعاد مباشرة من Groq بدون أي تنظيف،
    خلافاً لمسار /analyze الذي يُطبِّق هذه الدالة على السرد).
    """
    if not isinstance(result, dict):
        return result
    for k in ("summary_ar", "market_impact_ar"):
        if isinstance(result.get(k), str):
            result[k] = _strip_mixed_language_artifacts(result[k])
    for k in ("key_events", "risk_flags"):
        v = result.get(k)
        if isinstance(v, list):
            result[k] = [_strip_mixed_language_artifacts(x) if isinstance(x, str) else x
                          for x in v]
    return result


def _strip_header_duplicates(text: str) -> str:
    """يحذف السطور المكررة من بداية نص التحليل."""
    if not text: return text
    lines = text.strip().split("\n")
    skip_keys = ("السعر:", "rsi", "fear", "السوق:", "📊", "💰", "🌍")
    clean, header_done = [], False
    for line in lines:
        stripped = line.strip().lower()
        if not header_done and any(k in stripped for k in skip_keys):
            continue
        else:
            header_done = True
            clean.append(line)
    result = "\n".join(clean).strip()
    return result if len(result) > 20 else text


class NewsEngine:
    def __init__(self, groq_key: str = "", session: Optional[aiohttp.ClientSession] = None):
        self.groq_key = groq_key
        self.session  = session
        self._cache:   Dict[str, Tuple[List, float]] = {}
        self._cache_ttl = 300   # 5 دقائق

    # ═══════════════════════════════════════════════════════════
    # 1. جمع الأخبار
    # ═══════════════════════════════════════════════════════════
    async def fetch_news(self, symbols: List[str] = None,
                          limit: int = 20) -> List[Dict]:
        cache_key = ",".join(sorted(symbols or ["BTC", "ETH"]))
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        if not self.session:
            return []

        items = []

        # ── CryptoPanic ──
        try:
            currencies = ",".join(symbols or ["BTC", "ETH", "BNB"])
            url    = "https://cryptopanic.com/api/v1/posts/"
            params = {"public": "true", "currencies": currencies,
                      "filter": "important", "kind": "news"}
            async with self.session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    for post in data.get("results", [])[:limit]:
                        title = post.get("title", "")
                        if title:
                            items.append({
                                "title":      title,
                                "url":        post.get("url", ""),
                                "source":     post.get("source", {}).get("title", ""),
                                "published":  post.get("created_at", ""),
                                "votes_pos":  post.get("votes", {}).get("positive", 0),
                                "votes_neg":  post.get("votes", {}).get("negative", 0),
                                "currencies": [c["code"] for c in post.get("currencies", [])],
                            })
        except Exception as e:
            logger.warning(f"CryptoPanic: {e}")

        # ── RSS fallback ──
        if len(items) < 5:
            items.extend(await self._fetch_rss(limit=15))

        # إصلاح #192: فلتر الأخبار غير الكريبتو
        items = [i for i in items if _is_crypto_news(i.get("title", ""))]
        items = items[:limit]
        self._cache[cache_key] = (items, time.time())
        return items

    async def _fetch_rss(self, limit: int = 10) -> List[Dict]:
        import re
        results = []
        if not self.session:
            return results
        for source_name, url in RSS_SOURCES:
            try:
                async with self.session.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0",
                             "Accept": "application/rss+xml, text/xml"},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        text = await r.text()
                        for block in re.findall(r"<item>(.*?)</item>",
                                                text, re.DOTALL)[:5]:
                            t = re.search(
                                r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                                block, re.DOTALL)
                            l = re.search(r"<link>(.*?)</link>", block)
                            if t:
                                title = t.group(1).strip()
                                if title:
                                    results.append({
                                        "title":      title[:200],
                                        "url":        l.group(1).strip() if l else "",
                                        "source":     source_name,
                                        "published":  "",
                                        "votes_pos":  0,
                                        "votes_neg":  0,
                                        "currencies": [],
                                    })
            except Exception as e:
                logger.warning(f"RSS {source_name}: {e}")
        return results[:limit]

    # ═══════════════════════════════════════════════════════════
    # 2. تحليل بـ Groq (Llama 3.3 70B) — مجاني تماماً
    # ═══════════════════════════════════════════════════════════
    async def analyze(self, news_items: List[Dict],
                       symbols: List[str] = None) -> Dict:
        """
        يُحلل الأخبار بـ Groq/Llama 3.3 70B.
        Fallback تلقائي لـ rule-based عند غياب المفتاح.
        """
        if not news_items:
            return self._neutral_analysis()

        # إذا لا يوجد مفتاح Groq → rule-based مباشرة
        if not self.groq_key:
            logger.warning("⚠️ GROQ_API_KEY غير موجود — تحليل ذاتي")
            return self._rule_based_analysis(news_items)
        logger.info(f"🔑 Groq key: {self.groq_key[:4]}...{self.groq_key[-4:]} (طول={len(self.groq_key)})")

        import html as _html
        headlines = "\n".join(
            f"- {_html.unescape(item.get('title',''))[:120]} ({item.get('source','')})"
            for item in news_items[:15]
            if item.get("title")
        )
        coins_focus = ", ".join(symbols or ["BTC", "ETH"])

        prompt = f"""أنت محلل مالي خبير متخصص في أسواق الكريبتو وأسواق المال.
مهمتك: تحليل الأخبار التالية وتقييم تأثيرها على سوق الكريبتو.

الأخبار المُراد تحليلها:
{headlines}

العملات المحور: {coins_focus}

أعد ردك بصيغة JSON صحيح فقط, بدون أي نص خارجه:
{{
  "sentiment": "very_bullish|bullish|neutral|bearish|very_bearish",
  "sentiment_score": <رقم من -1.0 إلى 1.0>,
  "impact_level": "high|medium|low",
  "summary_ar": "<ملخص احترافي في 2-3 جمل عربية فقط — لا تستخدم كلمات إنجليزية في الجمل العربية>",
  "key_events": ["<حدث مهم 1>", "<حدث مهم 2>"],
  "affected_coins": ["BTC", "ETH"],
  "market_impact_ar": "<توقع التأثير على السوق في جملة واحدة>",
  "confidence": <رقم من 0.0 إلى 1.0>,
  "risk_flags": ["<خطر إن وجد>"]
}}"""

        # إنشاء session مؤقتة إذا لم تكن موجودة
        import aiohttp as _aio
        _temp_session = None
        if not self.session:
            _temp_session = _aio.ClientSession(
                headers={"User-Agent": "Mozilla/5.0 Chrome/124.0"},
                timeout=_aio.ClientTimeout(total=25),
            )
            self.session = _temp_session

        result = None  # إصلاح #458
        try:
            # محاولة Groq
            result = await self._call_groq(prompt, GROQ_MODEL)
            if result:
                result = _clean_news_result(result)
                result["source"] = f"groq/{GROQ_MODEL}"
                return result

            # Fallback: نموذج أصغر وأسرع
            result = await self._call_groq(prompt, GROQ_FALLBACK)
            if result:
                result = _clean_news_result(result)
                result["source"] = f"groq/{GROQ_FALLBACK}"
                return result
        finally:
            if _temp_session:
                await _temp_session.close()
                self.session = None

        # Fallback نهائي: rule-based
        return self._rule_based_analysis(news_items)

    async def _call_groq(self, prompt: str, model: str,
                          json_mode: bool = True) -> Optional[Dict]:
        """
        استدعاء Groq API.
        json_mode=False: يُعيد نصاً حراً مُغلَّفاً في {"text": "..."}
        إصلاح #274: analyze_symbol يستخدم json_mode=False
        """
        logger.info(f"Groq: استدعاء {model} | key={'✅' if self.groq_key else '❌ مفقود'}")
        try:
            import urllib.request
            import ssl

            # إصلاح #274: json_mode يتحكم في response_format
            sys_msg = ("أنت محلل مالي متخصص. أجب دائماً بـ JSON صحيح فقط بدون أي نص إضافي."
                       if json_mode else
                       # T30_fix: system prompt يتضمن اسم الأصل والسعر
                f"أنت خبير تحليل فني للأصل {symbol_name} (السعر الحالي: {_price_str}). "
                "أجب بنص عربي احترافي مباشر بدون JSON وبدون markdown. "
                f"ركز حصراً على {symbol_name} وأرقامه المرئية في الصورة.")
            req_body = {
                "model":       model,
                "messages":    [
                    {"role": "system", "content": sys_msg},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens":  800,
            }
            if json_mode:
                req_body["response_format"] = {"type": "json_object"}
            payload = json.dumps(req_body, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(
                GROQ_API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.groq_key}",
                    "Content-Type":  "application/json",
                    "User-Agent":    "RaedTradingAgent/2.0",
                },
                method="POST",
            )

            # تشغيل في executor لعدم حجب event loop
            loop = asyncio.get_event_loop()
            def _do_request():
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, context=ctx, timeout=25) as resp:
                    return resp.read().decode("utf-8")

            response_text = await loop.run_in_executor(None, _do_request)
            data    = json.loads(response_text)
            content = data["choices"][0]["message"]["content"].strip()

            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            # إصلاح #274: إذا json_mode=False → النتيجة نص حر
            if not json_mode:
                return {"summary_ar": content.strip(), "source": "groq_text"}
            # إصلاح #658: _gr بدلاً من result لتجنب UnboundLocalError في caller
            _gr = json.loads(content)
            if "sentiment" in _gr and "sentiment_score" in _gr:
                logger.info(f"✅ Groq نجح ({model}): sentiment={_gr.get('sentiment')}")
                return _gr
            _mapped = _normalize_groq_response(_gr)
            if _mapped:
                logger.info(f"✅ Groq (normalized) ({model}): sentiment={_mapped.get('sentiment')}")
                return _mapped
            else:
                logger.warning(f"Groq ({model}): JSON ناقص: {list(_gr.keys())}")

        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"Groq rate limit ({model})")
                await asyncio.sleep(3)
            elif e.code == 401:
                logger.error(f"Groq 401: مفتاح غير صالح — تحقق من GROQ_API_KEY")
            else:
                try:
                    err_body = e.read().decode("utf-8")[:200]
                    logger.error(f"Groq HTTP {e.code} ({model}): {err_body}")
                except Exception:
                    logger.error(f"Groq HTTP {e.code} ({model})")
        except urllib.error.URLError as e:
            logger.error(f"Groq URLError ({model}): {e.reason} — تحقق من الشبكة")
        except json.JSONDecodeError as e:
            logger.warning(f"Groq JSON error ({model}): {e}")
        except Exception as e:
            logger.error(f"Groq error ({model}): {type(e).__name__}: {e}")
        return None

    # ═══════════════════════════════════════════════════════════
    # 3. تحليل قائم على القواعد (fallback بدون API)
    # ═══════════════════════════════════════════════════════════
    def _rule_based_analysis(self, items: List[Dict]) -> Dict:
        """تحليل ذكي بالكلمات المفتاحية — يعمل بدون أي API."""
        bullish_kw = [
            "rally", "surge", "adoption", "bullish", "breakout", "approval",
            "etf", "institutional", "partnership", "launch", "milestone",
            "ارتفاع", "صعود", "نمو", "موافقة", "شراكة", "إطلاق",
        ]
        bearish_kw = [
            "crash", "ban", "hack", "exploit", "bearish", "dump", "sell",
            "regulatory", "lawsuit", "shutdown", "fraud", "scam", "warning",
            "انهيار", "حظر", "اختراق", "دعوى", "تحقيق", "تحذير", "احتيال",
        ]

        score = 0.0
        for item in items:
            import html as _html
            title  = _html.unescape(item.get("title", "")).lower()
            pos    = int(item.get("votes_pos") or 0)
            neg    = int(item.get("votes_neg") or 0)
            bull   = sum(1 for k in bullish_kw if k in title)
            bear   = sum(1 for k in bearish_kw if k in title)
            score += (bull - bear) * 0.15
            score += (pos  - neg)  * 0.005

        score = max(-1.0, min(1.0, score))

        if   score >  0.5: sentiment = "very_bullish"
        elif score >  0.1: sentiment = "bullish"
        elif score < -0.5: sentiment = "very_bearish"
        elif score < -0.1: sentiment = "bearish"
        else:              sentiment = "neutral"

        direction = "إيجابي" if score > 0 else "سلبي" if score < 0 else "محايد"

        return {
            "sentiment":       sentiment,
            "sentiment_score": round(score, 3),
            "impact_level":    "medium" if abs(score) > 0.2 else "low",
            "summary_ar":      (
                f"تحليل {len(items)} خبراً من المصادر العالمية — "
                f"مشاعر السوق {direction} بدرجة {abs(score):.2f}. "
                f"{'يُنصح بالحذر والمراقبة.' if score < -0.1 else 'السوق يُبدي إشارات إيجابية.' if score > 0.1 else 'السوق في حالة ترقب.'}"
            ),
            "key_events":      [_html.unescape(i.get("title", ""))[:120] for i in items[:3] if i.get("title")],
            "affected_coins":  ["BTC", "ETH"],
            "market_impact_ar": f"تأثير {direction} متوقع — يُنصح بالمراقبة",
            "confidence":      0.55,
            "risk_flags":      [],
            "source":          "rule_based",
        }

    def _neutral_analysis(self) -> Dict:
        return {
            "sentiment":       "neutral",
            "sentiment_score": 0.0,
            "impact_level":    "low",
            "summary_ar":      "لا أخبار مهمة متاحة في الوقت الحالي",
            "key_events":      [],
            "affected_coins":  [],
            "market_impact_ar": "لا تأثير واضح على السوق",
            "confidence":      0.3,
            "risk_flags":      [],
            "source":          "none",
        }

    # ═══════════════════════════════════════════════════════════
    # 4. تنسيق التقرير
    # ═══════════════════════════════════════════════════════════
    def format_ar(self, items: List[Dict], analysis: Dict) -> str:
        # T19_fix: تنسيق احترافي مُحسَّن
        sent_label, _ = SENTIMENT_LABELS.get(
            analysis.get("sentiment", "neutral"),
            ("⚪ محايد", 0.0)
        )
        impact_ar = {
            "high":   "🔴 عالي",
            "medium": "🟠 متوسط",
            "low":    "🟡 محدود",
        }.get(analysis.get("impact_level", "low"), "🟡")

        source = analysis.get("source", "—")
        source_label = (
            "🤖 Groq/Llama 3.3 70B" if "llama-3.3" in source
            else "🤖 Groq/Llama"    if "groq" in source
            else "📊 تحليل ذاتي"   if "rule" in source
            else source
        )

        _conf_news = analysis.get("confidence", 0)
        _bars_news = int(_conf_news * 10)
        _sentiment_bar = "█" * _bars_news + "░" * (10 - _bars_news)
        _n_sources = len(items) if items else 0

        # توصية مبنية على المشاعر
        _news_advice = (
            "💡 *توصية:* مراقبة فرص الشراء — الأخبار إيجابية"
            if "إيجابي" in sent_label else
            "⚠️ *توصية:* تقليل التعرض — أخبار سلبية قد تضغط"
            if "سلبي" in sent_label else
            "💡 *توصية:* أخبار محايدة — تابع المؤشرات التقنية"
        )

        # T19_fix: سيناريوهات سعرية بناءً على المشاعر
        _is_positive = "إيجابي" in sent_label
        _is_negative = "سلبي" in sent_label
        _scenarios = []
        if _is_positive:
            _scenarios = [
                "🟢 *صاعد:* إذا تأكدت الأخبار الإيجابية → ارتفاع +3-5%",
                "⚪ *محايد:* إذا تراجع تأثير الأخبار → تذبذب جانبي",
                "🔴 *هابط:* إذا تعارضت مع بيانات اقتصادية → انعكاس",
            ]
        elif _is_negative:
            _scenarios = [
                "🔴 *هابط:* إذا استمر ضغط الأخبار السلبية → هبوط -3-5%",
                "⚪ *محايد:* إذا تم تسعير الأخبار مسبقاً → استقرار",
                "🟢 *صاعد:* إذا تحسنت البيانات الاقتصادية → ارتداد",
            ]
        else:
            _scenarios = [
                "🟢 *صاعد:* إذا جاءت بيانات اقتصادية إيجابية",
                "⚪ *محايد:* السيناريو الأرجح — انتظار محفز واضح",
                "🔴 *هابط:* إذا تصاعدت التوترات التنظيمية",
            ]

        lines = [
            "📰 *تقرير الأخبار — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"المشاعر: {sent_label} | التأثير: {impact_ar}",
            f"الثقة: {_sentiment_bar} {_conf_news:.0%} | المصادر: {_n_sources}",
            "",
            "📋 *الملخص*",
            analysis.get("summary_ar", ""),
            "",
            "🎯 *التأثير المتوقع*",
            analysis.get("market_impact_ar", ""),
        ]

        import html as _html
        # T37b_fix: منطق تصنيف موحَّد مع word boundary (يحل مشكلة "sue" في "Issuer")
        import re as _re_news
        _neg_exact_news  = r"\b(ban|hack|crash|fraud|seized|jail|breach|ransom|arrest|scam|stolen|exploit|attack|lawsuit|penalty|fine|banned|shutdown|violation)\b"
        _neg_partial_news = ("sanction","exploit","hack","breach",
                              "حظر","عقوبة","اختراق","انهيار","احتيال","مصادرة","غرامة","عقوبات",
                              "استغلال","اختراق","سرقة","هجوم","احتيال")
        _neg_sue_news    = r"\bsues?\b"
        _pos_exact_news  = r"\b(approve|approved|launch|partner|etf|adopt|record|high|charter|trust|license|grant|bullish|surge|rally)\b"
        _pos_ar_news     = ("موافقة","إطلاق","شراكة","قياسي","تبنّي","ترخيص")
        def _news_impact(e):
            el = str(e).lower()
            if (_re_news.search(_neg_exact_news, el) or _re_news.search(_neg_sue_news, el)
                    or any(k in el for k in _neg_partial_news)):
                return (0, "🔴")
            if _re_news.search(_pos_exact_news, el) or any(k in str(e) for k in _pos_ar_news):
                return (2, "🟢")
            return (1, "🟡")

        events = analysis.get("key_events", [])
        if events:
            _sorted_ev = sorted(events[:6], key=lambda e: _news_impact(e)[0])
            lines += ["", "⚡ *الأحداث الرئيسية (مرتبة حسب التأثير)*"]
            lines += [
                f"{_news_impact(e)[1]} {_html.unescape(str(e)).replace('_',' ').replace('*','')[:100]}"
                for e in _sorted_ev if e
            ]

        flags = analysis.get("risk_flags", [])
        if flags:
            lines += ["", "⚠️ *تحذيرات*"]
            lines += [f"• {f}" for f in flags if f]

        # أخبار إضافية بدون تكرار
        # news_dedup_fix v2: المقارنة بالعناوين الإنجليزية الأصلية دائماً
        # لأن key_events قد تكون مُترجَمة لكن items إنجليزية دائماً
        _raw_titles_shown = set()
        # أضف عناوين items الأولى (key_events المصدر الإنجليزي) للـ shown
        for _item_raw in items[:len(analysis.get("key_events", []))]:
            _t_raw = str(_item_raw.get("title", "")).lower()
            if _t_raw:
                _raw_titles_shown.add(_t_raw[:40])
                _raw_titles_shown.add(_t_raw[:60])
                # كلمات رئيسية من الكيان (اسم الشركة/الشخصية)
                for _w in _t_raw.split()[:5]:
                    if len(_w) > 5:
                        _raw_titles_shown.add(_w)
        # أيضاً: أضف key_events مباشرة للمقارنة العربية
        for _ke in analysis.get("key_events", []):
            _ke_s = str(_ke).lower()
            _raw_titles_shown.add(_ke_s[:40])
        extra = []
        for item in items:
            t_orig = str(item.get("title", ""))
            # إصلاح #192: تخطي الأخبار غير الكريبتو
            if not _is_crypto_news(t_orig):
                continue
            _t_orig_low = t_orig.lower()
            # فحص التكرار بالعنوان الإنجليزي الأصلي
            _is_dup = (
                _t_orig_low[:40] in _raw_titles_shown or
                _t_orig_low[:60] in _raw_titles_shown or
                any(_w in _raw_titles_shown for _w in _t_orig_low.split()[:4] if len(_w) > 5)
            )
            if not _is_dup:
                t = _translate_news_title(t_orig)  # ترجمة M#59
                extra.append(t)
                _raw_titles_shown.add(_t_orig_low[:40])
            if len(extra) >= 4:
                break

        if extra:
            lines += ["", "📡 *أخبار إضافية*"]
            for t in extra:
                # V4b (#1345/#1347): رفع حد العنوان من 90 → 150 حرف
                title = t[:150]
                # فك HTML entities
                import html as _html_y2
                title = _html_y2.unescape(title)
                title = title.replace("_", " ").replace("*", "").replace("`", "")
                # Y2+T37_unify: توحيد منطق التصنيف مع key_events
                _prefix = _news_impact(title)[1] + " "
                lines.append(f"{_prefix}{title}")

        # T19_fix: إضافة سيناريوهات سعرية
        lines += ["", "📊 *السيناريوهات السعرية*"]
        lines += _scenarios

        # F2b: توصية رائد المحسّنة مع Sentiment Bar
        s_val = float(analysis.get("sentiment_score", 0) or 0)
        lines += ["", _news_advice]
        # تنبيه التعارض: أخبار إيجابية لكن السوق هابط
        _fear_val = analysis.get("fear_greed", 50) if isinstance(analysis.get("fear_greed"), int) else 50
        if s_val > 0.3 and _fear_val < 30:
            lines += [f"⚠️ *تنبيه:* مشاعر الأخبار إيجابية لكن السوق 🔴 اتجاه هابط (Fear & Greed: {_fear_val}) — لا تعتمد على الأخبار وحدها لاتخاذ القرار"]
        lines += ["", f"🔍 المصدر: {source_label} | 🤖 رائد التداول الذكي"]
        # ملاحظة: عناوين الأخبار بالإنجليزية من المصادر الدولية
        # ستُترجم تلقائياً بعد تفعيل Groq API في ملخص التحليل
        return "\n".join(lines)


    # ═══════════════════════════════════════════════════════════
    # تحليل عميق لعملة محددة
    # ═══════════════════════════════════════════════════════════
    async def analyze_symbol(self, symbol: str,
                              price: float, price_change_24h: float,
                              volume_24h: float, market_cap: float,
                              rsi: float, fear_greed: int,
                              regime_desc: str,
                              candles_summary: str = "",
                              trend: str = "",
                              **kwargs) -> str:
        """تحليل عميق للعملة — Groq مع fallback دائم."""
        if not self.groq_key:
            logger.warning(f"analyze_symbol ({symbol}): GROQ_API_KEY مفقود")
            return self._rule_based_symbol_analysis(
                symbol, price, price_change_24h, rsi, fear_greed, regime_desc)

                # إصلاح #274: prompt يطلب نصاً حراً — لا JSON
        _cs = (f" | {candles_summary}" if candles_summary else "")
        # الـ prompt الآن يستخدم السيناريوهات المُبنية من البيانات الحقيقية
        # candles_summary يحتوي على السيناريوهات الجاهزة من analysis.py
        # إصلاح #344/#368 (I3): EMA50 موحَّد + إصلاح #357/#362 (I4): RSI كـ int
        _ema_bearish = kwargs.get("ema_bearish", False)
        _ema_status  = "تحت EMA50" if _ema_bearish else "فوق EMA50"
        _rsi_int     = int(round(rsi))  # لا منازل عشرية في النص
        _is_perp_asset = any(x in symbol.upper() for x in ["SPCX","TSLA","AAPL","NVDA","MSFT","AMZN","GOOGL","META"])
        _asset_type  = "الأصل المُرمَّز" if _is_perp_asset else "العملة"
        # M2 (#1760/#1771/#1792/#1805): تمرير Market Phase الصحيح لـ Groq
        _mp_kwarg = kwargs.get("market_phase", "")
        _mp_ar_g  = _mp_kwarg if _mp_kwarg else (
            "هبوط" if "bear" in regime_desc.lower() or "هابط" in regime_desc else
            "صعود" if "bull" in regime_desc.lower() or "صاعد" in regime_desc else "تعزيز")

        prompt = (
            f"أنت محلل فني محايد متخصص في الكريبتو."
            f" البيانات: {_asset_type} {symbol} | السعر: ${price:,.4g}"
            f" | التغيير 24h: {price_change_24h:+.2f}%"
            f" | RSI: {_rsi_int} | Fear & Greed: {fear_greed}"
            f" | السوق: {regime_desc} | EMA50: {_ema_status}."
            f"{_cs}"
            f" [داخلي إلزامي: Market Phase = '{_mp_ar_g}' — يجب أن يُطابق هذا وصفك للسوق تماماً — لا تقل أي phase آخر]"
            f" [داخلي فقط: السعر {_ema_status} — عند ذكر EMA50 في التحليل استخدم 'السعر {_ema_status}' وليس 'EMA50 {_ema_status}']"
            " المطلوب: اكتب تحليلاً نصياً احترافياً باللغة العربية بدون markdown."
            " مهم: اكتب دائماً EMA5 و EMA20 و EMA50 بأحرف إنجليزية كبيرة."
            " مهم جداً: اكتب الأرقام بدون منازل عشرية زائدة (RSI=48 لا 48.5، EMA=1755 لا 1754.99)."
            " استخدم الأرقام والسيناريوهات الواردة أعلاه كمرجع."
            " اكتب 4-5 جمل تغطي:"
            " 1-الاتجاه العام وموقع SAR وMACD."
            " 2-تفسير السيناريوهات بأسلوب احترافي محايد."
            " 3-أي سيناريو أكثر احتمالاً ولماذا (بناءً على البيانات فقط)."
            " اكتب مباشرة بدون مقدمة ولا استنتاجات مطلقة."
            " مهم جداً: لا تقترح أي إجراء تداول (شراء/بيع/دخول/وقف)"
            " — التوصيات تصدر من النظام وليس من هذا النص."
            " اكتب فقط وصفاً تحليلياً محايداً للبيانات والسيناريوهات."
            " مهم: اكتب بالعربية الفصحى فقط بالكامل — لا تُدخل أي كلمات"
            " أو أحرف إنجليزية أو روسية أو أي لغة أخرى داخل الجمل العربية."
            " مهم: اكتب دائماً EMA5 و EMA20 و EMA50 و EMA200 بأحرف إنجليزية كبيرة."
            " مهم جداً: اكتب كل الأسعار والمستويات الرقمية بالأرقام"
            " الإنجليزية كما وردت في البيانات أعلاه حرفياً (مثل 0.074930)"
            " — ممنوع منعاً مطلقاً كتابة أي رقم أو سعر بالحروف العربية"
            " (مثل 'سبعين ألفاً' أو 'مائة وثلاثين') فهذا يُنتج قيماً"
            " خاطئة تماماً؛ الأرقام فقط بصيغتها العشرية كما هي."
        )


        result = None  # إصلاح #509
        try:
            # إصلاح #274: json_mode=False → نص حر مباشرةً
            result = await self._call_groq(prompt, GROQ_MODEL, json_mode=False)
            if result:
                # إصلاح #241: استخراج نص من أي مفتاح في الـ result
                text = ""
                # أولاً: المفاتيح المعيارية
                for k in ("summary_ar", "analysis", "market_impact_ar",
                          "تحليل", "الملخص", "ملخص"):
                    v = result.get(k, "")
                    if v and len(str(v)) > 20:
                        text = str(v)
                        break
                # ثانياً: أي قيمة نصية طويلة في الـ result
                if not text:
                    for k, v in result.items():
                        if isinstance(v, str) and len(v) > 30:
                            text = v
                            break
                        elif isinstance(v, dict):
                            # قيمة dict مثل {"دعم1": ..., "مقاومة1": ...}
                            # نُحوّلها لنص مقروء
                            sub_text = " | ".join(f"{sk}: {sv}"
                                                  for sk, sv in v.items()
                                                  if isinstance(sv, (str,int,float)))
                            if sub_text:
                                text = text + ("\n" if text else "") + sub_text
                if text and len(text) > 20:
                    text = _strip_mixed_language_artifacts(text, price=price)
                    text = _strip_header_duplicates(text)
                    # إصلاح #274: تصحيح "ل " المنقوصة ← "لـ {symbol} "
                    import re as _re
                    if f" ل ب" in text or "الحالي ل ب" in text:
                        text = text.replace(
                            "السوق الحالي ل ب",
                            f"السوق الحالي لـ {symbol} ب"
                        )
                    # EE6/GG1 (#1945/#1933/#2007): استبدال Market Phase برمجياً
                    _mp_kwarg_fix = kwargs.get("market_phase", "")
                    # GG1: إذا market_phase فارغ → نستنتجه من regime_desc
                    if not _mp_kwarg_fix and regime_desc:
                        if "هابط" in regime_desc or "bear" in regime_desc.lower():
                            _mp_kwarg_fix = "هبوط"
                        elif "صاعد" in regime_desc or "bull" in regime_desc.lower():
                            _mp_kwarg_fix = "صعود"
                        elif "تذبذب" in regime_desc or "sideways" in regime_desc.lower():
                            _mp_kwarg_fix = "تعزيز"
                        elif "تقلب" in regime_desc or "volatil" in regime_desc.lower():
                            _mp_kwarg_fix = "هبوط"
                    # GG2 (#1937/#2010/#2107): إصلاح Groq يحذف RSI من الجمل
                    import re as _re_gg2
                    text = _re_gg2.sub(r"و يبلغ ([0-9]+)", r"و RSI يبلغ \1", text)
                    text = _re_gg2.sub(r"، يبلغ ([0-9]+)", r"، حيث RSI يبلغ \1", text)

                    if _mp_kwarg_fix:
                        # استبدل أي phase مختلف يُذكره Groq بـ phase الصحيح
                        _phase_map = {
                            "صعود": ["صعود (Markup)", "Markup", "مرحلة صعود"],
                            "هبوط": ["هبوط (Markdown)", "Markdown", "مرحلة هبوط"],
                            "تراكم": ["تراكم (Accumulation)", "Accumulation"],
                            "توزيع": ["توزيع (Distribution)", "Distribution"],
                            "تعزيز": ["تعزيز (Consolidation)", "Consolidation"],
                        }
                        for _correct_phase, _wrong_variants in _phase_map.items():
                            if _correct_phase not in _mp_kwarg_fix:
                                continue
                            for _wrong in _phase_map:
                                if _wrong != _correct_phase:
                                    for _variant in _phase_map[_wrong]:
                                        if _variant in text:
                                            text = text.replace(_variant, _mp_kwarg_fix)
                    return text
        except Exception as _ae:
            logger.error(f"analyze_symbol ({symbol}): {_ae}")

        # fallback دائم — لا يرمي exception أبداً
        try:
            return self._rule_based_symbol_analysis(
                symbol, price, price_change_24h, rsi, fear_greed, regime_desc)
        except Exception as _e2:
            logger.error(f"rule_based ({symbol}): {_e2}")
            if price >= 1000:    p_str = f"${price:,.2f}"
            elif price >= 1:     p_str = f"${price:,.4f}"
            elif price >= 0.001: p_str = f"${price:.6f}"
            else:                p_str = f"${price:.8f}"
            return (f"📊 تحليل {symbol}\n"
                    f"السعر: {p_str} ({price_change_24h:+.2f}%)\n"
                    f"RSI: {rsi:.0f} | Fear & Greed: {fear_greed}\n"
                    f"السوق: {regime_desc}")

    async def detect_market_type_from_image(self, image_data: bytes) -> str:
        """
        R1 (#1024): تحديد نوع السوق من الصورة بسؤال مباشر وبسيط.
        يُستدعى قبل analyze_chart_image لتجاوز خطأ Groq في تصنيف Futures.
        يُعيد: 'futures' أو 'spot'
        """
        if not self.groq_key:
            return "spot"

        import base64, ssl, urllib.request, urllib.error, asyncio, json

        try:
            b64_image = base64.b64encode(image_data).decode("utf-8")
            ctx  = ssl.create_default_context()
            loop = asyncio.get_event_loop()

            # سؤال مباشر جداً — إجابة كلمة واحدة فقط
            detect_prompt = (
                "Look at this trading chart carefully. "
                "Is this a Futures/Perpetual contract chart or a Spot chart? "
                "Check for ANY of these indicators: "
                "Arabic text 'العقود الدائمة' or 'عقود آجلة', "
                "English text: Perp, Perpetual, Swap, USDT-SWAP, Mark Price, Funding Rate. "
                "Reply with ONLY one word: 'futures' or 'spot'. No explanation."
            )

            payload = json.dumps({
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                    {"type": "text", "text": detect_prompt}
                ]}],
                "temperature": 0.0, "max_tokens": 10,
            }, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(
                GROQ_API_URL, data=payload,
                headers={"Authorization": f"Bearer {self.groq_key}",
                         "Content-Type": "application/json",
                         "User-Agent": "RaedTradingAgent/3.0"},
                method="POST")

            resp = await loop.run_in_executor(
                None,
                lambda r=req: urllib.request.urlopen(r, context=ctx, timeout=15).read().decode())
            data    = json.loads(resp)
            answer  = data["choices"][0]["message"]["content"].strip().lower()
            result  = "futures" if "futures" in answer else "spot"
            logger.info(f"detect_market_type: '{answer}' → {result}")
            return result

        except Exception as e:
            logger.warning(f"detect_market_type error: {e}")
            return "spot"  # fallback آمن

    async def analyze_chart_image(self, image_data: bytes,
                                   symbol: str = "",
                                   current_price: float = 0.0) -> str:
        """تحليل صورة الشارت — نموذج Vision محدَّث مع fallback.
        T30_fix: symbol و current_price لمنع تحليل أصل خاطئ.
        """
        if not self.groq_key:
            return "⚠️ Groq API غير مُفعَّل — أضف GROQ_API_KEY في Railway"

        import base64, ssl, urllib.request, urllib.error, asyncio

        # إصلاح #551 (L5): تحديث نماذج Vision لأحدث المتاح في Groq
        # إصلاح P2 (#919/#920): حذف llama-3.2 المُوقَفة رسمياً من Groq
        # llama-3.2-90b-vision-preview → decommissioned
        # llama-3.2-11b-vision-preview → decommissioned (HTTP 400)
        # llama-4-scout يعمل ✅ (1022 حرف)
        # T11_fix v4: نماذج Groq Vision المدعومة (يوليو 2026)
        # محذوف: llava-v1.5, llava-v1.6, llama-3.2-90b, llama-4-maverick, llama-4-scout
        # مدعوم: Qwen3 Vision ✅
        VISION_MODELS = [
            "qwen/qwen3.6-27b",  # T11_fix: النموذج الوحيد المدعوم ✅ (Vision + Text)
        ]

        try:
            b64_image = base64.b64encode(image_data).decode("utf-8")
            sym_text  = f"لـ {symbol}" if symbol else ""
            # chart_price_prompt_fix: تعريف _price_str و _cp_marker قبل الاستخدام
            _price_str = f"${current_price:,.2f}" if current_price > 0 else "غير متوفر"
            # chart_px_prompt_fix v2: تعليمة مُحسَّنة للسعر
            if current_price > 0:
                # السعر معروف → نُخبر Qwen3 به مباشرة
                _px_instruction = (
                    f"السعر الحالي من OKX هو {_price_str}.\n"
                    f"PRICE_NOW:{current_price:.2f}\n"
                )
            else:
                # السعر غير معروف → Qwen3 يستخرجه من الشارت
                _px_instruction = (
                    "ابحث عن السعر الكبير الظاهر في أعلى يمين الشارت واكتبه هكذا في أول سطر:\n"
                    "PRICE_NOW:[السعر] مثال: PRICE_NOW:773.28\n"
                )
            prompt_text = (
                f"أنت خبير تحليل فني كريبتو. حلل شارت {sym_text} واكتب بالعربية فقط بدون تفكير.\n"
                f"{_px_instruction}"
                "اتبع هذا التنسيق بالضبط (14 قسم مرقم):\n"
                "0- نوع السوق: Spot أو Futures/Perp (تحقق من النص العربي: التداول الفوري=Spot | العقود الدائمة=Futures)\n"
                "1- الاتجاه العام وقوته: صاعد/هابط/جانبي مع السبب والأسعار الدقيقة\n"
                "2- مستويات الدعم والمقاومة: اذكر الأسعار الدقيقة من الشارت\n"
                "3- النماذج الفنية الظاهرة: ابحث عن أنماط شموع أو مخططات\n"
                "4- تحليل الشموع الأخيرة: اذكر ما تراه في آخر 3-5 شموع\n"
                "5- التوصية: شراء/بيع/انتظار مع السبب\n"
                "6- نقطة الدخول المقترحة بالسعر الدقيق\n"
                "7- وقف الخسارة بالسعر الدقيق\n"
                "8- هدف الربح الأول والثاني مع حساب R/R\n"
                "9- مستوى المخاطرة (نسبة مئوية من رأس المال)\n"
                "10- مؤشر RSI: القيمة أو اكتب لا يظهر\n"
                "11- مؤشر KDJ: القيم أو لا يظهر\n"
                "12- مؤشر MACD: DIF/DEA/Hist أو لا يظهر\n"
                "13- حجم الصفقة المقترح (Position Size)\n"
                "14- Market Phase: مرحلة السوق الحالية\n"
                "مهم: اذكر الأرقام الدقيقة من الشارت. لا تستخدم ** أو _ أو # أو markdown."
            )
            ctx  = ssl.create_default_context()
            loop = asyncio.get_event_loop()

            # T30_fix: تعريف symbol_name للـ prompt
            symbol_name = symbol.upper() if symbol else "الأصل المعروض"
            # _price_str مُعرَّف أعلاه في chart_price_prompt_fix
            _price_str_legacy = f"${current_price:,.4f}" if current_price > 0 else "غير متوفر"

            for model in VISION_MODELS:
                try:
                    payload = json.dumps({
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "أنت خبير تحليل فني. أجب مباشرة بالعربية بدون أي تفكير داخلي أو debugging. "
                                    "لا تستخدم <think> أو Step N أو Let me أو Wait. "
                                    "اكتب التحليل مباشرة بأرقام 1- 2- 3- فقط."
                                )
                            },
                            {"role": "user", "content": [
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                                {"type": "text", "text": prompt_text}
                            ]}
                        ],
                        "temperature": 0.1, "max_tokens": 1500,
                        # T11b_reasoning_fix: تعطيل reasoning في Qwen3
                        **({"reasoning_effort": "none"} if "qwen" in model.lower() else {}),
                    }, ensure_ascii=False).encode("utf-8")

                    req = urllib.request.Request(
                        GROQ_API_URL, data=payload,
                        headers={"Authorization": f"Bearer {self.groq_key}",
                                 "Content-Type": "application/json",
                                 "User-Agent": "RaedTradingAgent/3.0"},
                        method="POST")

                    resp = await loop.run_in_executor(
                        None,
                        lambda r=req: urllib.request.urlopen(r, context=ctx, timeout=60).read().decode())
                    data    = json.loads(resp)
                    content = data["choices"][0]["message"]["content"].strip()
                    # T11b_fix v2: تصفية <think>...</think> من Qwen3 (re.DOTALL)
                    import re as _re_qw
                    # T11b_fix v4: Qwen3 يضع كل التحليل داخل <think>
                    # الحل: استخراج محتوى <think> أولاً ثم تنظيفه
                    if "<think>" in content:
                        # استخراج ما داخل <think>
                        _inside = _re_qw.search(
                            r"<think>(.*?)(?:</think>|$)", content, flags=_re_qw.DOTALL)
                        if _inside:
                            _raw = _inside.group(1).strip()
                            # تنظيف: إزالة "Step N:" و"I see" وأسطر debug
                            _lines = [l.strip() for l in _raw.split("\n") if l.strip()]
                            # أخذ آخر 30 سطر (الخلاصة)
                            _summary = "\n".join(_lines[-30:]) if len(_lines) > 30 else "\n".join(_lines)
                            content = _summary if _summary else content
                        else:
                            content = _re_qw.sub(r"<think>.*", "", content, flags=_re_qw.DOTALL).strip()
                    # إزالة think خارجية إن وُجدت
                    content = _re_qw.sub(r"<think>.*?</think>", "", content, flags=_re_qw.DOTALL).strip()
                    content = _strip_markdown_headers(content)
                    logger.info(f"Groq Vision ({model}): {len(content)} حرف ✅")
                    return content

                except urllib.error.HTTPError as e:
                    err = e.read().decode()[:500]
                    logger.error(f"Groq Vision ({model}) HTTP {e.code}: {err[:200]}")
                    if e.code in (400, 404) and ("decommiss" in err.lower() or "not found" in err.lower()):
                        continue
                    if e.code in (401, 403):
                        return "❌ مفتاح Groq غير صحيح — تحقق من GROQ_API_KEY"
                    if e.code == 429:
                        return "⚠️ تجاوزت حد الطلبات — حاول بعد دقيقة"
                    continue
                except Exception as me:
                    logger.warning(f"Groq Vision ({model}): {me}")
                    continue

            return "❌ جميع نماذج تحليل الشارت غير متاحة حالياً. حاول لاحقاً"

        except Exception as e:
            logger.error(f"Groq Vision: {e}")
            return "❌ خطأ في تحليل الشارت. حاول لاحقاً"
    def _rule_based_symbol_analysis(self, symbol: str, price: float,
                                     change_24h: float, rsi: float,
                                     fear_greed: int, regime_desc: str,
                                     news_sentiment: float = 0.0) -> str:
        """تحليل rule-based احترافي — يستخدم كل مدارس التحليل الفني."""

        # 1. تحديد الاتجاه
        if regime_desc and "هابط" in regime_desc:
            trend = "هابط"
        elif regime_desc and "صاعد" in regime_desc:
            trend = "صاعد"
        else:
            trend = "صاعد" if change_24h > 2 else "هابط" if change_24h < -2 else "محايد"

        # 2. تفسير RSI بمناطق دقيقة
        rsi_text = _rsi_interpretation(rsi)

        # 3. تفسير Fear & Greed
        fg_text = _fear_greed_interpretation(fear_greed)

        # 4. تحليل التناقض
        contradiction = _contradiction_analysis(rsi, fear_greed, regime_desc, news_sentiment)

        # 5. تنسيق السعر
        if price >= 1000:    p_str = f"${price:,.2f}"
        elif price >= 1:     p_str = f"${price:,.4f}"
        elif price >= 0.001: p_str = f"${price:.6f}"
        else:                p_str = f"${price:.8f}"

        # 6. بناء التحليل
        # إصلاح #175: حذف سطور الهيدر — cmd_analyze يعرضها بالفعل
        # نبدأ مباشرةً من التحليل النوعي بدون تكرار البيانات
        lines = [
            f"RSI {rsi:.0f}: {rsi_text}",
            f"Fear & Greed {fear_greed}: {fg_text}",
        ]

        # 7. إضافة تحليل التناقض إذا وُجد
        if contradiction:
            lines.append("")
            lines.append("📊 *تحليل المؤشرات المتقاطعة*")
            lines.extend(contradiction.split("\n"))

        return "\n".join(lines)



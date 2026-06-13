"""
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
    """يحذف headers الـ Markdown."""
    if not text: return text
    lines = text.split("\n")
    clean = [l for l in lines if not l.startswith("###") and not l.startswith("####")]
    return "\n".join(clean).strip()


def _strip_mixed_language_artifacts(text: str, price: float = None) -> str:
    """
    إصلاح #94: شبكة أمان حتمية فوق تعليمة الـprompt — تُزيل كلمات/أحرف
    إنجليزية مدمجة وسط النص العربي (مثل "وraises احتمال" -> "و احتمال").
    لا تؤثر على الأرقام أو رموز العملات (BTC, RSI, EMA...) المُحاطة بمسافات.
    """
    if not text:
        return text
    import re
    # كلمة إنجليزية مباشرة بعد حرف عربي بدون مسافة (و/ف/ب/ك + ASCII letters)
    text = re.sub(r'(?<=[\u0600-\u06FF])([a-zA-Z]{2,})(?=[\u0600-\u06FF\s])', ' ', text)
    # كلمة إنجليزية مباشرة قبل حرف عربي بدون مسافة
    text = re.sub(r'([a-zA-Z]{2,})(?=[\u0600-\u06FF])', r'\1 ', text)
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
  "summary_ar": "<ملخص احترافي في 2-3 جمل عربية>",
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
                       "أنت خبير تحليل فني. أجب بنص عربي احترافي مباشر بدون JSON وبدون markdown.")
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
            "key_events":      [_html.unescape(i.get("title", ""))[:80] for i in items[:3] if i.get("title")],
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
        sent_label, _ = SENTIMENT_LABELS.get(
            analysis.get("sentiment", "neutral"),
            ("⚪ محايد", 0.0)
        )
        impact_ar = {
            "high":   "🔴 كبير",
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

        lines = [
            "📰 *تقرير الأخبار — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"المشاعر: {sent_label}",
            f"التأثير: {impact_ar}",
            f"الثقة:   {analysis.get('confidence', 0):.0%}",
            "",
            "📋 *الملخص*",
            analysis.get("summary_ar", ""),
            "",
            "🎯 *التأثير المتوقع*",
            analysis.get("market_impact_ar", ""),
        ]

        import html as _html
        events = analysis.get("key_events", [])
        if events:
            lines += ["", "⚡ *الأحداث الرئيسية*"]
            lines += [
                f"• {_html.unescape(str(e)).replace('_',' ').replace('*','')[:100]}"
                for e in events[:4] if e
            ]

        flags = analysis.get("risk_flags", [])
        if flags:
            lines += ["", "⚠️ *تحذيرات*"]
            lines += [f"• {f}" for f in flags if f]

        # أخبار إضافية بدون تكرار
        shown = {str(e)[:50].lower() for e in analysis.get("key_events", [])}
        extra = []
        for item in items:
            t = str(item.get("title", ""))
            # إصلاح #192: تخطي الأخبار غير الكريبتو
            if not _is_crypto_news(t):
                continue
            t = _translate_news_title(t)  # ترجمة M#59
            if t[:50].lower() not in shown and t:
                extra.append(t)
            if len(extra) >= 4:
                break

        if extra:
            lines += ["", "📡 *أخبار إضافية*"]
            for t in extra:
                title = t[:90]
                # فك HTML entities
                import html
                title = html.unescape(title)
                title = title.replace("_", " ").replace("*", "").replace("`", "")
                lines.append(f"• {title}")

        # توصية تداول بناءً على مشاعر الأخبار (M#59)
        s_val = float(analysis.get("sentiment_score", 0) or 0)
        if s_val > 0.3:
            lines += ["", "💡 *توصية رائد:* مراقبة فرص الشراء — الأخبار إيجابية"]
        elif s_val < -0.3:
            lines += ["", "💡 *توصية رائد:* انتظار — أخبار سلبية تستدعي الحذر"]
        else:
            lines += ["", "💡 *توصية رائد:* انتظار وترقب — السوق محايد"]
        lines += ["", f"🔍 المصدر: {source_label} | رائد التداول الذكي"]
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
        prompt = (
            f"أنت محلل فني محايد متخصص في الكريبتو."
            f" البيانات: {symbol} | السعر: ${price:,.4g}"
            f" | التغيير 24h: {price_change_24h:+.2f}%"
            f" | RSI: {rsi:.0f} | Fear & Greed: {fear_greed}"
            f" | السوق: {regime_desc}."
            f"{_cs}"
            " المطلوب: اكتب تحليلاً نصياً احترافياً باللغة العربية بدون markdown."
            " استخدم الأرقام والسيناريوهات الواردة أعلاه كمرجع."
            " اكتب 4-5 جمل تغطي:"
            " 1-الاتجاه العام وموقع SAR وMACD."
            " 2-تفسير السيناريوهات بأسلوب احترافي محايد."
            " 3-أي سيناريو أكثر احتمالاً ولماذا (بناءً على البيانات فقط)."
            " اكتب مباشرة بدون مقدمة ولا استنتاجات مطلقة."
            " مهم: اكتب بالعربية الفصحى فقط بالكامل — لا تُدخل أي كلمات"
            " أو أحرف إنجليزية داخل الجمل العربية (مثل دمج كلمة إنجليزية"
            " مباشرة مع كلمة عربية بدون فراغ أو ترجمة)."
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

    async def analyze_chart_image(self, image_data: bytes,
                                   symbol: str = "") -> str:
        """تحليل صورة الشارت — نموذج Vision محدَّث مع fallback."""
        if not self.groq_key:
            return "⚠️ Groq API غير مُفعَّل — أضف GROQ_API_KEY في Railway"

        import base64, ssl, urllib.request, urllib.error, asyncio

        VISION_MODELS = [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
        ]

        try:
            b64_image = base64.b64encode(image_data).decode("utf-8")
            sym_text  = f"لـ {symbol}" if symbol else ""
            prompt_text = (
                f"أنت خبير تحليل فني للكريبتو. أجب بنص عربي واضح بدون markdown headers (###) أو bullet lists. استخدم الأرقام 1- 2- فقط. حلل شارت {sym_text} بدقة باللغة العربية."
                " أجب بنص عربي واضح ومنظم بدون markdown headers (###) أو (####) أو bullet lists."
                " استخدم الترقيم فقط: 1- 2- 3- إلخ. اذكر الأسعار الدقيقة."
                " الأقسام المطلوبة:"
                " 1-الاتجاه العام وقوته"
                " 2-مستويات الدعم والمقاومة بالأسعار الدقيقة"
                " 3-النماذج الفنية الظاهرة"
                " 4-تحليل الشموع الأخيرة"
                " 5-التوصية: شراء/بيع/انتظار"
                " 6-نقطة الدخول المقترحة"
                " 7-وقف الخسارة"
                " 8-هدف الربح الأول والثاني"
                " 9-مستوى المخاطرة (نسبة مئوية من رأس المال)"
            )
            ctx  = ssl.create_default_context()
            loop = asyncio.get_event_loop()

            for model in VISION_MODELS:
                try:
                    payload = json.dumps({
                        "model": model,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                            {"type": "text", "text": prompt_text}
                        ]}],
                        "temperature": 0.1, "max_tokens": 1500,
                    }, ensure_ascii=False).encode("utf-8")

                    req = urllib.request.Request(
                        GROQ_API_URL, data=payload,
                        headers={"Authorization": f"Bearer {self.groq_key}",
                                 "Content-Type": "application/json",
                                 "User-Agent": "RaedTradingAgent/3.0"},
                        method="POST")

                    resp = await loop.run_in_executor(
                        None,
                        lambda r=req: urllib.request.urlopen(r, context=ctx, timeout=30).read().decode())
                    data    = json.loads(resp)
                    content = data["choices"][0]["message"]["content"].strip()
                    content = _strip_markdown_headers(content)
                    logger.info(f"Groq Vision ({model}): {len(content)} حرف ✅")
                    return content

                except urllib.error.HTTPError as e:
                    err = e.read().decode()[:300]
                    logger.warning(f"Groq Vision ({model}) HTTP {e.code}: {err[:80]}")
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



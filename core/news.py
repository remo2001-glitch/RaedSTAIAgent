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
GROQ_FALLBACK  = "llama3-8b-8192"             # fallback أسرع

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
        if not self.groq_key or not self.session:
            return self._rule_based_analysis(news_items)

        headlines = "\n".join(
            f"- {item.get('title','')[:120]} ({item.get('source','')})"
            for item in news_items[:15]
            if item.get("title")
        )
        coins_focus = ", ".join(symbols or ["BTC", "ETH"])

        prompt = f"""أنت محلل مالي خبير متخصص في أسواق الكريبتو وأسواق المال.
مهمتك: تحليل الأخبار التالية وتقييم تأثيرها على سوق الكريبتو.

الأخبار المُراد تحليلها:
{headlines}

العملات المحور: {coins_focus}

أعد ردك بصيغة JSON صحيح فقط، بدون أي نص خارجه:
{{
  "sentiment": "very_bullish|bullish|neutral|bearish|very_bearish",
  "sentiment_score": <رقم من -1.0 إلى 1.0>,
  "impact_level": "high|medium|low",
  "summary_ar": "<ملخص احترافي في ٢-٣ جمل عربية>",
  "key_events": ["<حدث مهم ١>", "<حدث مهم ٢>"],
  "affected_coins": ["BTC", "ETH"],
  "market_impact_ar": "<توقع التأثير على السوق في جملة واحدة>",
  "confidence": <رقم من 0.0 إلى 1.0>,
  "risk_flags": ["<خطر إن وجد>"]
}}"""

        # محاولة Groq
        result = await self._call_groq(prompt, GROQ_MODEL)
        if result:
            result["source"] = f"groq/{GROQ_MODEL}"
            return result

        # Fallback: نموذج أصغر وأسرع
        result = await self._call_groq(prompt, GROQ_FALLBACK)
        if result:
            result["source"] = f"groq/{GROQ_FALLBACK}"
            return result

        # Fallback نهائي: rule-based
        return self._rule_based_analysis(news_items)

    async def _call_groq(self, prompt: str, model: str) -> Optional[Dict]:
        """استدعاء Groq API مع معالجة أخطاء محكمة."""
        try:
            payload = {
                "model":    model,
                "messages": [
                    {
                        "role":    "system",
                        "content": "أنت محلل مالي متخصص. أجب دائماً بـ JSON صحيح فقط بدون أي نص إضافي."
                    },
                    {
                        "role":    "user",
                        "content": prompt,
                    }
                ],
                "temperature":     0.1,
                "max_tokens":      800,
                "response_format": {"type": "json_object"},
            }
            headers = {
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type":  "application/json",
            }
            async with self.session.post(
                GROQ_API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    data    = await r.json()
                    content = data["choices"][0]["message"]["content"]
                    # تنظيف الـ JSON
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    result = json.loads(content)
                    # تحقق من الحقول الإلزامية
                    if "sentiment" in result and "sentiment_score" in result:
                        return result
                elif r.status == 429:
                    logger.warning(f"Groq rate limit ({model})")
                    await asyncio.sleep(3)
                else:
                    logger.warning(f"Groq error {r.status} ({model})")
        except json.JSONDecodeError as e:
            logger.warning(f"Groq JSON parse error ({model}): {e}")
        except Exception as e:
            logger.warning(f"Groq call error ({model}): {e}")
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
            title  = item.get("title", "").lower()
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
            "summary_ar":      f"تحليل {len(items)} خبراً — مشاعر السوق {direction} بدرجة {abs(score):.2f}",
            "key_events":      [i.get("title", "")[:80] for i in items[:3] if i.get("title")],
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

        events = analysis.get("key_events", [])
        if events:
            lines += ["", "⚡ *الأحداث الرئيسية*"]
            lines += [
                f"• {str(e).replace('_',' ').replace('*','')[:100]}"
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
            if t[:50].lower() not in shown and t:
                extra.append(t)
            if len(extra) >= 4:
                break

        if extra:
            lines += ["", "📡 *أخبار إضافية*"]
            for t in extra:
                title = t[:90].replace("_", " ").replace("*", "").replace("`", "")
                lines.append(f"• {title}")

        lines += ["", f"🔍 المصدر: {source_label} | رائد التداول الذكي"]
        return "\n".join(lines)


# Singleton
news_engine = NewsEngine()

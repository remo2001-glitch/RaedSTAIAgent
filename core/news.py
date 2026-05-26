"""
📰 رائد — News Analysis Engine
يجمع الأخبار من CryptoPanic + RSS ويحللها بـ Gemini Flash
يُنتج: درجة المشاعر · التأثير المتوقع · ملخص عربي احترافي
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# مصادر RSS المجانية الموثوقة
RSS_SOURCES = [
    ("CoinTelegraph",  "https://cointelegraph.com/rss"),
    ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",        "https://decrypt.co/feed"),
]

SENTIMENT_LABELS = {
    "very_bullish":  ("🟢🟢 إيجابي جداً",  1.0),
    "bullish":       ("🟢 إيجابي",          0.65),
    "neutral":       ("⚪ محايد",           0.0),
    "bearish":       ("🔴 سلبي",           -0.65),
    "very_bearish":  ("🔴🔴 سلبي جداً",    -1.0),
}


class NewsEngine:
    def __init__(self, gemini_key: str = "", session: Optional[aiohttp.ClientSession] = None):
        self.gemini_key = gemini_key
        self.session    = session
        self._cache: Dict[str, Tuple[List, float]] = {}
        self._cache_ttl = 300   # 5 دقائق

    # ═══════════════════════════════════════════════════════════
    # 1. جمع الأخبار
    # ═══════════════════════════════════════════════════════════
    async def fetch_news(self, symbols: List[str] = None,
                          limit: int = 20) -> List[Dict]:
        cache_key = ",".join(sorted(symbols or ["BTC","ETH"]))
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        items = []

        # ── CryptoPanic ──
        try:
            currencies = ",".join(symbols or ["BTC","ETH","BNB"])
            url = "https://cryptopanic.com/api/v1/posts/"
            params = {"public": "true", "currencies": currencies,
                      "filter": "important", "kind": "news"}
            async with self.session.get(url, params=params,
                                         timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json()
                    for post in data.get("results", [])[:limit]:
                        items.append({
                            "title":      post.get("title", ""),
                            "url":        post.get("url", ""),
                            "source":     post.get("source", {}).get("title", ""),
                            "published":  post.get("created_at", ""),
                            "votes_pos":  post.get("votes", {}).get("positive", 0),
                            "votes_neg":  post.get("votes", {}).get("negative", 0),
                            "currencies": [c["code"] for c in post.get("currencies", [])],
                        })
        except Exception as e:
            logger.warning(f"CryptoPanic fetch error: {e}")

        # ── RSS fallback ──
        if len(items) < 5:
            rss_items = await self._fetch_rss(limit=15)
            items.extend(rss_items)

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
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        text = await r.text()
                        for block in re.findall(r"<item>(.*?)</item>", text, re.DOTALL)[:5]:
                            title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block)
                            link_m  = re.search(r"<link>(.*?)</link>", block)
                            if title_m:
                                results.append({
                                    "title":     title_m.group(1).strip(),
                                    "url":       link_m.group(1).strip() if link_m else "",
                                    "source":    source_name,
                                    "published": "",
                                    "votes_pos": 0, "votes_neg": 0,
                                    "currencies": [],
                                })
            except Exception as e:
                logger.warning(f"RSS {source_name} error: {e}")
        return results[:limit]

    # ═══════════════════════════════════════════════════════════
    # 2. تحليل بـ Gemini
    # ═══════════════════════════════════════════════════════════
    async def analyze(self, news_items: List[Dict],
                       symbols: List[str] = None) -> Dict:
        """
        يُحلل الأخبار ويُعيد:
        sentiment_score, impact_level, summary_ar, affected_coins, key_events
        """
        if not news_items:
            return self._neutral_analysis()

        headlines = "\n".join(
            f"- {item['title']} ({item.get('source','')})"
            for item in news_items[:15]
        )
        coins_focus = ", ".join(symbols or ["BTC", "ETH"])

        prompt = f"""أنت محلل مالي متخصص في أسواق الكريبتو.
حلّل هذه الأخبار واستخرج:

الأخبار:
{headlines}

أعد ردك بصيغة JSON فقط، بدون أي نص خارج الـ JSON:
{{
  "sentiment": "very_bullish|bullish|neutral|bearish|very_bearish",
  "sentiment_score": رقم من -1 إلى 1,
  "impact_level": "high|medium|low",
  "summary_ar": "ملخص عربي احترافي في ٣-٤ جمل",
  "key_events": ["حدث مهم ١", "حدث مهم ٢"],
  "affected_coins": ["BTC", "ETH"],
  "market_impact_ar": "توقع تأثير السوق في جملة واحدة",
  "confidence": رقم من 0 إلى 1,
  "risk_flags": ["خطر ١ إن وجد"]
}}"""

        if not self.gemini_key:
            return self._rule_based_analysis(news_items)

        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 800,
                    "responseMimeType": "application/json",
                }
            }
            url = f"{GEMINI_API_URL}?key={self.gemini_key}"
            async with self.session.post(url, json=payload,
                                          timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data     = await r.json()
                    text     = data["candidates"][0]["content"]["parts"][0]["text"]
                    analysis = json.loads(text)
                    analysis["source"] = "gemini"
                    return analysis
                else:
                    logger.warning(f"Gemini API error: {r.status}")
        except Exception as e:
            logger.warning(f"Gemini analysis error: {e}")

        return self._rule_based_analysis(news_items)

    def _safe_analyze(self, news_items: List[Dict],
                       symbols: List[str] = None) -> Dict:
        """wrapper آمن يُعيد neutral عند أي استثناء."""
        try:
            return self._rule_based_analysis(news_items or [])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"_safe_analyze: {e}")
            return self._neutral_analysis()

    def _rule_based_analysis(self, items: List[Dict]) -> Dict:
        """تحليل قائم على الكلمات المفتاحية عند غياب Gemini."""
        bullish_kw = ["rally", "surge", "adoption", "bullish", "breakout",
                      "approval", "etf", "institutional", "ارتفاع", "صعود"]
        bearish_kw = ["crash", "ban", "hack", "exploit", "bearish", "dump",
                      "sell", "regulatory", "انهيار", "حظر", "اختراق"]

        score = 0.0
        for item in items:
            title = item.get("title", "").lower()
            pos   = item.get("votes_pos", 0)
            neg   = item.get("votes_neg", 0)
            bull  = sum(1 for k in bullish_kw if k in title)
            bear  = sum(1 for k in bearish_kw if k in title)
            score += (bull - bear) * 0.15
            score += (pos - neg) * 0.005

        score = max(-1.0, min(1.0, score))

        if score > 0.5:    sentiment = "bullish"
        elif score > 0.1:  sentiment = "bullish"
        elif score < -0.5: sentiment = "bearish"
        elif score < -0.1: sentiment = "bearish"
        else:              sentiment = "neutral"

        return {
            "sentiment":        sentiment,
            "sentiment_score":  round(score, 3),
            "impact_level":     "medium",
            "summary_ar":       f"تحليل {len(items)} خبراً — درجة المشاعر: {score:+.2f}",
            "key_events":       [i["title"][:80] for i in items[:3]],
            "affected_coins":   ["BTC", "ETH"],
            "market_impact_ar": "تأثير محايد مع ميل " + ("إيجابي" if score > 0 else "سلبي"),
            "confidence":       0.5,
            "risk_flags":       [],
            "source":           "rule_based",
        }

    def _neutral_analysis(self) -> Dict:
        return {
            "sentiment": "neutral", "sentiment_score": 0.0,
            "impact_level": "low",
            "summary_ar": "لا أخبار مهمة متاحة في الوقت الحالي",
            "key_events": [], "affected_coins": [],
            "market_impact_ar": "لا تأثير واضح",
            "confidence": 0.3, "risk_flags": [], "source": "none",
        }

    # ═══════════════════════════════════════════════════════════
    # 3. تنسيق التقرير
    # ═══════════════════════════════════════════════════════════
    def format_ar(self, items: List[Dict], analysis: Dict) -> str:
        sent_label, _ = SENTIMENT_LABELS.get(
            analysis.get("sentiment", "neutral"),
            ("⚪ محايد", 0.0)
        )
        impact_ar = {"high": "🔴 كبير", "medium": "🟠 متوسط",
                     "low": "🟡 محدود"}.get(analysis.get("impact_level","low"), "🟡")

        lines = [
            "📰 *تقرير الأخبار — رائد*",
            "━━━━━━━━━━━━━━━━━━",
            f"المشاعر: {sent_label}",
            f"التأثير: {impact_ar}",
            f"الثقة:   {analysis.get('confidence',0):.0%}",
            "",
            f"📋 *الملخص*",
            analysis.get("summary_ar", ""),
            "",
            f"🎯 *التأثير المتوقع*",
            analysis.get("market_impact_ar", ""),
        ]

        events = analysis.get("key_events", [])
        if events:
            lines += ["", "⚡ *الأحداث الرئيسية*"]
            lines += [f"• {str(e).replace('_',' ').replace('*','')[:100]}" for e in events[:4]]

        flags = analysis.get("risk_flags", [])
        if flags:
            lines += ["", "⚠️ *تحذيرات*"]
            lines += [f"• {f}" for f in flags]

        # عرض الأخبار الإضافية فقط (غير المذكورة في key_events)
        shown_titles = set()
        events_list  = analysis.get("key_events", [])
        for e in events_list:
            shown_titles.add(str(e)[:50].lower())

        extra_items = []
        for item in items:
            t = str(item.get("title",""))
            if t[:50].lower() not in shown_titles:
                extra_items.append(t)
            if len(extra_items) >= 4:
                break

        if extra_items:
            lines += ["", f"📡 *أخبار إضافية*"]
            for t in extra_items:
                title = t[:90].replace('_',' ').replace('*','').replace('`','')
                lines.append(f"• {title}")

        lines += ["", f"🔍 المصدر: {analysis.get('source','—')} | "
                      f"رائد التداول الذكي"]
        return "\n".join(lines)


news_engine = NewsEngine()

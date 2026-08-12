"""
auditing_agent.py — وكيل التدقيق الإلزامي
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
يُدقق جميع المخرجات من أي Agent قبل إرسالها للمستخدم.
لا يصل أي محتوى للمستخدم بدون المرور بهذا الوكيل.

المعايير:
  1. جودة المحتوى (لا جمل فارغة أو متكررة)
  2. الأرقام والبيانات (قابلة للتحقق)
  3. التحفظات القانونية (لا توصيات صريحة)
  4. التعارضات المنطقية
  5. أمان المحتوى (لا بيانات حساسة)
"""
import re
import logging
from typing import Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# معايير التدقيق
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# أنماط المحتوى الخطير
_DANGEROUS_PATTERNS = [
    r"اضمن لك|مضمون \d+%|ربح مؤكد|لا خسارة",
    r"استثمر الآن|اشتر الآن فوراً|فرصة العمر",
    r"api[_-]?key|secret[_-]?key|password|passphrase",
    r"0x[0-9a-fA-F]{40}",  # Ethereum addresses
]

# أنماط المحتوى الفارغ
_EMPTY_PATTERNS = [
    r"^[\s\-_=\.]*$",
    r"^(N/A|null|none|undefined|nan)$",
]

# fallback للمحتوى المرفوض
_FALLBACK_MESSAGES = {
    "outlook": (
        "🌍 رؤية المؤسسات الكبرى — رائد\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ جاري تحديث البيانات من المصادر الرسمية...\n\n"
        "💡 للاستفادة من آخر التوقعات:\n"
        "• BlackRock: blackrock.com/insights\n"
        "• Vanguard: vanguard.com/research\n"
        "• Morningstar: morningstar.com/research\n\n"
        "⚠️ رأي استرشادي — القرار النهائي للمستخدم\n"
        "🤖 رائد التداول الذكي"
    ),
    "signal": "⚠️ تعذّر التحقق من صحة الإشارة — أعد المحاولة",
    "analyze": "⚠️ تعذّر إكمال التحليل — أعد المحاولة",
    "plan": "⚠️ تعذّر إنشاء الخطة — أعد المحاولة",
    "review": "⚠️ تعذّر إنشاء التقرير — أعد المحاولة",
    "default": "⚠️ المحتوى قيد المراجعة — أعد المحاولة",
}

# الإخلاء القانوني الإلزامي
_LEGAL_DISCLAIMER = "\n\n⚠️ هذا المحتوى استرشادي — القرار النهائي للمستخدم"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# فئة Auditing Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AuditingAgent:
    """
    وكيل التدقيق الإلزامي — يُدقق جميع المخرجات قبل إرسالها.
    
    الاستخدام:
        auditor = AuditingAgent()
        approved, content = auditor.audit(content, source="outlook")
        if approved:
            await msg.reply_text(content)
        else:
            await msg.reply_text(content)  # fallback
    """
    
    def __init__(self):
        self._audit_count = 0
        self._reject_count = 0
        self._start_time = datetime.now(timezone.utc)
    
    def audit(
        self,
        content: str,
        source: str = "default",
        require_numbers: bool = False,
        require_disclaimer: bool = True,
        min_length: int = 20,
        max_length: int = 4096,
    ) -> Tuple[bool, str]:
        """
        تدقيق المحتوى.
        
        Returns:
            (True, content)  → محتوى موافق عليه
            (False, fallback) → محتوى مرفوض + fallback
        """
        self._audit_count += 1
        
        try:
            # 1. فحص أساسي
            if not content or not isinstance(content, str):
                return self._reject(source, "محتوى فارغ أو غير صالح")
            
            content = content.strip()
            
            # 2. فحص الطول
            if len(content) < min_length:
                return self._reject(source, f"محتوى قصير جداً ({len(content)} حرف)")
            
            if len(content) > max_length:
                content = content[:max_length-100] + "\n...\n⚠️ اقتُطع المحتوى"
            
            # 3. فحص المحتوى الخطير
            content_lower = content.lower()
            for pattern in _DANGEROUS_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    return self._reject(source, f"محتوى خطير: {pattern[:30]}")
            
            # 4. فحص الجمل الفارغة المتكررة
            lines = [l.strip() for l in content.split("\n") if l.strip()]
            if len(lines) < 2:
                return self._reject(source, "محتوى غير كافٍ")
            
            # فحص التكرار: إذا أكثر من 50% من الجمل متكررة
            unique_lines = set(lines)
            if len(lines) > 4 and len(unique_lines) / len(lines) < 0.5:
                return self._reject(source, "محتوى متكرر بشكل مفرط")
            
            # 5. فحص الأرقام إذا مطلوب
            if require_numbers:
                has_numbers = bool(re.search(r"\d+\.?\d*[%$]|\$\d+|\d+\.\d+", content))
                if not has_numbers:
                    return self._reject(source, "لا أرقام أو بيانات قابلة للتحقق")
            
            # 6. فحص التضارب المنطقي (صعود + هبوط في نفس الجملة)
            if self._has_logical_conflict(content):
                logger.warning(f"auditing_agent: تضارب منطقي في {source}")
                # لا نرفض — نسجّل فقط
            
            # 7. إضافة الإخلاء القانوني إذا غائب
            if require_disclaimer and _LEGAL_DISCLAIMER.strip() not in content:
                if "استرشادي" not in content and "للمستخدم" not in content:
                    content = content + _LEGAL_DISCLAIMER
            
            # ✅ موافق
            logger.debug(f"auditing_agent: ✅ {source} ({len(content)} حرف)")
            return True, content
            
        except Exception as e:
            logger.error(f"auditing_agent error: {e}")
            return self._reject(source, f"خطأ في التدقيق: {e}")
    
    def audit_financial(self, content: str, source: str = "signal") -> Tuple[bool, str]:
        """تدقيق مالي متشدد للإشارات والتحليلات"""
        return self.audit(
            content,
            source=source,
            require_numbers=True,
            require_disclaimer=True,
            min_length=50,
        )
    
    def audit_outlook(self, content: str) -> Tuple[bool, str]:
        """تدقيق رؤية المؤسسات"""
        # /outlook لا يشترط أرقاماً صارمة لكن يشترط جودة
        approved, result = self.audit(
            content,
            source="outlook",
            require_numbers=False,
            require_disclaimer=True,
            min_length=100,
        )
        if not approved:
            return False, _FALLBACK_MESSAGES["outlook"]
        
        # تحقق إضافي: هل يذكر المؤسسات الثلاث؟
        mentions = sum(1 for inst in ["BlackRock", "Vanguard", "Morningstar"] 
                      if inst in content)
        if mentions < 2:
            logger.warning("auditing_agent: /outlook لا يذكر المؤسسات")
            return False, _FALLBACK_MESSAGES["outlook"]
        
        return True, result
    
    def _has_logical_conflict(self, content: str) -> bool:
        """فحص التضارب المنطقي"""
        bull_signals = len(re.findall(r"صاعد|شراء|ارتفاع|إيجابي", content))
        bear_signals = len(re.findall(r"هابط|بيع|انخفاض|سلبي", content))
        # تضارب شديد: أكثر من 3 إشارات لكل اتجاه في نفس النص
        return bull_signals > 3 and bear_signals > 3
    
    def _reject(self, source: str, reason: str) -> Tuple[bool, str]:
        """رفض المحتوى مع fallback"""
        self._reject_count += 1
        logger.warning(f"auditing_agent: ❌ رفض {source} — {reason}")
        fallback = _FALLBACK_MESSAGES.get(source, _FALLBACK_MESSAGES["default"])
        return False, fallback
    
    def get_stats(self) -> dict:
        """إحصائيات التدقيق"""
        uptime = (datetime.now(timezone.utc) - self._start_time).seconds
        return {
            "total": self._audit_count,
            "rejected": self._reject_count,
            "approved": self._audit_count - self._reject_count,
            "rejection_rate": f"{self._reject_count/max(1,self._audit_count)*100:.1f}%",
            "uptime_sec": uptime,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Instance مشترك — Singleton
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
auditing_agent = AuditingAgent()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper functions للاستخدام المباشر
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def audit_content(content: str, source: str = "default") -> Tuple[bool, str]:
    """تدقيق عام"""
    return auditing_agent.audit(content, source=source)

def audit_financial_content(content: str, source: str = "signal") -> Tuple[bool, str]:
    """تدقيق مالي"""
    return auditing_agent.audit_financial(content, source=source)

def audit_outlook_content(content: str) -> Tuple[bool, str]:
    """تدقيق رؤية المؤسسات"""
    return auditing_agent.audit_outlook(content)

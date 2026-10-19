"""
🛡️ رائد — Data Validation Layer (الطبقة ٢)
يتحقق من جودة كل بيانات قبل وصولها للنموذج.
"""

import math
import time
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    VALID   = "valid"
    WARNING = "warning"
    INVALID = "invalid"


@dataclass
class ValidationResult:
    status:   ValidationStatus
    score:    float          # 0.0 – 1.0 جودة البيانات
    warnings: List[str] = field(default_factory=list)
    errors:   List[str] = field(default_factory=list)
    cleaned:  Optional[Any] = None   # البيانات بعد التنظيف

    @property
    def is_usable(self) -> bool:
        return self.status in (ValidationStatus.VALID, ValidationStatus.WARNING)


# ─── حدود السوق المقبولة ───────────────────────────────────────────────────────
PRICE_BOUNDS = {
    "BTC":  (1_000,    500_000),
    "ETH":  (50,       50_000),
    "BNB":  (10,       5_000),
    "DEFAULT": (0.000001, 1_000_000),
}

MAX_PRICE_CHANGE_PCT  = 60.0   # أقصى تغيّر سعري مقبول في دورة واحدة
MAX_VOLUME_SPIKE      = 50.0   # أقصى ارتفاع مقبول في الحجم (×)
MAX_STALENESS_SECONDS = 300    # البيانات لا تتجاوز ٥ دقائق
MIN_LIQUIDITY_USD     = 100_000


class DataValidator:
    """
    يمر كل object بيانات عبر هذا الـ Validator قبل أن يصل للنموذج.
    يُنظّف، يُحدّث، ويحكم بالقبول أو الرفض.
    """

    def __init__(self):
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # symbol → (price, ts)

    # ═══════════════════════════════════════════════════════════
    # 1. تحقق السعر
    # ═══════════════════════════════════════════════════════════
    def validate_price(self, symbol: str, price: float,
                       timestamp: Optional[float] = None) -> ValidationResult:
        warnings, errors = [], []

        # 1-أ. نوع البيانات
        if not isinstance(price, (int, float)) or math.isnan(price) or math.isinf(price):
            return ValidationResult(ValidationStatus.INVALID, 0.0,
                                    errors=["السعر قيمة غير صالحة"])

        # 1-ب. موجب
        if price <= 0:
            return ValidationResult(ValidationStatus.INVALID, 0.0,
                                    errors=[f"سعر سالب أو صفر: {price}"])

        # 1-ج. حدود المنطق
        lo, hi = PRICE_BOUNDS.get(symbol.upper(), PRICE_BOUNDS["DEFAULT"])
        if not (lo <= price <= hi):
            errors.append(f"السعر {price:,.4f} خارج النطاق المتوقع [{lo}, {hi}]")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        # 1-د. مقارنة بالسعر السابق
        score = 1.0
        if symbol in self._price_cache:
            prev_price, prev_ts = self._price_cache[symbol]
            if prev_price > 0:
                chg = abs(price - prev_price) / prev_price * 100
                if chg > MAX_PRICE_CHANGE_PCT:
                    errors.append(f"تغيّر سعري مفاجئ {chg:.1f}٪ — محتمل تلاعب")
                    return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)
                elif chg > MAX_PRICE_CHANGE_PCT * 0.5:
                    warnings.append(f"تغيّر سعري مرتفع {chg:.1f}٪")
                    score -= 0.2

        # 1-هـ. حداثة البيانات
        now = time.time()
        ts  = timestamp or now
        staleness = now - ts
        if staleness > MAX_STALENESS_SECONDS:
            warnings.append(f"بيانات قديمة منذ {staleness:.0f}ث")
            score -= 0.3

        # تخزين للمقارنة القادمة
        self._price_cache[symbol] = (price, ts)

        status = ValidationStatus.VALID if not warnings else ValidationStatus.WARNING
        return ValidationResult(status, max(score, 0.1), warnings=warnings, cleaned=price)

    # ═══════════════════════════════════════════════════════════
    # 2. تحقق بيانات OHLCV
    # ═══════════════════════════════════════════════════════════
    def validate_ohlcv(self, candle: Dict) -> ValidationResult:
        warnings, errors = [], []
        required = {"open", "high", "low", "close", "volume"}

        missing = required - set(candle.keys())
        if missing:
            return ValidationResult(ValidationStatus.INVALID, 0.0,
                                    errors=[f"حقول مفقودة: {missing}"])

        o, h, l, c, v = (candle[k] for k in ("open", "high", "low", "close", "volume"))

        # علاقات OHLC
        if not (l <= o <= h and l <= c <= h):
            errors.append(f"علاقة OHLC خاطئة: O={o} H={h} L={l} C={c}")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        if l <= 0 or h <= 0:
            errors.append("قيم سالبة في الشمعة")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        # فتيل ضخم (>30٪ من الجسم) → تحذير فقط
        score = 1.0
        body  = abs(c - o)
        wick  = h - l
        if body > 0 and wick / body > 10:
            warnings.append("فتيل ضخم جداً — شمعة مريبة")
            score -= 0.15

        if v < 0:
            errors.append("حجم سالب")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        if v == 0:
            warnings.append("حجم صفر — قد تكون بيانات ملفقة")
            score -= 0.2

        cleaned = {**candle, "open": o, "high": h, "low": l, "close": c, "volume": v}
        status  = ValidationStatus.VALID if not warnings else ValidationStatus.WARNING
        return ValidationResult(status, max(score, 0.1), warnings=warnings, cleaned=cleaned)

    # ═══════════════════════════════════════════════════════════
    # 3. تحقق بيانات الأخبار
    # ═══════════════════════════════════════════════════════════
    def validate_news_item(self, item: Dict) -> ValidationResult:
        warnings, errors = [], []

        title = item.get("title", "")
        if not title or len(title) < 10:
            return ValidationResult(ValidationStatus.INVALID, 0.0,
                                    errors=["عنوان الخبر فارغ أو قصير جداً"])

        score = 1.0

        # تحقق من التاريخ
        pub = item.get("published_at") or item.get("created_at")
        if pub:
            age = time.time() - float(pub)
            if age > 86_400 * 3:   # أكثر من ٣ أيام
                warnings.append(f"خبر قديم — عمره {age/3600:.0f}ساعة")
                score -= 0.3
        else:
            warnings.append("لا يوجد تاريخ نشر")
            score -= 0.1

        # مصدر موثوق
        trusted = {"coindesk", "cointelegraph", "reuters", "bloomberg",
                   "theblock", "decrypt", "coinbase", "binance"}
        source  = str(item.get("source", {}).get("title", "")).lower()
        if source and not any(t in source for t in trusted):
            warnings.append(f"مصدر غير معروف: {source}")
            score -= 0.1

        status = ValidationStatus.VALID if not warnings else ValidationStatus.WARNING
        return ValidationResult(status, max(score, 0.1), warnings=warnings, cleaned=item)

    # ═══════════════════════════════════════════════════════════
    # 4. تحقق بيانات On-Chain
    # ═══════════════════════════════════════════════════════════
    def validate_onchain(self, data: Dict) -> ValidationResult:
        warnings, errors = [], []
        score = 1.0

        tvl = data.get("tvl", 0)
        if tvl < 0:
            errors.append("TVL سالب — بيانات تالفة")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        if tvl < MIN_LIQUIDITY_USD:
            warnings.append(f"TVL منخفض جداً: ${tvl:,.0f} — سيولة ضعيفة")
            score -= 0.25

        vol_24h = data.get("volume24h", 0)
        if vol_24h < 0:
            errors.append("حجم تداول سالب")
            return ValidationResult(ValidationStatus.INVALID, 0.0, errors=errors)

        if tvl > 0 and vol_24h / tvl > MAX_VOLUME_SPIKE:
            warnings.append(f"ارتفاع حجم مشبوه: {vol_24h/tvl:.1f}× من TVL")
            score -= 0.3

        status = ValidationStatus.VALID if not warnings else ValidationStatus.WARNING
        return ValidationResult(status, max(score, 0.1), warnings=warnings, cleaned=data)

    # ═══════════════════════════════════════════════════════════
    # 5. تحقق قائمة عامة (batch)
    # ═══════════════════════════════════════════════════════════
    def validate_batch(self, items: List[Dict], validator_fn) -> Tuple[List, List[str]]:
        """يرشّح قائمة بيانات ويُعيد الصالح منها + ملخص المشاكل."""
        valid_items, all_issues = [], []
        for i, item in enumerate(items):
            result = validator_fn(item)
            if result.is_usable:
                valid_items.append(result.cleaned or item)
            else:
                all_issues.append(f"[{i}] {'; '.join(result.errors)}")

        rejection_rate = 1 - len(valid_items) / max(len(items), 1)
        if rejection_rate > 0.5:
            logger.warning(f"⚠️ معدل رفض عالٍ {rejection_rate:.0%} — تحقق من المصدر")

        return valid_items, all_issues

    # ═══════════════════════════════════════════════════════════
    # 6. تقرير الصحة
    # ═══════════════════════════════════════════════════════════
    def health_report(self) -> Dict:
        return {
            "cached_symbols": len(self._price_cache),
            "price_cache":    {s: {"price": p, "age_s": round(time.time()-ts, 1)}
                               for s, (p, ts) in self._price_cache.items()},
        }


# Singleton
validator = DataValidator()

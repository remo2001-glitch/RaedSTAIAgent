"""
🔬 رائد — Model Drift Monitor
يراقب تراجع فعالية النموذج مع الزمن
يُنبّه عند ضعف الأداء ويُوصي بإعادة المعايرة
"""

import time
import logging
import math
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DriftState:
    current_win_rate:   float
    baseline_win_rate:  float
    drift_pct:          float        # الانحراف عن الأساس %
    drift_level:        str          # "none" | "mild" | "moderate" | "severe"
    signals_evaluated:  int
    last_update:        float
    recommendation_ar:  str
    needs_recalibration: bool


class DriftMonitor:
    """
    يقارن الأداء الأخير بالـ Backtest baseline.
    عتبات: mild>10% | moderate>20% | severe>35%
    """

    DRIFT_MILD     = 0.10
    DRIFT_MODERATE = 0.20
    DRIFT_SEVERE   = 0.35
    MIN_SIGNALS    = 10   # أدنى عدد إشارات للحكم

    def __init__(self, baseline_win_rate: float = 0.55):
        self.baseline       = baseline_win_rate
        self._outcomes:     List[bool] = []    # True=win, False=loss
        self._timestamps:   List[float] = []
        self._rolling_window = 50              # آخر 50 إشارة

    def record_outcome(self, was_correct: bool):
        self._outcomes.append(was_correct)
        self._timestamps.append(time.time())
        # احتفظ بآخر rolling_window فقط
        if len(self._outcomes) > self._rolling_window * 2:
            self._outcomes   = self._outcomes[-self._rolling_window:]
            self._timestamps = self._timestamps[-self._rolling_window:]

    def assess(self) -> DriftState:
        recent = self._outcomes[-self._rolling_window:]
        n      = len(recent)

        if n < self.MIN_SIGNALS:
            return DriftState(
                current_win_rate=0, baseline_win_rate=self.baseline,
                drift_pct=0, drift_level="none",
                signals_evaluated=n,
                last_update=time.time(),
                recommendation_ar=f"⏳ {n}/{self.MIN_SIGNALS} إشارة — نحتاج المزيد للتقييم",
                needs_recalibration=False,
            )

        current_wr = sum(recent) / n
        drift      = (self.baseline - current_wr) / self.baseline if self.baseline > 0 else 0
        drift      = max(drift, 0)   # نهتم فقط بالتراجع

        if drift >= self.DRIFT_SEVERE:
            level = "severe"
            rec   = "🔴 تراجع حاد — يُنصح بإيقاف التداول ومراجعة الاستراتيجية فوراً"
            needs = True
        elif drift >= self.DRIFT_MODERATE:
            level = "moderate"
            rec   = "🟠 تراجع متوسط — تقليل الحجم 50% وإعادة معايرة النموذج"
            needs = True
        elif drift >= self.DRIFT_MILD:
            level = "mild"
            rec   = "🟡 تراجع خفيف — مراقبة مستمرة, تقليل الحجم 20%"
            needs = False
        else:
            level = "none"
            rec   = "✅ النموذج يعمل ضمن المعايير المتوقعة"
            needs = False

        return DriftState(
            current_win_rate=round(current_wr, 3),
            baseline_win_rate=round(self.baseline, 3),
            drift_pct=round(drift * 100, 1),
            drift_level=level,
            signals_evaluated=n,
            last_update=time.time(),
            recommendation_ar=rec,
            needs_recalibration=needs,
        )

    def update_baseline(self, new_baseline: float):
        self.baseline = new_baseline
        logger.info(f"Drift baseline updated to {new_baseline:.2%}")

    def reset(self):
        self._outcomes   = []
        self._timestamps = []
        logger.info("Drift monitor reset")

    def format_ar(self, state: DriftState) -> str:
        bar_len = round(state.current_win_rate * 10)
        bar     = "█" * bar_len + "░" * (10 - bar_len)
        return (
            f"🔬 *مراقبة النموذج — Model Drift*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"معدل الفوز الحالي: {bar} {state.current_win_rate:.0%}\n"
            f"المعيار الأساسي:   {state.baseline_win_rate:.0%}\n"
            f"الانحراف:          {state.drift_pct:.1f}%\n"
            f"الإشارات المُقيَّمة: {state.signals_evaluated}\n\n"
            f"{state.recommendation_ar}"
        )


drift_monitor = DriftMonitor()

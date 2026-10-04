"""
🎯 رائد — Signal Outcome Tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
البنية التحتية لحلقة التعلّم الذاتي (خطة التطوير — البُعد الرابع، بموافقة
رحال على البدء بالبنية التحتية فقط الآن، دون أي معايرة تلقائية للعتبات).

الفجوة التي يسدّها هذا الملف: النظام الحالي (VirtualWallet + المهمة
المجدولة في raed_engine.py + DriftMonitor) يُغلق الصفقات الافتراضية
ويُسجِّل ربح/خسارة، لكن كرقم منفصل تماماً عن الإشارة التي أنتجت الصفقة —
لا يُعرَف نوع الإعداد (setup_type)، ولا الثقة وقت الإشارة، ولا عدد
التأكيدات، ولا RSI/ADX حينها. هذا الملف يُضيف "بطاقة هوية" لكل إشارة
صادرة، تُربَط لاحقاً بنتيجتها الفعلية، لتمكين قياس win_rate/expectancy
لكل نوع إعداد على حدة — تمهيداً لأي معايرة مستقبلية (لا تتم هنا).

لا يُعيد هذا الملف بناء أي شيء موجود: يستخدم نفس نمط Redis المُخصَّص لكل
مفتاح المُتَّبَع أصلاً في state_manager.py (raed:vw:{user_id})، ولا يستبدل
DriftMonitor — يعمل بجانبه (كلاهما يُستدعيان معاً عند إغلاق أي صفقة).
"""

import time
import uuid
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "raed:sig:"        # سجل إشارة واحدة: raed:sig:{signal_id}
REDIS_INDEX_KEY  = "raed:sig_index"   # قائمة كل signal_id بترتيب الصدور
MAX_INDEX_SIZE   = 20000              # سقف أمان لحجم الفهرس (لا نفقد بيانات، فقط نمنع نمواً غير محدود)


@dataclass
class SignalRecord:
    signal_id:          str
    symbol:             str
    setup_type:         str             # "long_reversal" | "long_breakout" | "short" | "wait" | "unknown"
    direction:          str             # "long" | "short" | "neutral"
    confidence_pct:     float
    confirmations_met:  int
    rsi_1d:             Optional[float] = None
    adx:                Optional[float] = None
    di_plus:            Optional[float] = None
    di_minus:           Optional[float] = None
    regime:             str = ""
    entry_price:        float = 0.0
    sl_price:           float = 0.0
    tp1_price:          float = 0.0
    tp2_price:          float = 0.0
    created_ts:         float = field(default_factory=time.time)
    user_id:            Optional[int] = None
    # تُملأ لاحقاً عند إغلاق الصفقة المرتبطة (إن وُجدت):
    status:             str = "open"    # "open" | "closed_tp" | "closed_sl" | "expired" | "cancelled"
    closed_ts:          Optional[float] = None
    exit_price:         Optional[float] = None
    pnl_pct:            Optional[float] = None
    was_win:            Optional[bool] = None
    # rejected_signal_eval_fix (الفجوة الثانية — تقييم الإشارات المرفوضة
    # بأثر رجعي): log_signal() يُسجِّل كل إشارة فور صدورها بصرف النظر عن
    # تنفيذها، لكن لا شيء كان يُميِّز "نُفِّذت فعلاً" عن "صدرت ولم تُنفَّذ"
    # — كلتاهما تبقيان status="open" للأبد إن لم تُفتَح صفقة حقيقية بها.
    # executed=True تُضبَط فقط عند فتح مركز حقيقي بهذه الإشارة (mark_executed)،
    # فتُصبح executed=False + status="open" القديمة العلامة الموثوقة لإشارة
    # "صدرت ورُفضَت ولم تُنفَّذ قط" — هذه فقط المؤهَّلة للتقييم الافتراضي.
    executed:              bool = False
    hypothetical_outcome:  Optional[str] = None   # "would_win"|"would_lose"|"entry_not_reached"|"inconclusive"
    hypothetical_eval_ts:  Optional[float] = None


def evaluate_hypothetical_outcome(signal: "SignalRecord", candles: List[Dict[str, Any]]) -> Optional[str]:
    """
    دالة خالصة (لا حالة، لا شبكة) تُحاكي: "لو نُفِّذت هذه الإشارة المرفوضة
    فعلياً، هل كانت لتضرب الهدف أم وقف الخسارة أولاً؟"

    المدخل candles: شموع OHLCV بعد لحظة صدور الإشارة فقط (مرتَّبة زمنياً
    تصاعدياً)، كل عنصر يحوي "timestamp"/"high"/"low" على الأقل (نفس صيغة
    get_ohlcv في core/data_layer.py).

    المخرج:
    - "entry_not_reached": السعر لم يصل قط لمستوى الدخول المحدَّد خلال
      نافذة الشموع المتاحة
    - "would_win": بعد لمس الدخول، ضرب الهدف الأول (tp1) قبل وقف الخسارة
    - "would_lose": بعد لمس الدخول، ضرب وقف الخسارة قبل الهدف الأول
    - None: لمس الدخول لكن لم يُحسَم أيهما بعد ضمن الشموع المتاحة — يحتاج
      إعادة محاولة لاحقاً بشموع أحدث، وليس نتيجة نهائية

    عند تحقق الهدف والوقف في نفس الشمعة الواحدة معاً (شموع يومية خشنة لا
    تحسم الترتيب الدقيق داخل اليوم)، نفترض وقف الخسارة أولاً — افتراض
    متحفِّظ يتجنَّب المبالغة في تقدير نسبة الفوز الافتراضية.
    """
    if not candles or signal.entry_price <= 0:
        return None
    is_long = signal.direction == "long"
    relevant = sorted(
        [c for c in candles if float(c.get("timestamp", 0)) >= signal.created_ts],
        key=lambda c: c.get("timestamp", 0)
    )
    if not relevant:
        return None

    entry_touched_idx = None
    for i, c in enumerate(relevant):
        hi, lo = float(c.get("high", 0)), float(c.get("low", 0))
        touched = (lo <= signal.entry_price) if is_long else (hi >= signal.entry_price)
        if touched:
            entry_touched_idx = i
            break
    if entry_touched_idx is None:
        return "entry_not_reached"

    has_sl = signal.sl_price > 0
    has_tp = signal.tp1_price > 0
    for c in relevant[entry_touched_idx:]:
        hi, lo = float(c.get("high", 0)), float(c.get("low", 0))
        if is_long:
            sl_hit = has_sl and lo <= signal.sl_price
            tp_hit = has_tp and hi >= signal.tp1_price
        else:
            sl_hit = has_sl and hi >= signal.sl_price
            tp_hit = has_tp and lo <= signal.tp1_price
        if sl_hit and tp_hit:
            return "would_lose"   # افتراض متحفِّظ عند التعادل داخل نفس الشمعة
        if sl_hit:
            return "would_lose"
        if tp_hit:
            return "would_win"
    return None  # لمس الدخول لكن لم يُحسَم بعد — يحتاج شموعاً أحدث


class SignalTracker:
    """
    يُستخدَم عبر state_manager المُحقَن (نفس نمط raed:vw:{user_id} —
    مفتاح Redis مخصَّص لكل سجل بحيث لا يُفقَد عند إعادة تشغيل الخدمة).
    قابل للعمل بدون Redis (fallback بلا تخزين — يُرجع None/فارغ بأمان)
    تماماً كبقية النظام عند غياب Redis.
    """

    def __init__(self, state_manager=None):
        self.attach(state_manager)

    def attach(self, state_manager):
        """يربط الـ tracker بـ state_manager الحقيقي بعد إنشائه في raed_engine
        (نفس نمط الحقن المتأخر المُستخدَم لعناصر أخرى في المحرك)."""
        self._sm = state_manager

    def _redis(self):
        sm = getattr(self, "_sm", None)
        if sm is None:
            return None
        return getattr(sm, "_redis", None) if getattr(sm, "_redis_ok", False) else None

    # ── تسجيل إشارة جديدة ────────────────────────────────────────────
    def log_signal(self, symbol: str, setup_type: str, direction: str,
                    confidence_pct: float, confirmations_met: int,
                    entry_price: float = 0.0, sl_price: float = 0.0,
                    tp1_price: float = 0.0, tp2_price: float = 0.0,
                    rsi_1d: Optional[float] = None, adx: Optional[float] = None,
                    di_plus: Optional[float] = None, di_minus: Optional[float] = None,
                    regime: str = "", user_id: Optional[int] = None) -> Optional[str]:
        """
        يُسجِّل إشارة فور صدورها — بصرف النظر عن تنفيذها فعلياً كصفقة.
        هذا مهم: نريد لاحقاً معرفة هل بوابة التأكيدات نفسها صحيحة (هل
        الإشارات التي مُنعَت من الدخول كانت ستخسر فعلاً لو نُفِّذت؟)، لا
        فقط أداء الصفقات المُنفَّذة. يُعيد signal_id أو None إذا Redis غير متاح.
        """
        if self._redis() is None:
            return None
        signal_id = uuid.uuid4().hex[:16]
        record = SignalRecord(
            signal_id=signal_id, symbol=symbol.upper(), setup_type=setup_type,
            direction=direction, confidence_pct=round(float(confidence_pct), 1),
            confirmations_met=int(confirmations_met),
            rsi_1d=rsi_1d, adx=adx, di_plus=di_plus, di_minus=di_minus,
            regime=regime, entry_price=float(entry_price), sl_price=float(sl_price),
            tp1_price=float(tp1_price), tp2_price=float(tp2_price), user_id=user_id,
        )
        if self._save(record):
            self._append_index(signal_id)
            return signal_id
        return None

    # ── إغلاق إشارة (بعد إغلاق الصفقة المرتبطة بها) ─────────────────
    def close_signal(self, signal_id: str, exit_price: float, status: str) -> Optional[SignalRecord]:
        """
        يُحدِّث سجل إشارة عند إغلاق الصفقة المرتبطة بها (TP/SL/انتهاء
        صلاحية/إلغاء). يُستدعى بجانب drift_monitor.record_outcome، لا بدلاً
        منه — كلاهما يستهلكان نفس حدث الإغلاق لغرضين مختلفين (DriftMonitor:
        تنبيه فوري مُجمَّع؛ SignalTracker: بيانات مُصنَّفة لتحليل لاحق).
        """
        if not signal_id:
            return None
        record = self.get_signal(signal_id)
        if not record:
            return None
        record.status = status
        record.closed_ts = time.time()
        record.exit_price = float(exit_price)
        if record.entry_price > 0:
            direction_mult = -1 if record.direction == "short" else 1
            record.pnl_pct = round(
                (record.exit_price - record.entry_price) / record.entry_price * 100 * direction_mult, 2
            )
            record.was_win = record.pnl_pct > 0
        self._save(record)
        return record

    def mark_executed(self, signal_id: str) -> bool:
        """
        يُستدعى فور نجاح فتح مركز حقيقي بهذه الإشارة (VirtualWallet.buy
        نجح مع signal_id) — يُميِّزها عن إشارات صدرت ورُفضَت ولم تُفتَح
        لها صفقة قط، وهي وحدها المؤهَّلة لاحقاً للتقييم الافتراضي.
        """
        if not signal_id:
            return False
        record = self.get_signal(signal_id)
        if not record:
            return False
        record.executed = True
        return self._save(record)

    def get_pending_rejected_signals(self, min_age_hours: float = 24.0,
                                      max_age_hours: float = 24.0 * 14,
                                      limit: int = 200) -> List[SignalRecord]:
        """
        يُعيد الإشارات المؤهَّلة للتقييم الافتراضي: صدرت ولم تُنفَّذ قط
        (executed=False)، لم تُقيَّم بعد (hypothetical_outcome is None)،
        وعمرها ضمن النافذة [min_age_hours, max_age_hours] — الحد الأدنى
        يضمن وقتاً كافياً ليتحرك السعر فعلياً نحو الدخول/الهدف/الوقف قبل
        محاولة التقييم، والحد الأقصى يمنع محاولات متكررة بلا جدوى على
        إشارات قديمة جداً فقدت صلتها.
        """
        now = time.time()
        min_age_s = min_age_hours * 3600
        max_age_s = max_age_hours * 3600
        out: List[SignalRecord] = []
        for sid in self.get_all_signal_ids(limit=limit * 5):
            rec = self.get_signal(sid)
            if not rec or rec.executed or rec.hypothetical_outcome is not None:
                continue
            age = now - rec.created_ts
            if min_age_s <= age <= max_age_s and rec.entry_price > 0:
                out.append(rec)
                if len(out) >= limit:
                    break
        return out

    def record_hypothetical_outcome(self, signal_id: str, outcome: str) -> bool:
        """يُسجِّل نتيجة التقييم الافتراضي لإشارة مرفوضة — قيمة واحدة من
        "would_win"|"would_lose"|"entry_not_reached"|"inconclusive"."""
        record = self.get_signal(signal_id)
        if not record:
            return False
        record.hypothetical_outcome = outcome
        record.hypothetical_eval_ts = time.time()
        return self._save(record)

    def get_signal(self, signal_id: str) -> Optional[SignalRecord]:
        r = self._redis()
        if not r or not signal_id:
            return None
        try:
            raw = r.get(f"{REDIS_KEY_PREFIX}{signal_id}")
        except Exception as e:
            logger.debug(f"SignalTracker.get_signal redis: {e}")
            return None
        if not raw:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            # حماية من حقول قديمة/إضافية إن تغيّر المخطط لاحقاً
            known = {f.name for f in SignalRecord.__dataclass_fields__.values()}
            data = {k: v for k, v in data.items() if k in known}
            return SignalRecord(**data)
        except Exception as e:
            logger.debug(f"SignalTracker.get_signal parse: {e}")
            return None

    def _save(self, record: SignalRecord) -> bool:
        r = self._redis()
        if not r:
            return False
        try:
            r.set(f"{REDIS_KEY_PREFIX}{record.signal_id}",
                  json.dumps(asdict(record), ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning(f"SignalTracker._save: {e}")
            return False

    def _append_index(self, signal_id: str):
        r = self._redis()
        if not r:
            return
        try:
            r.rpush(REDIS_INDEX_KEY, signal_id)
            r.ltrim(REDIS_INDEX_KEY, -MAX_INDEX_SIZE, -1)
        except Exception as e:
            logger.debug(f"SignalTracker._append_index: {e}")

    def get_all_signal_ids(self, limit: int = 5000) -> List[str]:
        r = self._redis()
        if not r:
            return []
        try:
            ids = r.lrange(REDIS_INDEX_KEY, -limit, -1)
            return [x.decode("utf-8") if isinstance(x, bytes) else x for x in ids]
        except Exception as e:
            logger.debug(f"SignalTracker.get_all_signal_ids: {e}")
            return []

    # ── استعلام أداء (قراءة فقط — لا معايرة تلقائية) ────────────────
    def compute_performance(self, setup_type: Optional[str] = None,
                             symbol: Optional[str] = None,
                             min_closed: int = 1) -> Dict[str, Any]:
        """
        يحسب win_rate/expectancy لصفقات مغلقة فعلياً، مُصفَّاة حسب نوع
        الإعداد و/أو العملة. استعلام قراءة صرف — لا يُغيِّر أي عتبة أو
        سلوك في النظام؛ الغرض توفير الأرقام لمراجعة رحال يدوياً أولاً
        (بموافقته: لا معايرة تلقائية قبل تراكم عيّنة كافية + مراجعة بشرية).
        """
        ids = self.get_all_signal_ids()
        closed = []
        for sid in ids:
            rec = self.get_signal(sid)
            if not rec or rec.status == "open" or rec.was_win is None:
                continue
            if setup_type and rec.setup_type != setup_type:
                continue
            if symbol and rec.symbol != symbol.upper():
                continue
            closed.append(rec)

        n = len(closed)
        if n < min_closed:
            return {
                "n_closed": n, "win_rate": None, "expectancy_pct": None,
                "avg_win_pct": None, "avg_loss_pct": None,
                "note": f"عيّنة غير كافية ({n} صفقة مغلقة فقط — الحد الأدنى المُوصى به 100 لكل نوع إعداد)",
            }

        wins   = [r.pnl_pct for r in closed if r.was_win]
        losses = [r.pnl_pct for r in closed if not r.was_win]
        win_rate = len(wins) / n
        avg_win  = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

        return {
            "n_closed":       n,
            "win_rate":       round(win_rate * 100, 1),
            "expectancy_pct": round(expectancy, 2),
            "avg_win_pct":    round(avg_win, 2),
            "avg_loss_pct":   round(avg_loss, 2),
            "note": (
                None if n >= 100 else
                f"عيّنة أولية ({n}/100 صفقة) — الأرقام إرشادية فقط حتى تراكم 100 صفقة مغلقة على الأقل"
            ),
        }

    def compute_rejected_performance(self, setup_type: Optional[str] = None,
                                      symbol: Optional[str] = None,
                                      min_evaluated: int = 1) -> Dict[str, Any]:
        """
        يحسب "لو نُفِّذت الإشارات المرفوضة فعلاً، ماذا كانت ستُحقِّق؟" —
        بالاعتماد حصراً على سجلات قُيِّمت افتراضياً فعلاً عبر
        evaluate_hypothetical_outcome (hypothetical_outcome ليس None ولا
        "entry_not_reached"، لأن الأخيرة لا تمثّل ربحاً أو خسارة فعلية).
        الغرض: مقارنة مباشرة مع compute_performance (الصفقات المُنفَّذة
        فعلاً) للتحقق هل بوابة التأكيدات صحيحة فعلاً — إن كانت الإشارات
        المرفوضة ستخسر أكثر بوضوح من المُنفَّذة، هذا يُثبِت أن البوابة
        تعمل كما يجب؛ إن تساوت النسبتان أو كانت المرفوضة أفضل، فهذا
        مؤشر أن البوابة متشدِّدة أكثر من اللازم. قراءة صرف — لا تُغيِّر
        أي عتبة أو سلوك تلقائياً.
        """
        ids = self.get_all_signal_ids()
        evaluated = []
        entry_missed = 0
        inconclusive = 0
        for sid in ids:
            rec = self.get_signal(sid)
            if not rec or rec.executed or rec.hypothetical_outcome is None:
                continue
            if setup_type and rec.setup_type != setup_type:
                continue
            if symbol and rec.symbol != symbol.upper():
                continue
            if rec.hypothetical_outcome == "entry_not_reached":
                entry_missed += 1
                continue
            if rec.hypothetical_outcome == "inconclusive":
                inconclusive += 1
                continue
            evaluated.append(rec)

        n = len(evaluated)
        if n < min_evaluated:
            return {
                "n_evaluated": n, "entry_not_reached": entry_missed,
                "inconclusive": inconclusive, "win_rate": None,
                "note": f"عيّنة غير كافية ({n} إشارة مرفوضة مُقيَّمة فقط)",
            }
        wins = sum(1 for r in evaluated if r.hypothetical_outcome == "would_win")
        win_rate = wins / n
        return {
            "n_evaluated":       n,
            "entry_not_reached": entry_missed,
            "inconclusive":      inconclusive,
            "win_rate":          round(win_rate * 100, 1),
            "note": (
                None if n >= 50 else
                f"عيّنة أولية ({n}/50 إشارة) — نسبة إرشادية فقط حتى تراكم 50 على الأقل"
            ),
        }

    def format_performance_ar(self, setup_type: Optional[str] = None,
                               symbol: Optional[str] = None) -> str:
        """نص عربي جاهز للعرض — يُستخدَم من أي أمر Telegram يريد عرض التقرير."""
        perf = self.compute_performance(setup_type=setup_type, symbol=symbol)
        title = setup_type or (symbol or "كل الإشارات")
        if perf["win_rate"] is None:
            lines = [f"📊 *أداء {title}*", perf["note"]]
        else:
            lines = [
                f"📊 *أداء {title}*",
                f"عدد الصفقات المغلقة: {perf['n_closed']}",
                f"معدل الفوز: {perf['win_rate']}%",
                f"العائد المتوقع (Expectancy): {perf['expectancy_pct']:+.2f}%",
                f"متوسط الربح: {perf['avg_win_pct']:+.2f}% | متوسط الخسارة: {perf['avg_loss_pct']:+.2f}%",
            ]
            if perf["note"]:
                lines.append(f"⚠️ {perf['note']}")

        # rejected_signal_eval_fix: مقارنة مباشرة مع الإشارات المرفوضة
        # المُقيَّمة افتراضياً، إن وُجدت أي منها فعلاً لنفس التصفية
        rej = self.compute_rejected_performance(setup_type=setup_type, symbol=symbol)
        if rej["win_rate"] is not None or rej["n_evaluated"] > 0:
            lines.append("")
            lines.append("🔬 *لو نُفِّذت الإشارات المرفوضة؟*")
            if rej["win_rate"] is not None:
                lines.append(f"من {rej['n_evaluated']} إشارة مرفوضة مُقيَّمة: كانت لتربح {rej['win_rate']}% منها")
                if rej["note"]:
                    lines.append(f"⚠️ {rej['note']}")
            else:
                lines.append(rej["note"])
            if rej["entry_not_reached"] > 0:
                lines.append(f"(+ {rej['entry_not_reached']} إشارة لم يصل السعر لدخولها إطلاقاً)")

        return "\n".join(lines)


# مثيل عام — يُحقَن به state_manager الحقيقي عند تهيئة raed_engine
# (نفس نمط الحقن المتأخر المُستخدَم لعناصر أخرى؛ لا استيراد دائري)
signal_tracker = SignalTracker()

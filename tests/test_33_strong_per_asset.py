"""سطر لكل أصل في قسم المسح السريع (الدفعة 78): «✅ فُتح آلياً» لما فُتح، والسبب لغيره."""
from _util import chk, src
import core.auto_trader_b68 as AT

rec = {"verdict": "AUTO-FAIL", "size_pct": 20, "direction": "long", "sl": 95, "tp1": 106, "tp2": 110, "atr_pct": 2}
chk("P1a شرح AUTO-FAIL بلا R/R", AT.long_gate_b68(rec, 100)[1] == "بنية الصفقة فاشلة (AUTO-FAIL): R/R المرجّح < 1:1 — الأهداف قريبة مقارنة بالوقف")
e = src("core/raed_engine.py")
chk("P2a لا إخفاء جماعي", "_opened77" not in e and 'get("opened_syms")' in e)
chk("P2b فُتح آلياً", "✅ فُتح آلياً (افتراضي) — انظر إشعار الصفقة" in e)
chk("P2c مركز قياس مفتوح", "🤖 لم يُفتح — مركز قياس مفتوح عليه بالفعل" in e)
a = src("core/auto_trader_b68.py")
chk("P2d تسجيل الأصول المفتوحة (شراء وShort)", a.count('summary.setdefault("opened_syms", []).append(sym)') == 2)

"""الدفعة 99 (رحال 2026-11-20 18:17 «1. أ  2. موافق»): RR2 (أ) «(للعلم)» يطابق المنطق المجمّد (السلع فقط)؛ RR1 مصدر أهداف Short."""
from _util import chk, src
import core.short_eval_b68 as SE

a = src("handlers/analysis.py")
chk("RR2a علم البطاقة = منطق الاستثناء", '"fund_excluded_b82": bool(locals().get("_gold_fund_ex"))' in a)
chk("RR2b قائمة التأكيدات والناقص بلا استثناء الأسهم", "if (_fund_pct >= -0.01 and not _gold_fund_ex   # RR2" in a
    and "if (_gold_fund_ex   # RR2 (أ) b99" in a and 'if _gold_fund_ex else   # RR2' in a)
chk("RR2c لا بقايا لقاعدة #696 القديمة", "batch59 (#696)" not in a)
chk("RR2d المنطق لم يُمس", "fund_excluded=bool(locals().get(\"_gold_fund_ex\", False))" in a
    and "if _f(fund_pct) > 0.01 and not fund_excluded:" in src("core/short_eval_b68.py"))
chk("RR1a وسم المصدر", SE.tp_src_note_b99("Fib") == "" and SE.tp_src_note_b99(None) == ""
    and SE.tp_src_note_b99("الدخول − 1×ATR") == "، الدخول − 1×ATR")
s = src("core/short_eval_b68.py")
chk("RR1b المصدر يُحفظ ولا يدخل الحساب", 'out["tp1_src"] = "Fib" if _t1_fib else "الدخول − 1×ATR"' in s
    and 'out["tp2_src"] = "Fib" if _t2_fib else "TP1 − 1×ATR"' in s
    and "tp1 = _below[-1] if _t1_fib else entry * (1 - a)" in s and "tp2 = _below2[-1] if _t2_fib else tp1 * (1 - a)" in s)
chk("RR1c البطاقة والتقرير الكامل يعرضانه", "_tps99(sh.get('tp1_src'))" in a and "tp_src_note_b99(ev.get('tp2_src'))" in s)

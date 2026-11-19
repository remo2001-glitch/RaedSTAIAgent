"""الدفعة 97 (رحال 2026-11-20 02:47 «نعم موافق معك»): PP1 — إشعار إغلاق القياس بالتفاصيل الكاملة (عرض فقط)."""
from datetime import datetime, timezone, timedelta
from _util import chk, src
import core.auto_trader_b68 as AT

op = (datetime.now(timezone.utc) - timedelta(hours=120.2)).isoformat()
pos = {"entry": 1765.96, "side": "long", "leverage": 1, "opened_at": op, "mfe_px": 1830.5, "mae_px": 1690.0,
       "tp1": 2044.84, "tp2": 2102.71}
t = {"price": 1751.83, "entry": 1765.96, "leverage": 1, "pnl": -4.12, "pnl_pct": -1.41, "fees": 0.29}
m = AT._close_msg("ETH", "long", "time", t, pos=pos, final=True)
chk("PP1a العنوان بالجهة والرافعة", m.startswith("🤖 *إغلاق آلي افتراضي* — ETH Long 1x\n"), m)
chk("PP1b الدخول ⇒ الخروج", "• الدخول: $1,765.96 → الخروج: $1,751.83 (حركة السعر على المركز -0.80%)" in m, m)
chk("PP1c الصافي $ و% والرسوم", "• الصافي: -$4.12 (-1.4% من الهامش) — بعد رسوم $0.29 وانزلاق الإغلاق" in m, m)
chk("PP1d المدة وأفضل/أسوأ حركة", "• المدة: 5.0 يوم (120 ساعة)" in m and "أفضل حركة +3.65% · أسوأ حركة -4.30%" in m, m)
ps = dict(pos, entry=573.59, tp1=613.64, tp2=633.9, mfe_px=615.0, mae_px=560.0, side="short")
m2 = AT._close_msg("BNB", "short", "tp1", {"price": 540.0, "entry": 573.59, "leverage": 1, "pnl": 10.0, "pnl_pct": 3.4}, pos=ps, final=False)
chk("PP1e الجزئي: «(جزئي)» والباقي والوقف الجديد", "Short 1x (جزئي)" in m2 and "أُغلق 60% — الباقي 40% بوقف جديد = سعر الهدف 1 $613.64" in m2
    and "(حركة السعر على المركز +5.86%)" in m2, m2)
m3 = AT._close_msg("XRP", "long", "sl", {"pnl": -25.35})
chk("PP1f بيانات ناقصة لا تُسقط الإشعار", "• الصافي: -$25.35" in m3 and m3.endswith("/autostats لنتائج القياس"), m3)
chk("PP1g الاستدعاء يمرّر المركز وحالة الإغلاق", "_close_msg(_bs, side, kind, t, pos=pos, final=final)" in src("core/auto_trader_b68.py"))

"""/autostats بصيغة مبلغ واحدة ومراكز مفتوحة مفصّلة، والتنبيه الساعي للعلم فقط (الدفعة 81)."""
from _util import chk, src
import core.auto_trader_b68 as AT

U = AT._usd_b81
chk("S1a صيغة المبلغ", U(-25.35) == "−$25.35" and U(4.1) == "+$4.10" and U(0) == "$0.00" and U(-1234.5) == "−$1,234.50")
st = AT.auto_stats_b68([])
st.update({"n": 1, "open": 1, "wins": 0, "total": -25.35, "avg": -25.35, "pf": 0.0, "dd": 25.35, "dd_pct": 0.25,
           "by_side": {"long": {"n": 1, "w": 0, "pnl": -25.35}}})
rows = [{"sym": "ETH", "side": "long", "hours": 2.1, "net": 3.2, "net_pct": 1.1, "to_tp1_pct": 14.2, "to_sl_pct": 9.1}]
t = AT.format_auto_stats_b68(st, rows)
chk("S1b لا «-25.35$»", "25.35$" not in t and "الصافي الكلي: −$25.35 | المتوسط: −$25.35 لكل صفقة" in t, t)
chk("S2a سطر المركز المفتوح", "↳ ETH Long منذ ساعتين — الصافي غير المحقق +$3.20 (+1.1%) | إلى TP1 14.2% | إلى الوقف 9.1%" in t, t)
t2 = AT.format_auto_stats_b68(st, [dict(rows[0], tp1_done=True, hours=30)])
chk("S2b بعد TP1 والمدة", "✅ TP1 تحقق" in t2 and "منذ 30 ساعة" in t2, t2)
chk("S2c بلا مراكز لا يتعطل", "مفتوحة: 1" in AT.format_auto_stats_b68(st))
chk("S2d /autostats يمرر المراكز", "open_rows_b81(_eng(context), uid)" in src("handlers/trading.py"))
m = src("main.py")
chk("T1a للعلم فقط", "⚡ *تنبيه — إشارة قوية (للعلم فقط، لا ينفّذ)*" in m and "تنبيه فوري — إشارة قوية" not in m)
chk("T1b ما سيحدث آلياً + التنبيه", "التداول الآلي يقرر في المسح القادم" in m and "_DISC81" in m)
chk("T2 ثقة تقديرية موسومة مع اسم الأصل", "ثقة تقديرية: {signal.confidence:.0%} (فني + ماكرو فقط)" in m and "/signal {sym} للثقة الكاملة والحكم" in m)

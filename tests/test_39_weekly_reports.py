"""التقريران الأسبوعيان (الدفعة 85، البند AA): مراكز القياس المفتوحة، ما حدث هذا الأسبوع، Futures في المحفظة، بلا تكرار."""
import time
from datetime import datetime, timezone
from _util import chk, src
import core.auto_trader_b68 as AT
import core.period_report as PR

now = time.time()
led = [
    {"ev": "open", "pos_id": "a", "symbol": "XRP", "side": "long", "ts": now - 2.5 * 86400},
    {"ev": "close", "pos_id": "a", "symbol": "XRP", "pnl": -25.35, "reason": "sl", "final": True, "ts": now - 1.6 * 86400},
    {"ev": "open", "pos_id": "b", "symbol": "ETH", "side": "long", "ts": now - 1.5 * 86400},
    {"ev": "open", "pos_id": "c", "symbol": "LTC", "side": "short", "ts": now - 9 * 86400},
    {"ev": "close", "pos_id": "b", "symbol": "ETH", "pnl": 12.5, "reason": "tp1", "final": False, "ts": now - 3600},
]
w = AT.week_summary_b85(led, now)
chk("AA2a هذا الأسبوع: الفتح بالجهة", w.startswith("📅 *هذا الأسبوع:* فُتحت 2 (XRP Long، ETH Long)") and "LTC" not in w, w)
chk("AA2b الإغلاق بسببه والجزئي والصافي", "أُغلقت 1 (XRP — الوقف)" in w and "إغلاق جزئي 1 (ETH)" in w and "الصافي المحقق −$12.85" in w, w)
chk("AA2c أسبوع بلا حركة", AT.week_summary_b85([], now) == "📅 *هذا الأسبوع:* لا فتح ولا إغلاق")
m = src("main.py")
chk("AA1 التقرير الأسبوعي يمرّر المراكز المفتوحة وملخص الأسبوع",
    "await open_rows_b81(context.bot_data.get(\"raed_engine\"), uid)" in m and "week_summary_b85(_led85)" in m
    and "format_auto_stats_b68(auto_stats_b68(_led85), _rows85)" in m)

vw = {"balance": 9245.25, "positions": {},
      "futures": {"ETH@AUTO": {"margin": 292.0, "logic_ver": "b68"}, "BTC@AUTO": {"margin": 243.0, "logic_ver": "b68"},
                  "BNB@AUTO": {"margin": 243.0, "logic_ver": "b68"}}}
ws = PR.wallet_status_lines(vw)
chk("AA3a Futures في قيمة المحفظة", ws[0] == "• المحفظة الافتراضية الآن: $10,023.25 (نقد $9,245.25 + Futures $778.00 هامش)", ws)
chk("AA3b العدد يميّز القياس الآلي", ws[1] == "• مراكز افتراضية مفتوحة: 3 (قياس آلي 3)", ws)
ws2 = PR.wallet_status_lines({"balance": 100.0, "positions": {"BTC": {"cost": 50.0}}, "futures": {"SOL": {"margin": 20.0}}})
chk("AA3c Spot ويدوية", ws2 == ["• المحفظة الافتراضية الآن: $170.00 (نقد $100.00 + Spot $50.00 بالتكلفة + Futures $20.00 هامش)",
                                 "• مراكز افتراضية مفتوحة: 2 (يدوية 2)"], ws2)

nowd = datetime.now(timezone.utc)
r1 = PR.build_user_report(1, "weekly", vw, None, now=nowd, auto_report_sent=True)
chk("AA4a لا تكرار لأرقام القياس", "التقرير الأسبوعي للقياس" in r1 and "صفقات مغلقة:" not in r1 and "لم تُغلق أي صفقة" not in r1, r1)
r2 = PR.build_user_report(1, "weekly", vw, None, now=nowd)
chk("AA4b بلا تقرير قياس منفصل يبقى القسم", "القياس الآلي — منطق /signal المجمّد" in r2)
chk("AA4c المجدول يحدد صاحب التداول الآلي للأسبوعي فقط", 'period == "weekly" and int(uid) in' in src("core/scheduler.py")
    and "auto_report_sent=_auto85" in src("core/scheduler.py"))

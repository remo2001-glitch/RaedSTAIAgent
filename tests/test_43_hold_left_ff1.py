"""FF1 (رحال 2026-11-19): موعد الخروج الزمني لكل مركز قياس في /autostats والتقرير الأسبوعي — عرض فقط."""
import asyncio
import types
from datetime import datetime, timezone, timedelta
from _util import chk, src
import core.auto_trader_b68 as AT

H = AT.hold_left_text_ff1
chk("FF1a لا شيء بلا بيانات", H(None) == "")
chk("FF1b مستحق", "مستحق" in H(-0.2) and "5 دقائق" in H(0))
chk("FF1c أقل من ساعة", H(0.4) == " | ⏰ خروج زمني خلال أقل من ساعة")
chk("FF1d الساعات للأسفل", H(1.9) == " | ⏰ خروج زمني بعد ساعة" and H(2.5).endswith("ساعتين")
    and H(6.3).endswith("بعد 6 ساعات") and H(23.9).endswith("بعد 23 ساعة"))
chk("FF1e الأيام", H(72).endswith("بعد 3.0 يوم"))

line = AT.open_lines_b81([{"sym": "ETH", "side": "long", "hours": 113.7, "net": 1.26, "net_pct": 0.4,
                            "to_tp1_pct": 15.1, "to_sl_pct": 8.4, "hold_left_h": 6.3}])[0]
chk("FF1f السطر الكامل", line.endswith("| إلى الوقف 8.4% | ⏰ خروج زمني بعد 6 ساعات"), line)
old = AT.open_lines_b81([{"sym": "ETH", "side": "long", "hours": 5, "net": 0, "net_pct": 0}])[0]
chk("FF1g صفوف قديمة بلا الحقل تبقى كما هي", "⏰" not in old)


class _DL:
    async def get_price(self, s, mkttype=None): return {"price": 1776.6}


op = (datetime.now(timezone.utc) - timedelta(hours=113.7)).isoformat()
wd = {"balance": 9700.0, "futures": {"AUTO:ETH": {"logic_ver": "b68", "side": "long", "entry": 1765.96, "qty": 0.17,
                                                  "margin": 300.0, "tp1": 2044.84, "stop_loss": 1628.24,
                                                  "opened_at": op, "max_hold_hours": 120.0, "symbol": "ETH"}}}
AT._sm = lambda: types.SimpleNamespace(get_virtual_wallet=lambda u: wd)
rows = asyncio.run(AT.open_rows_b81(types.SimpleNamespace(data_layer=_DL()), 1))
chk("FF1h open_rows يحسب المتبقي من max_hold_hours", rows and abs(rows[0]["hold_left_h"] - 6.3) < 0.05, rows)
chk("FF1i نفس قاعدة الخروج (max_hold_hours وإلا 120)", 'float(p.get("max_hold_hours") or 0) or float(getattr(VirtualWallet, "LEGACY_MAX_HOLD_HOURS", AUTO_HOLD_HOURS))'
    in src("core/auto_trader_b68.py"))

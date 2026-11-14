"""المحفظة الافتراضية ومراكز القياس المستقلة (الدفعة 70) وبوابات التداول الآلي (b68) وسطر المسح (73/74)."""
from _util import chk
from core.virtual_wallet import VirtualWallet as VW
import core.auto_trader_b68 as AT

chk("W1 مفتاح القياس", VW.auto_key("btc") == "BTC@AUTO" and VW.is_auto_key("BTC@AUTO") and not VW.is_auto_key("BTC"))
chk("W2 الرمز الأساسي", VW.base_symbol("BTC@AUTO", {"symbol": "BTC"}) == "BTC")
vw = VW({"balance": 10000.0})
r1 = vw.open_futures("BTC", "short", 100.0, 1000, 1, stop_loss=105, take_profit=90, key=VW.auto_key("BTC"),
                     extra={"logic_ver": "b68"})
r2 = vw.open_futures("BTC", "long", 100.0, 500, 1, stop_loss=95, take_profit=110)
chk("W3 مركز قياس ومركز يدوي مستقلان على الأصل نفسه", r1.get("ok") and r2.get("ok") and set(vw.futures) == {"BTC@AUTO", "BTC"}, (r1, r2, list(vw.futures)))
rr = vw.reduce_futures("BTC@AUTO", 90.0, 0.6, "tp1")
chk("W4 إغلاق 60% عند TP1", rr["ok"] and abs(rr["trade"]["quantity"] - 6.0) < 1e-6 and rr["trade"]["symbol"] == "BTC" and rr["trade"]["logic_ver"] == "b68", rr)
rc = vw.close_futures("BTC@AUTO", 90.0, "tp2")
chk("W5 إغلاق الباقي يحفظ الرمز الأساسي", rc.get("ok") and vw.history[-1]["symbol"] == "BTC" and "BTC@AUTO" not in vw.futures, rc)
chk("W6 المركز اليدوي باقٍ", "BTC" in vw.futures)
chk("W7 الرصيد منطقي", 0 < vw.balance < 10000 + 200)

# بوابة الشراء: R/R المرجّح 60/40 ≥ 1 والوقف ≥ 1.5×ATR
rec = {"verdict": "ENTER", "size_pct": 20, "direction": "long", "sl": 95, "tp1": 106, "tp2": 110, "atr_pct": 2}
ok, why, rr = AT.long_gate_b68(rec, 100)
chk("G1 شراء مقبول", ok and abs(rr - (0.6 * 6 + 0.4 * 10) / 5) < 1e-9, (ok, why, rr))
ok, why, _ = AT.long_gate_b68(dict(rec, atr_pct=4), 100)   # الوقف يتسع إلى 6% ⇒ R/R 1.27
chk("G2 الوقف ≥ 1.5×ATR", ok)
ok, why, _ = AT.long_gate_b68(dict(rec, tp1=102, tp2=103), 100)
chk("G3 R/R < 1 مرفوض", not ok and "R/R" in why, why)
chk("G4 انتظار مرفوض", AT.long_gate_b68(dict(rec, size_pct=0), 100)[1] == "انتظار")
chk("G5 AUTO-FAIL مرفوض بشرح", AT.long_gate_b68(dict(rec, verdict="AUTO-FAIL", rr=0.85), 100)[1] == "بنية الصفقة فاشلة (AUTO-FAIL): R/R المرجّح 1:0.85 < 1:1 — الأهداف قريبة مقارنة بالوقف")
ok, why, rr = AT.short_gate_b68({"eligible": True, "sl": 105, "tp1": 94, "tp2": 90}, 100)
chk("G6 Short مقبول", ok and rr > 1, (why, rr))
chk("G7 Short غير مؤهل يعيد السبب", AT.short_gate_b68({"eligible": False, "reason": "تأكيدات Short 0/4"}, 100)[1] == "تأكيدات Short 0/4")
chk("G8 الحجم يحترم سقف الحدث", AT.size_usd_b68(10000, 1, 0.5) == AT.size_usd_b68(10000, 1, 1) / 2)
chk("G9 الكون الآلي 13 أصلاً إضافياً منها 10 أسهم", len(AT.AUTO_EXTRA) == 13 and len(AT.AUTO_STOCKS) == 10)

# معايير القياس بلا صفقات
st = AT.auto_stats_b68([])
dd = [c for c in st["criteria"] if "أقصى تراجع" in c[0]][0]
chk("H1 «أقصى تراجع» غير متحقق بلا صفقات", dd[1] is False and dd[2] == "—", dd)
chk("H1b غير جاهز بلا صفقات", not st.get("ready"))

# سطر المسح: الأقرب للدخول — التأكيدات أولاً ثم الميل (الدفعة 74)
sm = {"users": 1, "universe": 33, "scanned_syms": ["X"] * 33, "long": 0, "short": 0}
AT._near_add_b73(sm, "GOOGL", "Short", 0.75, "تأكيدات Short 1/4 (الحد الأدنى 2)")
AT._near_add_b73(sm, "LINK", "Short", 0.74, "تأكيدات Short 0/4 (الحد الأدنى 2)")
AT._near_add_b73(sm, "LTC", "Short", 0.74, "تأكيدات Short 1/4 (الحد الأدنى 2)")
AT._near_add_b73(sm, "BNB", "Short", 0.80, "R/R")
AT._near_add_b73(sm, "GOOGL", "Short", 0.99, "مكرر")
ln = AT.scan_line_b68(sm)
t = ln.split("🔎")[1] if "🔎" in ln else ""
chk("H2 الأقرب للدخول: التأكيدات ثم الميل", t.index("GOOGL") < t.index("LTC") < t.index("BNB") and "LINK" not in t, ln)
chk("H2b بلا تكرار للأصل", t.count("GOOGL") == 1)
chk("H2c السطر يذكر 33 من 33", "فُحص 33 من 33" in ln, ln)
sm2 = dict(sm, short=1)
chk("H2d لا «الأقرب» عند فتح صفقة", "🔎" not in AT.scan_line_b68(sm2))
chk("H2e سقف الحدث يظهر", "سقف التعرض 50%" in AT.scan_line_b68(dict(sm, ev_mult=0.5)))

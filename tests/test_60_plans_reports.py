"""الخطط والتقارير الدورية والمراجعة الأسبوعية و/start والمجدول (الدفعات 71 و73)."""
from _util import chk, src
import asyncio, datetime, re
from unittest import mock

import handlers.plan as P
w = P._weeks_signal_gate_b73(["• أسبوع 1: مراقبة — RSI السوق المرجعي (BTC) = 36",
                              "• أسبوع 2: دخول تدريجي عند ارتداد RSI فوق 35",
                              "• أسبوع 3: Fear & Greed < 25 → 🟢 ابدأ التجميع التدريجي (x)",
                              "• أسبوع 4: تقييم القاع — قرار الدخول الكامل"])
j = "\n".join(w)
chk("P1 الأسابيع بحكم /signal فقط", "دخول تدريجي عند" not in j and "قرار الدخول الكامل" not in j and "بحكم /signal" in w[-1], w)
chk("P2 سطر الحكم يُضاف عند غيابه", P._weeks_signal_gate_b73(["• أسبوع 4: مراجعة"])[-1].startswith("• ⚖️ كل دخول بحكم /signal فقط"))
p = src("handlers/plan.py")
chk("P3 RSI السوق المرجعي", "مراقبة — RSI السوق المرجعي (BTC) = {_rsi_pw:.0f}" in p)
chk("P4 اتباع الاتجاه RSI > 45 دائماً", "_rsi_t_w = 45\n" in p)
chk("P5 «تتطلب ثقة 65%» مع غير مؤكد فقط", "and not _bear_w57 and signal.confidence > 1 - SIGNAL_DIRECTION_MIN_CONF" in p)
chk("P6 الهبوط ضعيف القوة لا غائب", "الهبوط ضعيف القوة لا غائب" in p)

# التقرير الدوري: القياس منفصل عن اليدوي، والملاحظات غير المعروضة فقط
import core.period_report as PR
now = datetime.datetime(2026, 11, 16, 10, 0, tzinfo=datetime.timezone.utc)
ts = (now - datetime.timedelta(days=2)).isoformat()
vw = {"history": [{"type": "sell", "symbol": "ETH", "pnl": -60, "pnl_pct": -63.5, "time": ts},
                  {"type": "sell", "symbol": "BTC", "pnl": 5, "pnl_pct": 2.0, "time": ts, "logic_ver": "b68"}],
      "balance": 10000, "positions": {}}
cm = [{"text": "قديمة", "ts": datetime.datetime(2026, 11, 9, tzinfo=datetime.timezone.utc).timestamp()},
      {"text": "جديدة معروضة", "ts": (now - datetime.timedelta(days=1)).timestamp(), "reported": True},
      {"text": "جديدة", "ts": (now - datetime.timedelta(days=1)).timestamp()}]
rep = PR.build_user_report(1, "weekly", vw, None, comments=cm, now=now)
chk("R1 قسم القياس منفصل", "🤖 *القياس الآلي" in rep and "✋ *صفقات افتراضية يدوية" in rep, rep)
chk("R2 القياس يضم صفقة b68 فقط", rep.split("✋")[0].count("صفقات مغلقة: 1") == 1, rep)
chk("R3 الملاحظات غير المعروضة فقط", "• جديدة" in rep and "قديمة" not in rep and "جديدة معروضة" not in rep, rep)
chk("R4 مجموع الصفقات", len(PR.virtual_closed(vw, 0, 2e9)) == 2 and len(PR.virtual_closed(vw, 0, 2e9, auto=True)) == 1)

# المراجعة الأسبوعية
import core.weekly_review as WR
chk("V1 لا «جاهز للإطلاق الكامل»", "جاهز للإطلاق الكامل" not in src("core/weekly_review.py"))
rpt = WR.agent_report_compile({"status": "ok", "warnings": [], "findings": []},
                              {"score": 90, "critical": [], "warnings": []},
                              {"score": 75, "critical": [], "warnings": ["⚠️ x.py: غير محمَّل"], "ok": ["a"]},
                              {"score": 100, "summary": "19/19 اختبار نجح", "failed": []}, None, [])["report_text"]
chk("V2 سبب درجة AgentCode", "أهمها: ⚠️ x.py: غير محمَّل" in rpt, rpt)
chk("V3 AgentQA فحص داخلي", "ليس اختبارات المشروع" in rpt, rpt)
chk("V4 توقيت السعودية", "بتوقيت السعودية" in rpt, rpt)
chk("V5 التوصية مرهونة بالقياس", "مرهون بالقياس الآلي" in rpt, rpt)
async def _okx(prev):
    data = {"code": "0", "data": [{"instId": i} for i in ("XRP-USDT", "XLM-USDT", "XCH-USDT", "XAUT-USDT",
                                                          "XAAPL-USDT", "XNVDA-USDT", "XTSLA-USDT", "BTC-USDT")]}
    with mock.patch.object(WR, "_fetch_json", side_effect=lambda req, t: data if "instruments" in req.full_url else {}):
        return await WR.agent_okx_review(prev)
r1 = asyncio.run(_okx(None))
chk("V6 الأسهم المُرمَّزة فقط", r1.get("x_list") == ["XAAPL-USDT", "XNVDA-USDT", "XTSLA-USDT"], r1.get("x_list"))
chk("V7 أول قائمة بلا «جديدة»", not r1.get("new_features"), r1)
r2 = asyncio.run(_okx(["XAAPL-USDT", "XNVDA-USDT"]))
chk("V8 «جديدة» = الفرق عن الأسبوع السابق", any("XTSLA-USDT" in x and "XAAPL" not in x for x in r2.get("new_features", [])), r2)

# /start
import main as M
m = src("main.py")
chk("S1 نص الترحيب يطابق النظام", "تنبيه فوري عند فرصة" not in m and "تنبيه ساعي عند إشارة BTC/ETH/SOL/BNB بثقة ≥ 80%" in m)
chk("S2 /stats مرة واحدة", '"/stats — يتضمن حالة النظام للمالك"' not in m)
def _md_ok(t):
    # Markdown تيليجرام القديم: عدد * و_ و` غير المهرَّبة زوجي
    u = re.sub(r"\\[_*`\[]", "", t)
    return all(u.count(c) % 2 == 0 for c in "*_`")
import core.access_guard as G
with mock.patch.object(G, "is_owner", return_value=True), mock.patch.object(G, "is_admin", return_value=True):
    try:
        txt = M._build_commands_text(6291447764, "diamond", "ماسي")
        chk("S3 «أوامرك» للمالك Markdown سليم", _md_ok(txt) and "/scanstatus" in txt, txt[-400:])
    except Exception as e:
        chk("S3 «أوامرك» للمالك Markdown سليم", False, e)

# المجدول
import core.scheduler as SC
chk("C1 ست جلسات مسح", SC.SCAN_HOURS_UTC == {22, 2, 6, 10, 14, 18})
s = SC.Scheduler.__new__(SC.Scheduler)
fixed = datetime.datetime(2026, 11, 12, 6, 9, tzinfo=datetime.timezone.utc)
class _DT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return fixed
with mock.patch.object(SC, "datetime", _DT):
    t = SC.Scheduler.next_scan_ar(s)
chk("C2 المسح القادم بعد 3 ساعات و51 دقيقة", "13:00 KSA" in t and "3 ساعات و51 دقيقة" in t, t)
chk("C3 مهلة المسح 25 دقيقة", SC.Scheduler.SCAN_TIMEOUT_SEC == 1500)
chk("C4 الملاحظات تُعلَّم بعد التقرير", "mark_comments_reported(uid" in src("core/scheduler.py"))

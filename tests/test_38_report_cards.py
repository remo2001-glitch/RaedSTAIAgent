"""بطاقات الخطط و/liquidity و/onchain و/regime (الدفعة 84، البند Y المرحلة 3) — عرض فقط."""
import os
from _util import chk, src, ROOT
from core.report_cards_b84 import split_full_b84, plan_week_card_b84, plan_month_card_b84, DISC_B84

chk("P0 «full» ليس رمزاً", split_full_b84(["BTC", "full"]) == (["BTC"], True) and split_full_b84(["كامل"]) == ([], True)
    and split_full_b84(["ETH", "SOL"]) == (["ETH", "SOL"], False))
fx = os.path.join(ROOT, "tests", "fixtures")
w_full = open(os.path.join(fx, "planweek_b84.txt"), encoding="utf-8").read()
w = plan_week_card_b84(w_full)
wl = w.split("\n")
chk("P1 /planweek ≤ 45 سطراً بدل ≈ 180", len(wl) <= 45 and len(w_full.split("\n")) > 150, (len(wl), len(w_full.split("\n"))))
chk("P2 القرار الأسبوعي والاتساع", "🎯 *القرار الأسبوعي:* ⚪ *انتظار*" in w and "اتساع السوق يخالف الاتجاه" in w)
chk("P3 سطر لكل أصل + المستويات", "💎 *AVAX* — $7.289 (+2.85%) · ⚪ انتظار · ثقة 39% · جاهزية 0/4" in w
    and "   📊 دعم $6.933 · مقاومة $7.4609 · ATR 8.8%" in w and w.count("💎 *") == 10, w)
chk("P4 الحركة الحادة موسومة", "💎 *XRP* — $0.967300 (+6.67%) · ⚪ انتظار · ثقة 30% · جاهزية 0/4 · ⚠️ حركة حادة" in w)
chk("P5 الأسابيع والأحداث تبقى", "📅 *خطة الأسابيع الأربعة" in w and "• أسبوع 4:" in w and "📅 *أحداث الأسبوع*" in w and "أقرب حدث كبير" in w)
chk("P6 التنبؤ والشروح في التفاصيل", "🔮" not in w and "السيناريوهات الـ 30" not in w and "خيار المحترف" not in w
    and "عتبات RSI" not in w and "رؤية المؤسسات" not in w and "شروط الدخول:" not in w)
chk("P7 الترتيب كما في الكامل وآخر سطر التنبيه", w.index("*AVAX*") < w.index("*TRX*") < w.index("*LINK*") and wl[-1] == DISC_B84)
ready = ("💎 *SOL* — $72.16 (+3.44%)\n  الإشارة: 🟢 شراء | الثقة: 68%\n  🏷️ الجاهزية: 🟢 جاهزة (4/4 شروط)\n"
         "  ⚡ خيار المحترف: دخول $70.00 | وقف $64.00 | TP1 $78.00\n\n📅 *أحداث الأسبوع*\n✅ لا أحداث")
chk("P8 الأصل الجاهز يبقى بكامل كتلته", "⚡ خيار المحترف: دخول $70.00 | وقف $64.00 | TP1 $78.00" in plan_week_card_b84(ready))
m_full = open(os.path.join(fx, "planmonth_b84.txt"), encoding="utf-8").read()
m = plan_month_card_b84(m_full)
chk("P9 /planmonth: المصفوفة سطر الوضع الحالي فقط", "◀ الوضع الحالي" in m and "🟢 إذا Fear < 30" not in m and "↳ الحكم الحالي" in m, m)
chk("P10 /planmonth: التوزيع والجدول والأحداث تبقى، والتنبؤ يُنقل", "لا توجد أصول مؤهلة للتوزيع" in m and "📅 *جدول الشهر المقترح*" in m
    and "📅 *أحداث الشهر*" in m and "🔮" not in m and "Elliott" not in m and len(m.split("\n")) < len(m_full.split("\n")))
a = src("handlers/analysis.py"); p = src("handlers/plan.py")
chk("P11 الخطط: بطاقة + الأزرار الأصلية + التفاصيل", '_pwc84(_pw_text), _pw_text, "الخطة الأسبوعية", "planweek", markup=_fb_kb' in p
    and '"الخطة الشهرية", "planmonth", markup=_fb_kb' in p and "args, _full84w = _sf84w" in p and "args, _full84m = _sf84m" in p)
chk("P12 /liquidity و/onchain و/regime: بطاقة والكامل كما كان (raw)", a.count("raw=True)") == 3 and 'if ent.get("raw"):' in a
    and "/liquidity {symbol} full" in a and "/onchain full" in a and "/regime {symbol} full" in a)
chk("P13 فشل البطاقة يرسل الكامل", all(x in a + p for x in ("liquidity card b84", "onchain card b84", "regime card b84", "planweek card b84", "planmonth card b84")))

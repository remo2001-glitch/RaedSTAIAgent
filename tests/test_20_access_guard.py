"""حماية البوت (الدفعة 69): الأوامر المخفية، الإغراق، الحد اليومي، تنقية الأخطاء، الوضع الخاص."""
from _util import chk, src
from unittest import mock
import core.access_guard as G

OWNER, ADMIN, USER = 6291447764, 6747412518, 979722641
with mock.patch.object(G, "owner_id", return_value=OWNER), \
     mock.patch.object(G, "all_admins", return_value={OWNER, ADMIN}), \
     mock.patch.object(G, "is_owner", side_effect=lambda u: int(u) == OWNER), \
     mock.patch.object(G, "is_admin", side_effect=lambda u: int(u) in (OWNER, ADMIN)):
    chk("A1 parse_command", G.parse_command("/Signal@RaedBot BTC") == ("signal", ["BTC"]) and G.parse_command("hi") == (None, []))
    for c in ("killswitch", "drift", "whaletest", "survey_report", "review", "scanstatus"):
        chk(f"A2 /{c} للمالك فقط", G.command_visible(OWNER, c, []) and not G.command_visible(ADMIN, c, []) and not G.command_visible(USER, c, []))
    chk("A3 /access للمدراء", G.command_visible(ADMIN, "access", []) and not G.command_visible(USER, "access", []))
    chk("A4 /premium بلا خيارات للجميع، بخيارات للمدراء", G.command_visible(USER, "premium", []) and not G.command_visible(USER, "premium", ["list"]))
    chk("A5 /autotrade users للمدراء", not G.command_visible(USER, "autotrade", ["users"]) and G.command_visible(USER, "autotrade", ["on"]))
    chk("A6 /autostats ID للمدراء", not G.command_visible(USER, "autostats", ["123"]) and G.command_visible(USER, "autostats", []))
    chk("A7 /signal ظاهر للجميع", G.command_visible(USER, "signal", ["BTC"]))
    # الإغراق: 6 تحديثات في 20 ث مسموحة، السابع يُغلق 10 دقائق؛ المدراء مستثنون
    G._hits.clear(); G._muted.clear()
    r = [G.flood_check(USER, now=1000 + i) for i in range(7)]
    chk("A8 الإغراق: السابع يُغلق", all(x == (True, False) for x in r[:6]) and r[6] == (False, True), r)
    chk("A9 مغلق أثناء الحظر", G.flood_check(USER, now=1100) == (False, False))
    chk("A10 يُفتح بعد 10 دقائق", G.flood_check(USER, now=1000 + 6 + 601) == (True, False))
    chk("A11 المدراء بلا إغراق", all(G.flood_check(ADMIN, now=2000 + i)[0] for i in range(20)))
    # الحد اليومي
    store = {}
    class _SM:
        def kv_get(self, k, d=None): return store.get(k, d)
        def kv_set(self, k, v): store[k] = v
    with mock.patch.object(G, "_sm", return_value=_SM()):
        took = [G.quota_take(USER) for _ in range(6)]
        chk("A12 حد 5 تحليلات يومياً", took == [True] * 5 + [False], took)
        chk("A13 المدراء بلا حد", all(G.quota_take(ADMIN) for _ in range(10)))
    chk("A14 رد «غير متاح» مرة كل 12 ساعة", G.should_reply_deny(USER, now=1.8e9) and not G.should_reply_deny(USER, now=1.8e9 + 100) and G.should_reply_deny(USER, now=1.8e9 + 12 * 3600 + 1))
    G._probes.clear(); G._probe_notified.clear()
    chk("A15 إشعار المالك بعد 3 محاولات مخفية مرة واحدة", [G.record_probe(USER) for _ in range(4)] == [False, False, True, False])
chk("A16 تنقية سطر الخطأ", G.sanitize_text("نتيجة\n❌ خطأ: 'dict' object has no attribute 'user_id'\nنهاية") == "نتيجة\n" + G._GENERIC + "\nنهاية")
chk("A17 نص عادي لا يتغيّر", G.sanitize_text("📡 إشارة رائد — BTC") == "📡 إشارة رائد — BTC")
chk("A18 Traceback يُحجب", "Traceback" not in G.sanitize_text("Traceback (most recent call last):\n  File \"x.py\", line 3, in f"))
pub = {c for c, _ in G.PUBLIC_MENU}
chk("A19 قائمة العامة بلا أوامر مدير", not (pub & (G.OWNER_COMMANDS | G.ADMIN_COMMANDS | {"stats_admin"})), pub)
chk("A20 الأوامر التحليلية محسوبة في الحد", {"signal", "analyze", "planweek", "liquidity"} <= G.ANALYSIS_COMMANDS)
a = src("handlers/access.py")
chk("A21 مغادرة القنوات والمجموعات دائماً", "chat.type != ChatType.PRIVATE" in a and "_leave(" in a)
chk("A22 البوابة قبل كل المعالجات", "group=-100" in a)
Bot = G.make_guarded_bot_class()
chk("A23 GuardedBot يغلّف الإرسال", all(hasattr(Bot, m) for m in ("send_message", "edit_message_text", "send_photo", "send_document", "answer_callback_query")))

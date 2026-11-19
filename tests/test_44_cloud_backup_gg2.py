"""GG2 (رحال 2026-11-19 «أ»): نسخ احتياطي سحابي لبيانات القياس إلى مستودع GitHub خاص — خادم GitHub وهمي محلي (aiohttp)."""
import asyncio
import base64
import gzip
import json
import os
import types
from _util import chk, src
import core.cloud_backup_gg2 as B

# ── حالة وهمية (بدل Redis) ─────────────────────────────────────────
KV = {}
LED = {"autoledger_b68:111": json.dumps([{"ev": "open", "pos_id": "p1", "symbol": "ETH", "entry": 1765.96, "e_rsi": 41},
                                         {"ev": "close", "pos_id": "p0", "symbol": "XRP", "pnl": -25.35, "reason": "sl", "mfe_pct": 1.2}])}
WAL = {111: {"balance": 9000, "futures": {"AUTO:ETH": {"logic_ver": "b68", "entry": 1765.96, "side": "long"},
                                          "SOL": {"entry": 100, "side": "long"}},   # مركز يدوي — لا يُنسخ
             "api_keys": {"okx": "ENCRYPTED"}}}
fake_sm = types.SimpleNamespace(
    kv_get=lambda k, d=None: KV.get(k, LED.get(k, d)), kv_set=lambda k, v: KV.__setitem__(k, str(v)),
    get_autotrade_users=lambda: [111], get_virtual_wallet=lambda u: WAL.get(u))
B._sm = lambda: fake_sm
import core.auto_trader_b68 as AT
AT._sm = lambda: fake_sm


class _Tr:
    def get_all_signal_ids(self, limit=0): return ["a", "b"]
    def _redis(self):
        return types.SimpleNamespace(mget=lambda keys: [json.dumps({"signal_id": k[-1], "symbol": "BTC"}) for k in keys])


ENG = types.SimpleNamespace(signal_tracker=_Tr())

# ── خادم GitHub وهمي ───────────────────────────────────────────────
from aiohttp import web
FILES, CALLS = {}, []
MODE = {"fail": False, "stale_once": False}


async def contents(req):
    path = req.match_info["path"]
    CALLS.append((req.method, path, req.headers.get("Authorization")))
    if MODE["fail"]:
        return web.json_response({"message": "Bad credentials"}, status=401)
    if req.method == "GET":
        return web.json_response({"sha": FILES[path]["sha"]}) if path in FILES else web.json_response({}, status=404)
    body = await req.json()
    cur = FILES.get(path)
    if MODE["stale_once"] and cur:
        MODE["stale_once"] = False
        return web.json_response({"message": "sha mismatch"}, status=409)
    if cur and body.get("sha") != cur["sha"]:
        return web.json_response({"message": "sha wasn't supplied"}, status=422)
    sha = f"s{len(CALLS)}"
    FILES[path] = {"sha": sha, "data": base64.b64decode(body["content"]), "msg": body["message"]}
    return web.json_response({"content": {"sha": sha}}, status=200 if cur else 201)


async def main():
    app = web.Application()
    app.router.add_route("*", "/repos/o/RaedBackup/contents/{path:.*}", contents)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    B.API = f"http://127.0.0.1:{port}"

    os.environ.pop("BACKUP_GH_TOKEN", None)
    r0 = await B.run_backup_gg2(ENG)
    chk("G1 بلا إعداد: لا رفع ولا خطأ تشغيل", "غير مُعدّ" in r0["error"] and not CALLS and "غير مُعدّ" in B.status_text_gg2())

    os.environ.update(BACKUP_GH_TOKEN="tok", BACKUP_GH_REPO="o/RaedBackup")
    T0 = 1795600000.0
    r1 = await B.run_backup_gg2(ENG, now=T0)
    chk("G2 أول تشغيل: latest + اليومي + الإشارات", r1["uploaded"] == ["measurement/latest.json", "measurement/daily/2026-11-25.json",
                                                                    "signals/latest.json.gz"] and not r1["error"], r1)
    m = json.loads(FILES["measurement/latest.json"]["data"])
    u = m["users"]["111"]
    chk("G3 المحتوى: السجل كاملاً (لقطة الدخول وMFE) + مراكز القياس فقط", len(u["ledger"]) == 2 and u["ledger"][0]["e_rsi"] == 41
        and u["ledger"][1]["mfe_pct"] == 1.2 and list(u["open_positions"]) == ["futures:AUTO:ETH"], u)
    raw = json.dumps(m)
    chk("G4 لا مفاتيح منصات ولا مراكز يدوية", "ENCRYPTED" not in raw and "api_keys" not in raw and "SOL" not in raw)
    chk("G5 التوكن في الترويسة", all(c[2] == "Bearer tok" for c in CALLS))
    sg = json.loads(gzip.decompress(FILES["signals/latest.json.gz"]["data"]))
    chk("G6 سجل الإشارات مضغوطاً", sg["count"] == 2 and sg["signals"][0]["symbol"] == "BTC")

    n = len(CALLS)
    r2 = await B.run_backup_gg2(ENG, now=T0 + 1800)
    chk("G7 بلا تغيّر: لا رفع", r2["uploaded"] == [] and len(CALLS) == n, r2)

    LED["autoledger_b68:111"] = json.dumps(json.loads(LED["autoledger_b68:111"]) + [{"ev": "close", "pos_id": "p1", "reason": "time"}])
    r3 = await B.run_backup_gg2(ENG, now=T0 + 3600)
    chk("G8 إغلاق صفقة ⇒ تحديث latest بالـ sha", r3["uploaded"] == ["measurement/latest.json"]
        and len(json.loads(FILES["measurement/latest.json"]["data"])["users"]["111"]["ledger"]) == 3, r3)

    MODE["stale_once"] = True
    r4 = await B.run_backup_gg2(ENG, force=True, now=T0 + 3700)
    chk("G9 sha قديم ⇒ يقرأ الحالي ويعيد", not r4["error"] and "measurement/latest.json" in r4["uploaded"], r4)

    r5 = await B.run_backup_gg2(ENG, now=T0 + 86400)
    chk("G10 يوم جديد: لقطة يومية جديدة + الإشارات", "measurement/daily/2026-11-26.json" in r5["uploaded"]
        and "signals/latest.json.gz" in r5["uploaded"] and "measurement/daily/2026-11-25.json" in FILES, r5)

    MODE["fail"] = True
    LED["autoledger_b68:111"] = json.dumps(json.loads(LED["autoledger_b68:111"]) + [{"ev": "open", "pos_id": "p9"}])
    r6 = await B.run_backup_gg2(ENG, now=T0 + 90000)
    st = B.status_get()
    chk("G11 الفشل يُسجَّل ولا يُكسر شيء", "401" in r6["error"] and st["last_error"] and st["last_ok"] == T0 + 86400, (r6, st))
    MODE["fail"] = False
    r7 = await B.run_backup_gg2(ENG, now=T0 + 90100)
    chk("G12 بعد الفشل: التغيير يُرفع في المحاولة التالية", r7["uploaded"] == ["measurement/latest.json"]
        and len(json.loads(FILES["measurement/latest.json"]["data"])["users"]["111"]["ledger"]) == 4, r7)
    txt = B.status_text_gg2(r7, now=T0 + 90100)
    chk("G13 نص /backup", "o/RaedBackup" in txt and "سجل القياس: 4 سجل" in txt and "مراكز مفتوحة: 1" in txt, txt)
    await runner.cleanup()

asyncio.run(main())

m = src("main.py")
chk("G14 المهمة كل 30 دقيقة والأمر للمالك", "run_repeating(cloud_backup_job_gg2, interval=1800" in m and 'CommandHandler("backup", cmd_backup_gg2)' in m)
import core.access_guard as G
chk("G15 /backup ضمن أوامر المالك فقط", "backup" in G.OWNER_COMMANDS and "backup" not in {c for c, _ in G.PUBLIC_MENU})
chk("G16 لا يمس منطق القياس", "cloud_backup" not in src("core/auto_trader_b68.py"))

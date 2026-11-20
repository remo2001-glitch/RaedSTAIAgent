"""
core/cloud_backup_gg2.py — نسخ احتياطي سحابي لبيانات القياس (GG2، قرار رحال 2026-11-19 «أ»)

الوجهة: مستودع GitHub خاص مستقل (لا مستودع الكود) عبر GitHub Contents API — كل رفع = commit
فيبقى تاريخ النسخ كاملاً ويمكن استرجاع أي نسخة.

المتغيرات (Railway):
  BACKUP_GH_TOKEN  توكن fine-grained صلاحيته Contents: Read and write على مستودع النسخ فقط
  BACKUP_GH_REPO   owner/repo  (مثال: remo2001-glitch/RaedBackup)
  BACKUP_GH_BRANCH اختياري (الافتراضي: الفرع الافتراضي للمستودع)
بدونها: لا شيء يحدث (no-op) ويُذكر ذلك في /backup.

الملفات:
  measurement/latest.json            سجل القياس + مراكز القياس المفتوحة — يُرفع عند أي تغيّر (فحص كل 30 دقيقة)
  measurement/daily/YYYY-MM-DD.json  لقطة يومية (UTC) — مرة واحدة يومياً
  signals/latest.json.gz             سجل الإشارات (/performance) مضغوطاً — مرة يومياً

يُستبعد دائماً: بيانات المستخدمين (الباقات، الإعدادات) ومفاتيح المنصات — حتى المشفّرة.
تخزين فقط: لا يقرأ منه البوت ولا يؤثر على أي قرار أو خروج.
"""
from core.ar_count import ar_n   # QQ2 (b98): صيغة العدّ العربية
import base64
import gzip
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

API = "https://api.github.com"
KV_STATUS = "backup_gg2:status"
PATH_LATEST = "measurement/latest.json"
PATH_SIGNALS = "signals/latest.json.gz"
SCHEMA = 1


def _sm():
    from core.state_manager import state_manager
    return state_manager


def config_gg2() -> dict:
    return {"token": (os.environ.get("BACKUP_GH_TOKEN") or "").strip(),
            "repo": (os.environ.get("BACKUP_GH_REPO") or "").strip().strip("/"),
            "branch": (os.environ.get("BACKUP_GH_BRANCH") or "").strip()}


def configured_gg2(cfg: dict = None) -> bool:
    c = cfg or config_gg2()
    return bool(c["token"]) and c["repo"].count("/") == 1


# ── الحالة (Redis kv) ───────────────────────────────────────────────
def status_get() -> dict:
    try:
        return json.loads(_sm().kv_get(KV_STATUS, "{}") or "{}")
    except Exception:
        return {}


def _status_put(st: dict) -> None:
    try:
        _sm().kv_set(KV_STATUS, json.dumps(st, ensure_ascii=False))
    except Exception as e:
        logger.debug(f"backup status: {e}")


# ── جمع البيانات ────────────────────────────────────────────────────
def _measurement_uids() -> list:
    uids = set()
    try:
        uids.update(int(u) for u in (_sm().get_autotrade_users() or []))
    except Exception:
        pass
    try:
        from core.access_guard import owner_id
        if owner_id():
            uids.add(int(owner_id()))
    except Exception:
        pass
    return sorted(uids)


def _measurement_positions(uid: int) -> dict:
    """مراكز القياس المفتوحة فقط (logic_ver) — لا بقية المحفظة."""
    out = {}
    try:
        wd = _sm().get_virtual_wallet(uid) or {}
        for book in ("futures", "positions"):
            for k, p in (wd.get(book) or {}).items():
                if isinstance(p, dict) and p.get("logic_ver"):
                    out[f"{book}:{k}"] = p
    except Exception as e:
        logger.debug(f"backup positions {uid}: {e}")
    return out


def build_measurement_gg2(uids: list = None) -> dict:
    from core.auto_trader_b68 import ledger_get, AUTO_LOGIC_VER
    users = {}
    for uid in (uids if uids is not None else _measurement_uids()):
        led, pos = ledger_get(uid), _measurement_positions(uid)
        if led or pos:
            users[str(uid)] = {"ledger": led, "open_positions": pos}
    return {"schema": SCHEMA, "kind": "measurement", "logic_ver": AUTO_LOGIC_VER, "users": users}


def build_signals_gg2(engine=None, limit: int = 20000) -> dict:
    recs = []
    try:
        tr = getattr(engine, "signal_tracker", None) if engine else None
        if tr is None:
            from core.signal_tracker import signal_tracker as tr   # المثيل العام إن وُجد
        r = tr._redis()
        if r is not None:
            from core.signal_tracker import REDIS_KEY_PREFIX
            ids = tr.get_all_signal_ids(limit=limit) or []
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for raw in r.mget([f"{REDIS_KEY_PREFIX}{x}" for x in chunk]):
                    if raw:
                        try:
                            recs.append(json.loads(raw))
                        except Exception:
                            pass
    except Exception as e:
        logger.debug(f"backup signals: {e}")
    return {"schema": SCHEMA, "kind": "signals", "count": len(recs), "signals": recs}


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _stamp(obj: dict) -> bytes:
    out = dict(obj, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return json.dumps(out, ensure_ascii=False, indent=1, default=str).encode("utf-8")


# ── GitHub Contents API ─────────────────────────────────────────────
async def _gh(session, method, url, token, **kw):
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "raed-backup"}
    hdr.update(kw.pop("headers", {}))
    async with session.request(method, url, headers=hdr, **kw) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {}
        return resp.status, body or {}


async def put_file_gg2(session, cfg: dict, path: str, data: bytes, message: str, sha_cache: dict) -> str:
    """يرفع ملفاً (إنشاء أو تحديث) ويعيد sha الجديد. يرفع استثناءً بسبب واضح عند الفشل."""
    url = f"{API}/repos/{cfg['repo']}/contents/{path}"
    body = {"message": message, "content": base64.b64encode(data).decode()}
    if cfg.get("branch"):
        body["branch"] = cfg["branch"]
    for attempt in (1, 2):
        sha = sha_cache.get(path)
        if sha:
            body["sha"] = sha
        else:
            body.pop("sha", None)
        st, js = await _gh(session, "PUT", url, cfg["token"], json=body)
        if st in (200, 201):
            new = ((js.get("content") or {}).get("sha")) or ""
            sha_cache[path] = new
            return new
        if st in (409, 422) and attempt == 1:   # sha قديم/مفقود ⇒ نقرأ sha الحالي ونعيد
            q = {"ref": cfg["branch"]} if cfg.get("branch") else None
            st2, js2 = await _gh(session, "GET", url, cfg["token"], params=q,
                                 headers={"Accept": "application/vnd.github.object"})
            sha_cache[path] = js2.get("sha") if st2 == 200 else None
            continue
        raise RuntimeError(f"GitHub {st}: {str(js.get('message') or '')[:120]}")
    raise RuntimeError("GitHub: تعذّر الرفع بعد إعادة المحاولة")


# ── الدورة ───────────────────────────────────────────────────────────
async def run_backup_gg2(engine=None, force: bool = False, session=None, now: float = None) -> dict:
    """يرفع ما تغيّر. force=True: يرفع latest حتى لو لم يتغير. يعيد ملخصاً {uploaded: [...], skipped, error}."""
    cfg = config_gg2()
    st = status_get()
    res = {"uploaded": [], "skipped": [], "error": ""}
    if not configured_gg2(cfg):
        res["error"] = "غير مُعدّ (BACKUP_GH_TOKEN / BACKUP_GH_REPO)"
        return res
    now = now or time.time()
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    shas = dict(st.get("shas") or {})
    if st.get("repo") != cfg["repo"]:
        shas = {}   # مستودع جديد ⇒ لا نثق بالـ sha المخزّن
    own = session is None
    if own:
        import aiohttp
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    try:
        meas = build_measurement_gg2()
        dg = _digest(meas)
        if force or dg != st.get("meas_digest"):
            await put_file_gg2(session, cfg, PATH_LATEST, _stamp(meas), f"measurement latest {day}", shas)
            st["meas_digest"] = dg
            res["uploaded"].append(PATH_LATEST)
        else:
            res["skipped"].append(PATH_LATEST)
        if st.get("daily_day") != day:
            p = f"measurement/daily/{day}.json"
            await put_file_gg2(session, cfg, p, _stamp(meas), f"measurement daily {day}", shas)
            shas.pop(p, None)   # ملف يومي لا يُحدَّث لاحقاً
            st["daily_day"] = day
            res["uploaded"].append(p)
        if force or st.get("sig_day") != day:
            sig = build_signals_gg2(engine)
            await put_file_gg2(session, cfg, PATH_SIGNALS, gzip.compress(_stamp(sig)), f"signals {day} ({sig['count']})", shas)
            st["sig_day"], st["sig_count"] = day, sig["count"]
            res["uploaded"].append(PATH_SIGNALS)
        n_led = sum(len(u.get("ledger") or []) for u in meas["users"].values())
        n_pos = sum(len(u.get("open_positions") or {}) for u in meas["users"].values())
        st.update({"repo": cfg["repo"], "shas": shas, "last_ok": now, "last_error": "", "last_error_ts": 0,
                   "ledger_records": n_led, "open_positions": n_pos})
    except Exception as e:
        res["error"] = str(e)[:200]
        st.update({"repo": cfg["repo"], "shas": shas, "last_error": res["error"], "last_error_ts": now})
        logger.warning(f"cloud backup gg2: {e}")
    finally:
        if own:
            await session.close()
        _status_put(st)
    return res


def _ago(ts: float, now: float) -> str:
    if not ts:
        return "—"
    m = max(0, (now - float(ts)) / 60)
    return f"منذ {m:.0f} دقيقة" if m < 90 else f"منذ {m / 60:.1f} ساعة" if m < 48 * 60 else f"منذ {m / 1440:.1f} يوم"


def status_text_gg2(res: dict = None, now: float = None) -> str:
    now = now or time.time()
    cfg, st = config_gg2(), status_get()
    L = ["☁️ النسخ الاحتياطي السحابي (GitHub)", "━━━━━━━━━━━━━━━━━━"]
    if not configured_gg2(cfg):
        return "\n".join(L + ["⚠️ غير مُعدّ — أضف في Railway: BACKUP_GH_TOKEN و BACKUP_GH_REPO (owner/repo)",
                              "لا يُرفع شيء حتى ذلك؛ البيانات باقية في Redis كما هي."])
    L.append(f"• المستودع: {cfg['repo']}")
    if res is not None:
        if res.get("error"):
            L.append(f"❌ فشل الرفع الآن: {res['error']}")
        else:
            L.append("✅ رُفع الآن: " + ("، ".join(res["uploaded"]) if res["uploaded"] else "لا تغيير منذ آخر نسخة"))
    L.append(f"• آخر نسخة ناجحة: {_ago(st.get('last_ok', 0), now)}")
    if st.get("last_error") and float(st.get("last_error_ts", 0)) > float(st.get("last_ok", 0)):
        L.append(f"• آخر خطأ ({_ago(st.get('last_error_ts', 0), now)}): {st['last_error']}")
    if st.get("last_ok"):
        L.append(f"• سجل القياس: {ar_n(st.get('ledger_records', 0), 'سجل')} · مراكز مفتوحة: {st.get('open_positions', 0)}")
        L.append(f"• سجل الإشارات: {ar_n(st.get('sig_count', 0), 'إشارة')} (آخر رفع {st.get('sig_day', '—')})")
    L.append("ℹ️ تلقائي: سجل القياس عند أي تغيّر (فحص كل 30 دقيقة) + لقطة يومية؛ الإشارات يومياً. لا بيانات مستخدمين ولا مفاتيح.")
    return "\n".join(L)


# ── الاسترجاع (GG3، b91) — سجل القياس فقط، ولا يُستبدل سجل موجود أبداً ─────────────
async def fetch_latest_gg2(session=None, cfg: dict = None) -> dict:
    """يقرأ measurement/latest.json من مستودع النسخ. يرفع استثناءً بسبب واضح عند الفشل."""
    cfg = cfg or config_gg2()
    if not configured_gg2(cfg):
        raise RuntimeError("النسخ السحابي غير مُعدّ")
    own = session is None
    if own:
        import aiohttp
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    try:
        url = f"{API}/repos/{cfg['repo']}/contents/{PATH_LATEST}"
        q = {"ref": cfg["branch"]} if cfg.get("branch") else None
        hdr = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/vnd.github.raw+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "raed-backup"}
        async with session.get(url, headers=hdr, params=q) as resp:
            body = await resp.read()
            if resp.status != 200:
                raise RuntimeError(f"GitHub {resp.status}")
        data = json.loads(body.decode("utf-8"))
        if data.get("kind") != "measurement":
            raise RuntimeError("الملف ليس نسخة قياس")
        return data
    finally:
        if own:
            await session.close()


def restore_plan_gg2(backup: dict) -> list:
    """لكل مستخدم في النسخة: ماذا سيحدث — restore (السجل الحالي فارغ أو بداية مطابقة للنسخة) أو skip (أطول/مختلف)."""
    from core.auto_trader_b68 import ledger_get
    plan = []
    for uid_s, u in ((backup or {}).get("users") or {}).items():
        bak = list(u.get("ledger") or [])
        cur = ledger_get(int(uid_s))
        if len(cur) >= len(bak):
            act = ("same" if len(cur) == len(bak) else "newer") if cur[:len(bak)] == bak else "skip"
        elif cur == bak[:len(cur)]:
            act = "restore"
        else:
            act = "skip"
        plan.append({"uid": int(uid_s), "cur": len(cur), "bak": len(bak), "action": act,
                     "positions": len(u.get("open_positions") or {})})
    return plan


def apply_restore_gg2(backup: dict, plan: list) -> int:
    """يكمل السجلات الناقصة فقط (action=restore). مراكز المحفظة لا تُعدَّل آلياً. يعيد عدد السجلات المضافة."""
    from core.auto_trader_b68 import ledger_get
    added = 0
    users = (backup or {}).get("users") or {}
    for p in plan:
        if p.get("action") != "restore":
            continue
        bak = list((users.get(str(p["uid"])) or {}).get("ledger") or [])
        cur = ledger_get(p["uid"])
        if cur != bak[:len(cur)]:
            continue   # تغيّر أثناء العرض — لا نخاطر
        _sm().kv_set(f"autoledger_b68:{int(p['uid'])}", json.dumps(bak[-5000:], ensure_ascii=False))
        added += len(bak) - len(cur)
    return added


def restore_text_gg2(backup: dict, plan: list, applied: int = None) -> str:
    L = ["♻️ استرجاع سجل القياس من النسخة السحابية", "━━━━━━━━━━━━━━━━━━",
         f"• تاريخ النسخة: {str((backup or {}).get('generated_at') or '—')}"]
    _act = {"restore": "يُستكمل من النسخة", "same": "مطابق — لا حاجة", "newer": "الحالي أحدث من النسخة — لا حاجة",
            "skip": "الحالي يختلف عن النسخة — لا يُمس (يُراجع يدوياً)"}
    for p in plan:
        L.append(f"• الحساب {p['uid']}: الحالي {ar_n(p['cur'], 'سجل')} · النسخة {ar_n(p['bak'], 'سجل')} ⇒ {_act[p['action']]}")
    if applied is None:
        if any(p["action"] == "restore" for p in plan):
            L.append("للتنفيذ: /backup restore confirm")
        else:
            L.append("✅ لا شيء يحتاج استرجاعاً")
    else:
        L.append(f"✅ أُضيف {ar_n(applied, 'سجل')}")
    L.append("ℹ️ لا يُستبدل سجل موجود أبداً، ومراكز المحفظة الافتراضية لا تُعدَّل آلياً.")
    return "\n".join(L)

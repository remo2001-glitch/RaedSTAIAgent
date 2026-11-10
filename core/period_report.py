"""
🤖 رائد التداول الذكي — التقارير الدورية الشخصية (الدفعة 55)

المصدر الوحيد لأرقام التقرير الأسبوعي والشهري لكل مشترك:
• الصفقات الافتراضية: صفقات البيع في VirtualWallet.history للمستخدم (نفس مصدر /stats).
• الصفقات الحقيقية: صفقات OrderManager المغلقة في Redis (raed:trade:{uid}:*).

الحساب بنفس صيغ /stats (فوز = pnl > 0، خسارة = pnl ≤ 0، Profit Factor = إجمالي الربح ÷ إجمالي الخسارة)
كي لا يتناقض التقرير مع /stats. قراءة فقط — لا يغيّر أي رصيد أو صفقة.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

RIYADH = timezone(timedelta(hours=3))

_AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
              "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


# ── الفترة ──────────────────────────────────────────────────────────────────
def period_window(period: str, now: Optional[datetime] = None) -> Tuple[float, float, str]:
    """يعيد (بداية، نهاية) بالثواني UTC + وصف عربي للفترة.
    weekly: آخر 7 أيام حتى الآن. monthly: الشهر الميلادي المنتهي بتوقيت السعودية
    (تقرير 3 نوفمبر يغطي أكتوبر كاملاً)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if period == "monthly":
        loc = now.astimezone(RIYADH)
        cur_first = loc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_last = cur_first - timedelta(days=1)
        prev_first = prev_last.replace(day=1)
        label = f"{_AR_MONTHS[prev_first.month - 1]} {prev_first.year} ({prev_first.strftime('%Y-%m')})"
        return prev_first.timestamp(), cur_first.timestamp(), label
    end = now
    start = now - timedelta(days=7)
    s_loc, e_loc = start.astimezone(RIYADH), end.astimezone(RIYADH)
    label = f"{s_loc.strftime('%Y-%m-%d')} → {e_loc.strftime('%Y-%m-%d')}"
    return start.timestamp(), end.timestamp(), label


def _ts(v) -> Optional[float]:
    """وقت الصفقة: نص ISO أو رقم epoch."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v)
        try:
            return float(s)
        except ValueError:
            pass
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# ── جمع الصفقات ─────────────────────────────────────────────────────────────
def virtual_closed(vw_data: Optional[dict], start: float, end: float, auto: Optional[bool] = None) -> List[dict]:
    """auto=None الكل؛ True صفقات القياس الآلي (logic_ver)؛ False اليدوية فقط (batch73 G1)."""
    out = []
    for t in ((vw_data or {}).get("history") or []):
        if t.get("type") != "sell":
            continue
        if auto is not None and bool(t.get("logic_ver")) != bool(auto):
            continue
        ts = _ts(t.get("time"))
        if ts is None or not (start <= ts < end):
            continue
        out.append({"symbol": t.get("symbol", "-"), "pnl": float(t.get("pnl", 0) or 0),
                    "pnl_pct": float(t.get("pnl_pct", 0) or 0), "ts": ts})
    return out


# batch73 (G2): الملاحظات قبل هذه الدفعة عولجت في دفعات سابقة — لا تُعرض
COMMENTS_CUTOFF_B73 = datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp()


def _keys(r, pattern: str) -> List[str]:
    try:
        ks = list(r.scan_iter(match=pattern, count=200)) if hasattr(r, "scan_iter") else list(r.keys(pattern))
    except Exception:
        try:
            ks = list(r.keys(pattern))
        except Exception:
            return []
    return [k.decode() if isinstance(k, bytes) else str(k) for k in ks]


def real_closed(uid: int, start: float, end: float, r) -> List[dict]:
    out = []
    if r is None:
        return out
    for k in _keys(r, f"raed:trade:{uid}:*"):
        try:
            raw = r.get(k)
            if not raw:
                continue
            d = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            if d.get("status") != "CLOSED":
                continue
            if int(d.get("user_id") or 0) != int(uid):
                continue
            ts = _ts(d.get("closed_at"))
            if ts is None or not (start <= ts < end):
                continue
            out.append({"symbol": d.get("symbol", "-"), "pnl": float(d.get("pnl_usd", 0) or 0),
                        "pnl_pct": float(d.get("pnl_pct", 0) or 0), "ts": ts})
        except Exception:
            continue
    return out


def real_trade_user_ids(r) -> List[int]:
    ids = set()
    if r is None:
        return []
    for k in _keys(r, "raed:trade:*"):
        parts = k.split(":")
        if len(parts) >= 4 and parts[2].isdigit():
            ids.add(int(parts[2]))
    return sorted(ids)


# ── الحساب ──────────────────────────────────────────────────────────────────
def summarize(trades: Iterable[dict]) -> Dict:
    tr = list(trades)
    wins = [t for t in tr if t["pnl"] > 0]
    losses = [t for t in tr if t["pnl"] <= 0]
    n = len(tr)
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in tr)
    return {
        "count": n, "wins": len(wins), "losses": len(losses),
        "net": round(net, 2),
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "avg_win": (gw / len(wins)) if wins else 0.0,
        "avg_loss": (gl / len(losses)) if losses else 0.0,
        "pf": (gw / gl) if gl > 0 else None,
        "expectancy": (net / n) if n else 0.0,
        "best": max(wins, key=lambda t: t["pnl"]) if wins else None,
        "worst": min(losses, key=lambda t: t["pnl"]) if losses else None,
    }


def _usd(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def format_section(title: str, s: Dict) -> List[str]:
    if not s["count"]:
        return [f"{title}", "• لا صفقات مغلقة في هذه الفترة"]
    lines = [
        f"{title}",
        f"• صفقات مغلقة: {s['count']} | رابحة: {s['wins']} | خاسرة: {s['losses']}",
        f"• صافي الربح/الخسارة: {_usd(s['net'])}",
        f"• نسبة الفوز: {s['win_rate']:.1f}%",
        f"• متوسط الربح: ${s['avg_win']:,.2f} | متوسط الخسارة: ${s['avg_loss']:,.2f}",
        f"• Expectancy لكل صفقة: {_usd(s['expectancy'])}",
        ("• Profit Factor: " + (f"{s['pf']:.2f}" if s["pf"] is not None else "∞ (لا خسائر)")),
    ]
    if s["best"]:
        b = s["best"]
        lines.append(f"• أفضل صفقة: {b['symbol']} {b['pnl_pct']:+.1f}% ({_usd(b['pnl'])})")
    if s["worst"]:
        w = s["worst"]
        lines.append(f"• أسوأ صفقة: {w['symbol']} {w['pnl_pct']:+.1f}% ({_usd(w['pnl'])})")
    return lines


def wallet_status_lines(vw_data: Optional[dict]) -> List[str]:
    if not vw_data:
        return []
    try:
        bal = float(vw_data.get("balance", 0) or 0)
        pos = vw_data.get("positions") or {}
        cost = sum(float((p or {}).get("cost", 0) or 0) for p in pos.values())
        return [f"• المحفظة الافتراضية الآن: ${bal + cost:,.2f} (نقد ${bal:,.2f} + مراكز ${cost:,.2f} بالتكلفة)",
                f"• مراكز افتراضية مفتوحة: {len(pos)}"]
    except Exception:
        return []


def build_user_report(uid: int, period: str, vw_data: Optional[dict], redis_client=None,
                      has_exchange: bool = False, comments: Optional[List[dict]] = None,
                      now: Optional[datetime] = None) -> str:
    start, end, label = period_window(period, now)
    is_m = period == "monthly"
    head = "📅 *التقرير الشهري — رائد التداول الذكي*" if is_m else "📊 *التقرير الأسبوعي — رائد التداول الذكي*"
    lines = [head, "━━━━━━━━━━━━━━━━━━",
             (f"📅 الشهر المنتهي: {label}" if is_m else f"📅 الفترة: {label} (آخر 7 أيام)"), ""]
    # batch73 (G1): القياس الآلي (منطق /signal المجمّد) منفصل عن الصفقات اليدوية — كانت اليدوية القديمة (10x) تُخلط بأداء رائد
    va = summarize(virtual_closed(vw_data, start, end, auto=True))
    vm = summarize(virtual_closed(vw_data, start, end, auto=False))
    v = {"count": va["count"] + vm["count"]}
    lines += format_section("🤖 *القياس الآلي — منطق /signal المجمّد (b68)*", va)
    if vm["count"]:
        lines += [""] + format_section("✋ *صفقات افتراضية يدوية (تدريب — ليست أداء رائد)*", vm)
    lines += wallet_status_lines(vw_data)
    rl = real_closed(uid, start, end, redis_client)
    if rl or has_exchange:
        lines += [""] + format_section("💱 *الصفقات الحقيقية*", summarize(rl))
    if v["count"] == 0 and not rl:
        lines += ["", "ℹ️ لم تُغلق أي صفقة خلال هذه الفترة — الأرقام أعلاه ليست أداءً صفرياً بل غياب صفقات."]
    recent = [c for c in (comments or []) if (_ts(c.get("ts")) or 0) >= max(start, COMMENTS_CUTOFF_B73)
              and not c.get("reported")]   # batch73 (G2، قرار رحال): غير المعالجة فقط
    if recent:
        lines += ["", "💬 *ملاحظاتك المسجَّلة*"] + [f"• {str(c.get('text', ''))[:80]}" for c in recent[-3:]]
    lines += ["", "ℹ️ الأرقام من صفقاتك المغلقة في الفترة نفسها وبصيغ /stats ذاتها.",
              "━━━━━━━━━━━━━━━━━━", "🤖 رائد التداول الذكي"]
    return "\n".join(lines)


def eligible_user_ids(sm, redis_client=None, owner_id: Optional[int] = None) -> List[int]:
    """مستلمو التقرير: كل من لديه محفظة افتراضية أو صفقات حقيقية أو حساب منصة مربوط، والمالك دائماً."""
    ids = set()
    try:
        ids.update(int(x) for x in (sm.list_wallet_user_ids() or []))
    except Exception:
        pass
    ids.update(real_trade_user_ids(redis_client))
    try:
        for uid in sm.get_all_user_ids() or []:
            try:
                if (sm._get_user(uid) or {}).get("exchange"):
                    ids.add(int(uid))
            except Exception:
                continue
    except Exception:
        pass
    if owner_id:
        ids.add(int(owner_id))
    return sorted(ids)

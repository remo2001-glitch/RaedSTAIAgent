"""
batch51 — اختبار تاريخي «Whale» (نسبة حسابات Long/Short في OKX) — للأدمن، قراءة فقط.

الغاية (قرار رحال 2026-10-28 بند 9): قبل اعتماد Whale كتأكيد يوافق الاتجاه، نقيس تاريخياً هل نسبة Long/Short
المرتفعة/المنخفضة تسبق عوائد مختلفة عن المتوسط. لا يغيّر أي قرار تداول ولا يكتب شيئاً — يعرض أرقاماً فقط.

الدوال هنا نقية (قابلة للاختبار بلا شبكة)؛ الجلب الفعلي يتم في handlers (aiohttp) بنقطة OKX:
GET /api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1D  →  data: [[ts_ms, ratio], ...] (الأحدث أولاً)
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

HORIZONS = (3, 7)          # أيام العائد اللاحق
HI, LO = 1.2, 0.8          # حدود النظام الحالية (get_whale_ratio): >1.2 أغلبية Long، <0.8 أغلبية Short
MIN_SAMPLES = 15           # أقل عدد عيّنات لعرض متوسط بلا تحفظ


def _ts_ms(c) -> float:
    try:
        v = c.get("timestamp", c.get("ts"))
        v = float(v)
        return v * 1000.0 if v < 1e11 else v
    except (TypeError, ValueError, AttributeError):
        return 0.0


def forward_returns(ratio_pts: Sequence[Tuple[float, float]], candles: Sequence[dict],
                    horizons: Sequence[int] = HORIZONS) -> List[Dict]:
    """لكل قراءة (ts_ms, ratio): الدخول = افتتاح أول شمعة يومية تبدأ بعد القراءة، والخروج = إغلاق شمعة بعد h يوماً.
    تُهمل القراءات التي لا يتوفر لها عائد لاحق كامل (لا استقراء)."""
    cs = [(_ts_ms(c), float(c.get("open", c.get("close", 0)) or 0), float(c.get("close", 0) or 0)) for c in candles]
    cs = [x for x in cs if x[0] > 0 and x[1] > 0 and x[2] > 0]
    cs.sort(key=lambda x: x[0])
    out: List[Dict] = []
    j = 0
    for ts, ratio in sorted(ratio_pts):
        while j < len(cs) and cs[j][0] < ts:
            j += 1
        if j >= len(cs):
            break
        rec = {"ratio": float(ratio)}
        ok = True
        for h in horizons:
            k = j + h - 1
            if k >= len(cs):
                ok = False
                break
            rec[h] = cs[k][2] / cs[j][1] - 1.0
        if ok:
            out.append(rec)
    return out


def bucketize(recs: Sequence[Dict], horizons: Sequence[int] = HORIZONS, hi: float = HI, lo: float = LO) -> Dict[str, Dict]:
    """يجمّع العوائد حسب حالة النسبة: long_crowded / balanced / short_crowded / all."""
    names = {"long_crowded": lambda r: r > hi, "short_crowded": lambda r: r < lo,
             "balanced": lambda r: lo <= r <= hi, "all": lambda r: True}
    res: Dict[str, Dict] = {}
    for nm, pred in names.items():
        sel = [r for r in recs if pred(r["ratio"])]
        row = {"n": len(sel)}
        for h in horizons:
            vals = [r[h] for r in sel]
            row[f"mean_{h}"] = (sum(vals) / len(vals) * 100.0) if vals else None
            row[f"pos_{h}"] = (sum(1 for v in vals if v > 0) / len(vals) * 100.0) if vals else None
        res[nm] = row
    return res


def merge_buckets(all_recs: Sequence[Sequence[Dict]], horizons: Sequence[int] = HORIZONS) -> Dict[str, Dict]:
    flat: List[Dict] = [r for recs in all_recs for r in recs]
    return bucketize(flat, horizons)


def format_report_ar(per_coin: Dict[str, Dict[str, Dict]], pooled: Dict[str, Dict],
                     horizons: Sequence[int] = HORIZONS) -> str:
    def _f(v):
        return "—" if v is None else f"{v:+.2f}%"

    def _p(v):
        return "—" if v is None else f"{v:.0f}%"

    labels = {"long_crowded": "أغلبية Long (>1.2)", "balanced": "متوازن", "short_crowded": "أغلبية Short (<0.8)"}
    lines = ["🐋 *اختبار Whale التاريخي (OKX Long/Short — يومي)*", "━━━━━━━━━━━━━━━━━━",
             "العائد = افتتاح الشمعة التالية للقراءة → إغلاق بعد N أيام. للقراءة فقط ولا يغيّر أي قرار.", ""]
    base = pooled.get("all", {})
    lines.append(f"📊 *الإجمالي (كل العملات)* — عيّنات: {base.get('n', 0)}")
    for h in horizons:
        lines.append(f"• متوسط عائد {h} أيام بلا شرط: {_f(base.get(f'mean_{h}'))} | موجب {_p(base.get(f'pos_{h}'))}")
    for k in ("long_crowded", "balanced", "short_crowded"):
        r = pooled.get(k, {})
        small = " ⚠️ عيّنة صغيرة" if r.get("n", 0) < MIN_SAMPLES else ""
        parts = " | ".join(
            f"{h}د: {_f(r.get(f'mean_{h}'))} (موجب {_p(r.get(f'pos_{h}'))})" for h in horizons)
        lines.append(f"• {labels[k]} — n={r.get('n', 0)}{small}: {parts}")
    lines += ["", "📌 *لكل عملة*"]
    for sym, bk in per_coin.items():
        lc, sc = bk.get("long_crowded", {}), bk.get("short_crowded", {})
        h = horizons[-1]
        lines.append(
            f"• {sym}: n={bk.get('all', {}).get('n', 0)} | Long مزدحم {h}د {_f(lc.get(f'mean_{h}'))} (n={lc.get('n', 0)}) | "
            f"Short مزدحم {h}د {_f(sc.get(f'mean_{h}'))} (n={sc.get('n', 0)})")
    lines += ["", "💡 القراءة: إن كان متوسط «Long مزدحم» أعلى من المتوسط بلا شرط بفارق واضح وبعيّنة كافية فقد يدعم اعتباره تأكيداً صاعداً؛ "
              "إن كان أدنى منه فهو أقرب لتحذير ازدحام. العيّنات الصغيرة لا يُبنى عليها قرار."]
    return "\n".join(lines)

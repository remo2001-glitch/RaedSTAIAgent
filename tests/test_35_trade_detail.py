"""تفاصيل صفقة القياس الكاملة (الدفعة 80): عرض /vfutures، لقطة الدخول، تفاصيل الإغلاق (MFE/MAE والمدة)."""
from _util import chk, src
import datetime
import handlers.trading as TR
import core.auto_trader_b68 as AT
from handlers.analysis import _fmt_price

p = {"tp1": 1.0711, "tp2": 1.1014, "stop_loss": 0.908953, "tp1_done": False}
t = TR._auto_targets_b80(p, _fmt_price)
chk("R1a الهدفان وخطة 60/40", "TP1 $1.0711 (60%) ← TP2 $1.1014 (40%)" in t and "بعد TP1 يُنقل الوقف إلى $1.0711" in t, t)
t2 = TR._auto_targets_b80(dict(p, tp1_done=True, stop_loss=1.0711), _fmt_price)
chk("R1b بعد TP1", "✅ TP1 $1.0711 تحقق (أُغلق 60%) — الوقف الآن $1.0711 للباقي 40%" in t2, t2)
s = src("handlers/trading.py")
chk("R1c R/R المرجّح لمراكز القياس", "R/R المرجّح عند الدخول 1:" in s)
chk("R2 منزلة واحدة لخسارة الوقف", "−{_loss_sl:.1f}% من الهامش" in s)
D = TR._dur_ar_b80
chk("R3 صيغة المدة", D(5) == "5 ساعات" and D(1) == "ساعة" and D(2) == "ساعتين" and D(13) == "13 ساعة" and D(0.5) == "30 دقيقة" and D(72) == "3.0 يوم", [D(5), D(1), D(2), D(13), D(0.5), D(72)])

rec = {"verdict": "ENTER", "size_pct": 20, "atr_pct": 5.6, "setup_score": 82, "n_conf": 2, "rsi": 44.2,
       "vol_ratio": 1.3, "fear_greed": 13, "sl_pct": 8.47, "tp1_pct": 7.86, "tp2_pct": 10.91,
       "regime_desc": "🔴 اتجاه هابط", "decision_label": "دخول — 2/4 تأكيدات", "mkt": "futures"}
snap = AT.entry_snapshot_b80(rec, 0.7)
chk("D1 لقطة الدخول كاملة", all(k in snap for k in ("e_verdict", "e_atr_pct", "e_setup_score", "e_n_conf", "e_rsi",
                                                    "e_fear_greed", "e_sl_pct", "e_tp1_pct", "e_tp2_pct", "e_regime_desc"))
    and snap["e_ev_mult"] == 0.7 and snap["e_decision"].startswith("دخول"), snap)
chk("D2 لا مفاتيح فارغة", "e_cvd_available" not in snap)
op = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)).isoformat()
cd = AT.close_details_b80({"entry": 1.0, "side": "long", "mfe_px": 1.05, "mae_px": 0.97, "opened_at": op})
chk("D3 MFE/MAE للشراء", cd.get("mfe_pct") == 5.0 and cd.get("mae_pct") == -3.0, cd)
chk("D4 مدة الصفقة", 4.9 < cd.get("held_h", 0) < 5.1, cd)
cs = AT.close_details_b80({"entry": 1.0, "side": "short", "mfe_px": 0.95, "mae_px": 1.02, "opened_at": op})
chk("D5 MFE/MAE للـ Short", cs.get("mfe_pct") == 5.0 and cs.get("mae_pct") == -2.0, cs)
chk("D6 الإغلاق بلا مركز لا يتعطل", isinstance(AT.close_details_b80(None), dict))
a = src("core/auto_trader_b68.py")
chk("D7 لقطة الدخول في الشراء وShort", a.count("**entry_snapshot_b80(rec, ev_mult)") == 2)
chk("D8 سجل الإغلاق يحمل التفاصيل", "**close_details_b80(pos)" in a and "_ledger_close(uid, t, kind, final=final, pos=pos)" in a)
chk("D9 تتبع MFE/MAE في الخروج", 'pos["mfe_px"]' in a and 'pos["mae_px"]' in a)
chk("D10 قرار /signal المحفوظ أوفى", '"setup_score": (score or {}).get("score")' in src("handlers/analysis.py"))

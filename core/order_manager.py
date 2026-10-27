"""
📋 رائد — Order Manager
يتتبع الصفقات الحقيقية: PnL + Status + Stop Loss + Take Profit

الدفعة 43:
  • كل OrderManager يخص مستخدماً واحداً (user_id) ولا يحمّل/يراقب صفقات غيره.
  • الإغلاق لا يُعلَّم CLOSED إلا بعد تأكيد البيع على المنصة (أو تنفيذ حماية المنصة).
  • سعر/كمية الدخول والخروج من التنفيذ الفعلي (get_order) لا من سعر الإشارة.
  • SL/TP تُوضع كأوامر على المنصة (OCO/Plan) متى دعمتها، والمراقبة الداخلية طبقة ثانية.
  • إشعار المستخدم عند كل إغلاق + ربط Risk Engine بالفتح/الإغلاق.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, fields, asdict
from typing import Callable, Dict, List, Optional
from core.exchange import BaseExchange, OrderResult

logger = logging.getLogger(__name__)

CLOSE_REASON_AR = {
    "manual":          "إغلاق يدوي",
    "stop_loss":       "وقف الخسارة",
    "stop_loss_auto":  "وقف الخسارة (حماية تلقائية)",
    "take_profit":     "هدف الربح",
    "trailing_stop":   "Trailing Stop",
    "exchange_tp":     "هدف الربح (أمر على المنصة)",
    "exchange_sl":     "وقف الخسارة (أمر على المنصة)",
    "closed_externally": "أُغلقت من المنصة مباشرة",
    "killswitch":      "Kill Switch",
}

_INSUFFICIENT_HINTS = (
    "insufficient", "not enough", "balance", "51008", "51020", "-2010",
    "170131", "170193", "43012", "43009", "30004", "exceed",
)


@dataclass
class LiveTrade:
    trade_id:     str
    symbol:       str
    side:         str       # "Buy" | "Sell"
    entry_price:  float
    qty:          float
    size_usd:     float
    stop_loss:    float     # سعر وقف الخسارة
    take_profit:  float     # سعر الهدف
    order_id:          str   = ""
    trailing_pct:      float = 0.0      # > 0 = Trailing Stop مُفعَّل
    status:            str   = "OPEN"   # OPEN | CLOSED | CANCELLED | PENDING
    exit_price:        float = 0.0
    pnl_usd:           float = 0.0
    pnl_pct:           float = 0.0
    opened_at:         float = field(default_factory=time.time)
    closed_at:         float = 0.0
    close_reason:      str   = ""
    user_id:           int   = 0
    # ── Limit Order ──────────────────────────────────────
    limit_price:       float = 0.0   # 0 = market order
    order_type:        str   = "MARKET"  # MARKET | LIMIT
    # ── Trailing Stop (M#92) ─────────────────────────────
    trailing_stop_pct: float = 0.0   # 0 = غير مفعَّل
    trailing_stop_price: float = 0.0  # السعر الحالي للـ trailing
    trailing_activated: bool  = False
    highest_price:     float = 0.0   # أعلى سعر وصله منذ الدخول
    # ── حماية تلقائية (M#92) ──────────────────────────────
    auto_protect:      bool  = False  # للذهبي وأعلى
    remind_sent:       bool  = False  # تم إرسال تذكير؟
    remind_at:         float = 0.0    # وقت التذكير
    profit_target_50pct: float = 0.0  # 50% من الهدف
    # ── الدفعة 43 ────────────────────────────────────────
    sl_pct:            float = 0.0    # نسب SL/TP الأصلية (للـ Limit المعلق)
    tp_pct:            float = 0.0
    prot:              dict  = field(default_factory=dict)  # أوامر SL/TP على المنصة
    prot_sl:           float = 0.0    # قيمة SL الموضوعة على المنصة حالياً
    prot_note:         str   = ""     # حالة الحماية للعرض
    last_prot_sync:    float = 0.0
    fill_unconfirmed:  bool  = False  # لم نتمكن من تأكيد التنفيذ من المنصة
    ex_pending:        bool  = False  # أمر Limit قائم على المنصة نفسها
    close_failures:    int   = 0
    om_scope:          str   = ""     # لمن تتبع هذه الصفقة (user:<id> | global)


_LT_FIELDS = {f.name for f in fields(LiveTrade)}


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class OrderManager:

    def __init__(self, exchange: BaseExchange, user_id: int = 0,
                 notify_fn: Optional[Callable] = None,
                 on_opened: Optional[Callable] = None,
                 on_closed: Optional[Callable] = None):
        self.exchange   = exchange
        self.user_id    = int(user_id or 0)
        self.scope      = f"user:{self.user_id}" if self.user_id else "global"
        self._trades:   Dict[str, LiveTrade] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self.notify_fn  = notify_fn          # async fn(user_id, message)
        self.on_opened  = on_opened          # fn(trade)
        self.on_closed  = on_closed          # fn(trade)
        self.last_error = ""
        self.recent_closed_pnl: Dict[str, float] = {}   # يوم → ربح محقق (لإعادة بناء Risk Engine)
        self._cycle_lock = None              # يُنشأ داخل الحلقة
        self._close_locks: Dict[str, asyncio.Lock] = {}
        # إصلاح #1048 + C1: تحميل صفقات هذا المستخدم فقط من Redis
        self._load_all_open_trades()

    # ═══════════════════════════════════════════════════════════
    # Redis
    # ═══════════════════════════════════════════════════════════
    def _get_redis(self):
        try:
            if hasattr(self, "_rc") and self._rc:
                return self._rc
            import os, redis as _r
            url = os.environ.get("REDIS_URL", "")
            if not url:
                return None
            self._rc = _r.from_url(url, decode_responses=True, socket_timeout=3)
            self._rc.ping()
            return self._rc
        except Exception:
            return None

    def _trade_key(self, trade) -> str:
        return f"raed:trade:{trade.user_id}:{trade.trade_id}"

    def _save_trade_to_redis(self, trade):
        try:
            r = self._get_redis()
            if not r:
                return
            trade.om_scope = self.scope
            data = asdict(trade)
            ttl = 86400 * (365 if trade.status in ("OPEN", "PENDING") else 30)
            r.setex(self._trade_key(trade), ttl,
                    json.dumps(data, ensure_ascii=False, default=str))
        except Exception as e:
            logger.debug(f"save_trade: {e}")

    @staticmethod
    def _trade_from_dict(data: dict) -> Optional[LiveTrade]:
        try:
            clean = {}
            for k, v in data.items():
                if k not in _LT_FIELDS:
                    continue
                clean[k] = v
            for k in ("entry_price", "qty", "size_usd", "stop_loss", "take_profit"):
                clean[k] = _num(clean.get(k))
            clean["trade_id"] = str(clean["trade_id"])
            clean["symbol"] = str(clean["symbol"])
            clean["side"] = str(clean["side"])
            if not isinstance(clean.get("prot"), dict):
                clean["prot"] = {}
            for k in ("exit_price", "pnl_usd", "pnl_pct", "opened_at", "closed_at",
                      "limit_price", "trailing_pct", "trailing_stop_pct",
                      "trailing_stop_price", "highest_price", "remind_at",
                      "profit_target_50pct", "sl_pct", "tp_pct", "prot_sl",
                      "last_prot_sync"):
                if k in clean:
                    clean[k] = _num(clean[k])
            for k in ("trailing_activated", "auto_protect", "remind_sent",
                      "fill_unconfirmed", "ex_pending"):
                if k in clean:
                    v = clean[k]
                    clean[k] = (str(v).lower() in ("true", "1")) if isinstance(v, str) else bool(v)
            if "user_id" in clean:
                clean["user_id"] = int(_num(clean["user_id"]))
            if "close_failures" in clean:
                clean["close_failures"] = int(_num(clean["close_failures"]))
            return LiveTrade(**clean)
        except Exception:
            return None

    def _load_all_open_trades(self):
        """تحميل الصفقات المفتوحة لهذا المستخدم فقط (C1) + أرباح اليوم المحققة."""
        try:
            r = self._get_redis()
            if not r:
                return
            pattern = f"raed:trade:{self.user_id}:*" if self.user_id else "raed:trade:*"
            keys = r.keys(pattern)
            loaded = 0
            today = time.strftime("%Y-%m-%d", time.gmtime())
            for key in (keys or []):
                try:
                    raw = r.get(key)
                    if not raw:
                        continue
                    data = json.loads(raw)
                    t = self._trade_from_dict(data)
                    if not t:
                        continue
                    # حارس العزل: لا نقبل صفقة مستخدم آخر أبداً
                    if self.user_id and t.user_id != self.user_id:
                        continue
                    if not self.user_id and data.get("om_scope") != "global":
                        continue
                    if t.status == "CLOSED":
                        if t.closed_at and time.strftime(
                                "%Y-%m-%d", time.gmtime(t.closed_at)) == today:
                            self.recent_closed_pnl[today] = \
                                self.recent_closed_pnl.get(today, 0.0) + t.pnl_usd
                        continue
                    if t.status not in ("OPEN", "PENDING"):
                        continue
                    t.om_scope = self.scope
                    self._trades[t.trade_id] = t
                    loaded += 1
                except Exception:
                    continue
            if loaded > 0:
                logger.info(f"✅ [{self.scope}] تحميل {loaded} صفقة من Redis")
        except Exception as e:
            logger.warning(f"_load_all_open_trades: {e}")

    def _load_trades_from_redis(self, user_id: int) -> list:
        try:
            r = self._get_redis()
            if not r:
                return []
            keys = r.keys(f"raed:trade:{user_id}:*")
            trades = [json.loads(r.get(k)) for k in keys if r.get(k)]
            return sorted(trades, key=lambda x: x.get("opened_at", 0), reverse=True)
        except Exception:
            return []

    def get_lessons_summary(self, user_id: int) -> dict:
        try:
            r = self._get_redis()
            if not r:
                return {}
            keys = r.keys(f"raed:lesson:{user_id}:*")
            lessons = [json.loads(r.get(k)) for k in keys if r.get(k)]
            if not lessons:
                return {}
            wins = [l for l in lessons if float(l.get("pnl_pct", 0)) > 0]
            return {"total": len(lessons), "wins": len(wins),
                    "losses": len(lessons) - len(wins),
                    "win_rate": len(wins) / len(lessons) * 100,
                    "best": max(lessons, key=lambda x: float(x.get("pnl_pct", 0)), default={}),
                    "worst": min(lessons, key=lambda x: float(x.get("pnl_pct", 0)), default={})}
        except Exception:
            return {}

    def record_lesson(self, trade, lesson_type="auto"):
        try:
            r = self._get_redis()
            if not r:
                return
            lesson = {"trade_id": trade.trade_id, "symbol": trade.symbol, "side": trade.side,
                      "entry_price": trade.entry_price, "exit_price": trade.exit_price,
                      "pnl_pct": trade.pnl_pct, "close_reason": trade.close_reason,
                      "lesson_type": lesson_type, "ts": time.time(), "user_id": trade.user_id}
            r.setex(f"raed:lesson:{trade.user_id}:{trade.trade_id}", 86400 * 365,
                    json.dumps(lesson, ensure_ascii=False))
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # أدوات مساعدة
    # ═══════════════════════════════════════════════════════════
    async def _notify(self, user_id: int, message: str):
        fn = self.notify_fn
        if not fn or not user_id:
            return
        try:
            await fn(user_id, message)
        except Exception as e:
            logger.warning(f"OrderManager notify {user_id}: {e}")

    def _lock_for(self, trade_id: str) -> asyncio.Lock:
        lk = self._close_locks.get(trade_id)
        if lk is None:
            lk = self._close_locks[trade_id] = asyncio.Lock()
        return lk

    def _cycle(self) -> asyncio.Lock:
        if self._cycle_lock is None:
            self._cycle_lock = asyncio.Lock()
        return self._cycle_lock

    async def _ticker(self, symbol: str) -> float:
        try:
            return float(await self.exchange.get_price(symbol) or 0)
        except Exception:
            return 0.0

    async def _confirm_fill(self, result: OrderResult, symbol: str,
                            attempts: int = 4) -> dict:
        """
        يؤكد التنفيذ الفعلي من المنصة.
        يُعيد {filled, avg, status, confirmed}. confirmed=False = لم نتمكن من القراءة.
        """
        filled = float(result.filled_qty or 0)
        avg    = float(result.avg_price or 0)
        status = str(result.status or "")
        if filled > 0 and avg > 0:
            return {"filled": filled, "avg": avg, "status": status, "confirmed": True}
        if not result.order_id:
            return {"filled": filled, "avg": avg, "status": status, "confirmed": False}
        for i in range(attempts):
            try:
                o = await self.exchange.get_order(symbol, result.order_id)
            except Exception as e:
                logger.debug(f"confirm_fill get_order: {e}")
                o = None
            if o is not None and o.success:
                st = str(o.status or "").upper()
                if o.filled_qty > 0 and (o.avg_price > 0 or st in
                                         ("FILLED", "FILLED_FULLY", "FULL_FILL", "FULL-FILL")):
                    px = o.avg_price or await self._ticker(symbol)
                    return {"filled": float(o.filled_qty), "avg": float(px),
                            "status": st, "confirmed": True}
                if st in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED",
                          "CANCELED_BY_USER", "CANCEL", "DEACTIVATED"):
                    return {"filled": float(o.filled_qty or 0), "avg": float(o.avg_price or 0),
                            "status": st, "confirmed": True}
                if i == attempts - 1:
                    return {"filled": float(o.filled_qty or 0), "avg": float(o.avg_price or 0),
                            "status": st, "confirmed": True}
            await asyncio.sleep(0.4 * (i + 1))
        return {"filled": filled, "avg": avg, "status": status, "confirmed": False}

    def _levels(self, is_long: bool, ref: float, sl_pct: float, tp_pct: float):
        if is_long:
            return ref * (1 - sl_pct / 100), ref * (1 + tp_pct / 100)
        return ref * (1 + sl_pct / 100), ref * (1 - tp_pct / 100)

    # ═══════════════════════════════════════════════════════════
    # حماية SL/TP على المنصة
    # ═══════════════════════════════════════════════════════════
    async def _place_protection(self, trade: LiveTrade) -> None:
        """يضع أوامر SL/TP على المنصة (إن دُعمت). الفشل لا يُلغي الصفقة."""
        ex = self.exchange
        trade.prot, trade.prot_sl = {}, 0.0
        if trade.side.lower() not in ("buy", "long"):
            trade.prot_note = "🛡️ الحماية داخل البوت فقط"
            return
        if not getattr(ex, "SUPPORTS_PROTECTION", False):
            trade.prot_note = "🛡️ الحماية داخل البوت فقط (المنصة لا تدعم أوامر SL/TP عبر API)"
            return
        try:
            res = await ex.place_protection(
                trade.symbol, trade.qty, trade.stop_loss, trade.take_profit)
        except Exception as e:
            res = None
            err = str(e)
        else:
            err = res.error if res else ""
        if res and res.success:
            trade.prot = {"mode": res.mode, "sl_id": res.sl_id,
                          "tp_id": res.tp_id, "group_id": res.group_id}
            trade.prot_sl = trade.stop_loss
            trade.last_prot_sync = time.time()
            trade.prot_note = "🛡️ SL/TP موضوعان على المنصة نفسها"
        else:
            logger.warning(f"protection فشلت {trade.symbol} [{self.scope}]: {err}")
            trade.prot_note = "⚠️ تعذّر وضع SL/TP على المنصة — الحماية داخل البوت فقط"

    async def _drop_protection(self, trade: LiveTrade) -> str:
        """
        يُلغي أوامر المنصة قبل أي بيع.
        يُعيد: "ok" (أُلغيت/لا توجد) | "tp" | "sl" (نُفِّذ أحدهما — لا تبع) | "fail".
        """
        if not trade.prot:
            return "ok"
        ex = self.exchange
        try:
            st = await ex.protection_status(trade.symbol, trade.prot)
        except Exception:
            st = {"state": "unknown"}
        if st.get("state") in ("tp", "sl"):
            return st["state"]
        try:
            ok = await ex.cancel_protection(trade.symbol, trade.prot)
        except Exception as e:
            logger.warning(f"cancel_protection {trade.trade_id}: {e}")
            ok = False
        if not ok:
            try:
                st = await ex.protection_status(trade.symbol, trade.prot)
            except Exception:
                st = {"state": "unknown"}
            if st.get("state") in ("tp", "sl"):
                return st["state"]
            if st.get("state") != "gone":
                return "fail"
        trade.prot, trade.prot_sl = {}, 0.0
        return "ok"

    async def _sync_protection(self, trade: LiveTrade) -> None:
        """يحدّث SL على المنصة عند تحرّك Trailing (بحد أدنى 0.3% وبفاصل 60ث)."""
        if not trade.prot or trade.prot_sl <= 0:
            return
        if abs(trade.stop_loss - trade.prot_sl) / trade.prot_sl < 0.003:
            return
        if time.time() - trade.last_prot_sync < 60:
            return
        state = await self._drop_protection(trade)
        if state in ("tp", "sl"):
            return                      # سيعالجه _reconcile في الدورة التالية
        if state == "fail":
            trade.last_prot_sync = time.time()
            return
        await self._place_protection(trade)
        trade.last_prot_sync = time.time()
        if not trade.prot:
            await self._notify(
                trade.user_id,
                f"⚠️ *{trade.symbol}*: تعذّر تحديث SL على المنصة — "
                f"الحماية الآن داخل البوت فقط.")
        self._save_trade_to_redis(trade)

    async def _reconcile(self, trade: LiveTrade) -> bool:
        """
        يقارن حالة أوامر المنصة. يُعيد True إن أُغلقت الصفقة من المنصة.
        """
        if not trade.prot or trade.status != "OPEN":
            return False
        try:
            st = await self.exchange.protection_status(trade.symbol, trade.prot)
        except Exception as e:
            logger.debug(f"reconcile {trade.trade_id}: {e}")
            return False
        state = st.get("state")
        if state in ("tp", "sl"):
            # أُغلقت من المنصة — أوقف الشقيق (لـ separate) ثم أنهِ الصفقة
            try:
                await self.exchange.cancel_protection(trade.symbol, trade.prot)
            except Exception:
                pass
            px = float(st.get("price") or 0) or await self._ticker(trade.symbol) \
                or (trade.take_profit if state == "tp" else trade.stop_loss)
            trade.prot = {}
            await self._finalize(trade, px, "exchange_tp" if state == "tp" else "exchange_sl")
            return True
        if state == "gone":
            # لا أمر قائم ولا تنفيذ معروف: تحقق من الرصيد
            trade.prot = {}
            trade.prot_note = "⚠️ أوامر SL/TP لم تعد قائمة على المنصة — الحماية داخل البوت"
            self._save_trade_to_redis(trade)
            await self._notify(
                trade.user_id,
                f"⚠️ *{trade.symbol}*: أوامر SL/TP على المنصة لم تعد قائمة "
                f"(أُلغيت أو نُفِّذت). المراقبة الآن داخل البوت.")
        return False

    # ═══════════════════════════════════════════════════════════
    # فتح صفقة
    # ═══════════════════════════════════════════════════════════
    async def open_trade(self, symbol: str, side: str,
                          size_usd: float, entry_price: float,
                          stop_loss_pct: float, take_profit_pct: float,
                          order_type: str = "MARKET",
                          user_id: int = 0,
                          limit_price: float = 0.0,
                          auto_protect: bool = False) -> Optional[LiveTrade]:
        """
        يفتح صفقة حقيقية ويُسجّلها. عند الفشل يُعيد None ويضع السبب في last_error.
        """
        self.last_error = ""
        uid = int(user_id or self.user_id or 0)
        if self.user_id and uid != self.user_id:
            self.last_error = "مستخدم غير مطابق لمدير الأوامر"
            logger.error(f"open_trade: user mismatch {uid} != {self.user_id}")
            return None
        is_long = side.lower() in ("buy", "long")
        if not is_long:
            self.last_error = ("البيع على المكشوف غير مدعوم في Spot — "
                               "لإغلاق مركز مفتوح استخدم الإغلاق")
            return None
        qty = size_usd / entry_price if entry_price > 0 else 0
        if qty <= 0:
            self.last_error = "كمية غير صالحة"
            logger.error(f"open_trade: qty={qty} غير صالح")
            return None

        _is_limit = order_type.upper() == "LIMIT"
        if _is_limit and limit_price > 0:
            exec_price = limit_price
        else:
            if _is_limit:
                logger.warning(f"LIMIT order بدون limit_price لـ {symbol} → تحويل لـ MARKET")
            order_type, exec_price = "MARKET", 0
            _is_limit = False

        result = await self.exchange.place_order(
            symbol=symbol, side="Buy",
            qty=qty, order_type=order_type,
            price=exec_price,
        )
        if not result.success:
            self.last_error = result.error or "رفضت المنصة الأمر"
            logger.error(f"open_trade فشل ({symbol}): {result.error}")
            return None

        fill = await self._confirm_fill(result, symbol)
        unconfirmed = not fill["confirmed"]
        filled, avg = fill["filled"], fill["avg"]
        trade_id = f"{symbol.upper()}_{int(time.time() * 1000)}"

        if _is_limit and (filled <= 0 or fill["status"] not in
                          ("FILLED", "FILLED_FULLY", "FULL_FILL", "FULL-FILL")):
            if filled > 0:
                # تنفيذ جزئي: ألغِ الباقي واعتمد المنفَّذ
                try:
                    await self.exchange.cancel_order(symbol, result.order_id)
                except Exception:
                    pass
            else:
                tr = LiveTrade(
                    trade_id=trade_id, symbol=symbol.upper(), side="Buy",
                    entry_price=limit_price, qty=0, size_usd=size_usd,
                    stop_loss=0, take_profit=0, order_id=result.order_id,
                    status="PENDING", limit_price=limit_price, order_type="LIMIT",
                    user_id=uid, auto_protect=auto_protect, ex_pending=True,
                    sl_pct=stop_loss_pct, tp_pct=take_profit_pct,
                    remind_at=time.time() + 1800, om_scope=self.scope)
                tr.stop_loss, tr.take_profit = self._levels(True, limit_price, stop_loss_pct, take_profit_pct)
                tr.profit_target_50pct = limit_price + (tr.take_profit - limit_price) * 0.5
                self._trades[trade_id] = tr
                self._save_trade_to_redis(tr)
                logger.info(f"📋 Limit قائم على المنصة: {trade_id}")
                return tr

        if filled <= 0:
            if unconfirmed and order_type == "MARKET":
                # أمر Market أُرسل ولم نستطع قراءة تنفيذه: نفترض التنفيذ بالكمية المرسلة
                # (إخفاء صفقة مفتوحة فعلياً أخطر من وسمها «غير مؤكدة»)
                filled = float(result.qty or qty)
                avg = await self._ticker(symbol) or entry_price
            else:
                self.last_error = f"لم يُنفَّذ الأمر ({fill['status'] or 'غير معروف'})"
                return None
        if avg <= 0:
            avg = await self._ticker(symbol) or entry_price

        sl_price, tp_price = self._levels(True, avg, stop_loss_pct, take_profit_pct)
        trade = LiveTrade(
            trade_id=trade_id,
            symbol=symbol.upper(),
            side="Buy",
            entry_price=avg,
            qty=filled,
            size_usd=filled * avg,
            stop_loss=sl_price,
            take_profit=tp_price,
            order_id=result.order_id,
            user_id=uid,
            order_type=order_type.upper(),
            limit_price=limit_price if _is_limit else 0.0,
            auto_protect=auto_protect,
            highest_price=avg,
            profit_target_50pct=avg + (tp_price - avg) * 0.5,
            sl_pct=stop_loss_pct, tp_pct=take_profit_pct,
            fill_unconfirmed=unconfirmed,
            om_scope=self.scope,
        )
        await self._place_protection(trade)
        self._trades[trade_id] = trade
        self._save_trade_to_redis(trade)
        self._fire(self.on_opened, trade)
        logger.info(f"✅ [{self.scope}] صفقة مفتوحة: {trade_id} | {symbol} "
                    f"qty={filled} @ {avg} | {trade.prot_note}")
        return trade

    def _fire(self, cb, trade):
        if cb:
            try:
                cb(trade)
            except Exception as e:
                logger.warning(f"OrderManager callback: {e}")

    # ═══════════════════════════════════════════════════════════
    # إغلاق صفقة
    # ═══════════════════════════════════════════════════════════
    async def _finalize(self, trade: LiveTrade, exit_price: float, reason: str,
                        notify: bool = True) -> LiveTrade:
        """يُنهي الصفقة (بعد تأكيد الخروج فعلياً)."""
        if exit_price <= 0:
            exit_price = trade.entry_price
        if trade.side.lower() in ("buy", "long"):
            pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100 if trade.entry_price else 0.0
            pnl_usd = (exit_price - trade.entry_price) * trade.qty
        else:
            pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100 if trade.entry_price else 0.0
            pnl_usd = (trade.entry_price - exit_price) * trade.qty
        trade.status       = "CLOSED"
        trade.exit_price   = exit_price
        trade.pnl_usd      = pnl_usd
        trade.pnl_pct      = pnl_pct
        trade.closed_at    = time.time()
        trade.close_reason = reason
        self._save_trade_to_redis(trade)
        self.record_lesson(trade)
        self._fire(self.on_closed, trade)
        logger.info(f"🔒 [{self.scope}] صفقة مغلقة: {trade.trade_id} | "
                    f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+,.2f}) | {reason}")
        if notify:
            icon = "📈" if pnl_usd >= 0 else "📉"
            await self._notify(
                trade.user_id,
                f"🔒 *أُغلقت الصفقة — {trade.symbol}*\n\n"
                f"• السبب: {CLOSE_REASON_AR.get(reason, reason)}\n"
                f"• الدخول: ${trade.entry_price:,.6g}\n"
                f"• الخروج: ${exit_price:,.6g}\n"
                f"• {icon} الربح/الخسارة: {pnl_pct:+.2f}% (${pnl_usd:+,.2f})\n\n"
                f"/trades لمراجعة الصفقات")
        return trade

    async def close_trade(self, trade_id: str,
                           reason: str = "manual") -> Optional[LiveTrade]:
        """
        يغلق صفقة مفتوحة. لا تُعلَّم CLOSED إلا بعد تأكيد البيع (أو تنفيذ المنصة).
        يُعيد None عند الفشل (الصفقة تبقى OPEN، والسبب في last_error).
        """
        trade = self._trades.get(trade_id)
        if not trade or trade.status != "OPEN":
            return None
        async with self._lock_for(trade_id):
            if trade.status != "OPEN":
                return None
            self.last_error = ""

            # 1) أوامر المنصة: ألغِها أو اعرف أنها نُفِّذت
            st = await self._drop_protection(trade)
            if st in ("tp", "sl"):
                info = await self.exchange.protection_status(trade.symbol, trade.prot) \
                    if trade.prot else {}
                px = float((info or {}).get("price") or 0) or await self._ticker(trade.symbol)
                trade.prot = {}
                await self._finalize(trade, px, "exchange_tp" if st == "tp" else "exchange_sl")
                return trade
            if st == "fail":
                trade.close_failures += 1
                self.last_error = "تعذّر إلغاء أوامر SL/TP على المنصة قبل البيع"
                logger.error(f"close_trade {trade_id}: {self.last_error}")
                self._save_trade_to_redis(trade)
                return None

            # 2) البيع الفعلي
            close_side = "Sell" if trade.side.lower() in ("buy", "long") else "Buy"
            qty = trade.qty
            result = await self.exchange.place_order(
                symbol=trade.symbol, side=close_side,
                qty=qty, order_type="MARKET")

            if not result.success and any(
                    h in (result.error or "").lower() for h in _INSUFFICIENT_HINTS):
                # الرصيد المتاح أقل من الكمية (رسوم الشراء تُخصم من العملة) — بِع المتاح
                try:
                    bal = await self.exchange.get_balance(trade.symbol)
                    free = float(getattr(bal, "free", 0) or 0)
                except Exception:
                    free = 0.0
                if free > 0 and free >= qty * 0.5:
                    qty = min(qty, free)
                    result = await self.exchange.place_order(
                        symbol=trade.symbol, side=close_side,
                        qty=qty, order_type="MARKET")
                else:
                    # لا يوجد ما يكفي: أُغلقت يدوياً من المنصة غالباً. لا نحسم من
                    # قراءة واحدة (قد تكون خطأ شبكة) — نشترط تكرارها في دورتين متتاليتين.
                    trade.close_failures += 1
                    self.last_error = f"الرصيد المتاح {free:g} أقل من كمية الصفقة {qty:g}"
                    if trade.close_failures < 2:
                        if not trade.prot:
                            await self._place_protection(trade)
                        self._save_trade_to_redis(trade)
                        return None
                    px = await self._ticker(trade.symbol) or trade.entry_price
                    logger.warning(f"close_trade {trade_id}: الرصيد {free} < {qty} → closed_externally")
                    await self._finalize(trade, px, "closed_externally")
                    return trade

            if not result.success:
                await asyncio.sleep(1.0)
                result = await self.exchange.place_order(
                    symbol=trade.symbol, side=close_side,
                    qty=qty, order_type="MARKET")

            if not result.success:
                trade.close_failures += 1
                self.last_error = result.error or "رفضت المنصة أمر البيع"
                logger.error(f"close_trade {trade_id} فشل ({trade.close_failures}): {self.last_error}")
                # أعد وضع الحماية على المنصة إن أزلناها
                if not trade.prot:
                    await self._place_protection(trade)
                self._save_trade_to_redis(trade)
                if trade.close_failures in (1, 5, 20):
                    await self._notify(
                        trade.user_id,
                        f"🚨 *تعذّر إغلاق {trade.symbol}*\n"
                        f"• السبب: {self.last_error[:150]}\n"
                        f"• الصفقة ما زالت مفتوحة — سأعيد المحاولة تلقائياً. "
                        f"راجع المنصة إن استمر الأمر.")
                return None

            fill = await self._confirm_fill(result, trade.symbol)
            if fill["confirmed"] and fill["filled"] <= 0:
                trade.close_failures += 1
                self.last_error = f"لم يُنفَّذ أمر البيع ({fill['status']})"
                self._save_trade_to_redis(trade)
                return None
            exit_price = fill["avg"] or await self._ticker(trade.symbol) or trade.entry_price
            await self._finalize(trade, exit_price, reason)
            return trade

    # ═══════════════════════════════════════════════════════════
    # Limit Orders — M#90/#91
    # ═══════════════════════════════════════════════════════════
    def add_pending_limit(self, symbol: str, side: str,
                           size_usd: float, limit_price: float,
                           stop_loss_pct: float, take_profit_pct: float,
                           user_id: int, auto_protect: bool = False) -> str:
        """يُضيف أمر Limit معلق (داخل البوت) — ينتظر وصول السعر."""
        if self.user_id and int(user_id) != self.user_id:
            raise ValueError("user mismatch")
        trade_id = f"LIMIT_{symbol.upper()}_{int(time.time() * 1000)}"
        entry    = limit_price
        is_long  = side.lower() in ("buy", "long")
        sl, tp   = self._levels(is_long, entry, stop_loss_pct, take_profit_pct)
        tp_50    = entry + (tp - entry) * 0.5

        trade = LiveTrade(
            trade_id=trade_id, symbol=symbol.upper(), side=side.capitalize(),
            entry_price=limit_price, qty=0, size_usd=size_usd,
            stop_loss=sl, take_profit=tp,
            status="PENDING", limit_price=limit_price,
            order_type="LIMIT", user_id=int(user_id),
            auto_protect=auto_protect,
            profit_target_50pct=tp_50,
            remind_at=time.time() + 1800,
            highest_price=limit_price,
            sl_pct=stop_loss_pct, tp_pct=take_profit_pct,
            om_scope=self.scope,
        )
        self._trades[trade_id] = trade
        self._save_trade_to_redis(trade)
        logger.info(f"📋 Limit pending: {trade_id} | {side} {symbol} @ ${limit_price}")
        return trade_id

    def cancel_pending(self, trade_id: str) -> bool:
        t = self._trades.get(trade_id)
        if not t or t.status != "PENDING" or t.ex_pending:
            return False
        t.status, t.close_reason, t.closed_at = "CANCELLED", "cancelled_by_user", time.time()
        self._save_trade_to_redis(t)
        return True

    async def _check_limit_orders(self, notify_fn=None):
        """يفحص الأوامر المعلقة — هل وصل السعر / هل نُفِّذ الأمر القائم على المنصة؟"""
        async with self._cycle():
            await self._check_limit_orders_locked(notify_fn)

    async def _check_limit_orders_locked(self, notify_fn=None):
        pending = [t for t in self._trades.values() if t.status == "PENDING"]
        if not pending:
            return
        notify = notify_fn or self.notify_fn

        async def _n(uid, msg):
            if notify:
                try:
                    await notify(uid, msg)
                except Exception as e:
                    logger.warning(f"limit notify: {e}")

        for trade in pending:
            try:
                is_buy = trade.side.lower() in ("buy", "long")
                price = await self._ticker(trade.symbol)
                if price <= 0:
                    continue

                # ── أمر قائم على المنصة نفسها ──
                if trade.ex_pending:
                    o = await self.exchange.get_order(trade.symbol, trade.order_id)
                    if o.success:
                        st = str(o.status or "").upper()
                        if o.filled_qty > 0 and st in ("FILLED", "FILLED_FULLY", "FULL_FILL", "FULL-FILL"):
                            await self._activate(trade, o.filled_qty, o.avg_price or trade.limit_price, _n)
                            continue
                        if st in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "CANCELED_BY_USER"):
                            if o.filled_qty > 0:
                                await self._activate(trade, o.filled_qty, o.avg_price or trade.limit_price, _n)
                            else:
                                trade.status, trade.close_reason, trade.closed_at = \
                                    "CANCELLED", "cancelled_on_exchange", time.time()
                                self._save_trade_to_redis(trade)
                            continue
                    if time.time() - trade.opened_at > 86400:
                        try:
                            await self.exchange.cancel_order(trade.symbol, trade.order_id)
                        except Exception:
                            pass
                        o2 = await self.exchange.get_order(trade.symbol, trade.order_id)
                        if o2.success and o2.filled_qty > 0:
                            await self._activate(trade, o2.filled_qty, o2.avg_price or trade.limit_price, _n)
                        else:
                            trade.status, trade.close_reason, trade.closed_at = \
                                "CANCELLED", "expired_24h", time.time()
                            self._save_trade_to_redis(trade)
                            await _n(trade.user_id,
                                     f"🚫 *أمر Limit مُلغى تلقائياً*\n\n• {trade.symbol} @ "
                                     f"${trade.limit_price:,.6g}\n• السبب: انتهت المدة (24 ساعة)")
                    continue

                # ── أمر داخل البوت ──
                reached = (is_buy and price <= trade.limit_price) or \
                          (not is_buy and price >= trade.limit_price)

                if not trade.remind_sent and time.time() > trade.remind_at:
                    trade.remind_sent = True
                    age_min = int((time.time() - trade.opened_at) / 60)
                    await _n(
                        trade.user_id,
                        f"⏰ *تذكير — أمر Limit معلق*\n\n"
                        f"• {trade.symbol} | {'شراء' if is_buy else 'بيع'}\n"
                        f"• الحجم: ${trade.size_usd:,.2f}\n"
                        f"• سعر Limit: ${trade.limit_price:,.4f}\n"
                        f"• السعر الحالي: ${price:,.4f}\n"
                        f"• منذ: {age_min} دقيقة\n\n"
                        f"الأمر سيُلغى تلقائياً بعد 24 ساعة.")

                if time.time() - trade.opened_at > 86400:
                    trade.status = "CANCELLED"
                    trade.close_reason = "expired_24h"
                    trade.closed_at = time.time()
                    self._save_trade_to_redis(trade)
                    await _n(
                        trade.user_id,
                        f"🚫 *أمر Limit مُلغى تلقائياً*\n\n"
                        f"• {trade.symbol} | Limit @ ${trade.limit_price:,.4f}\n"
                        f"• السبب: انتهت المدة (24 ساعة)\n\n"
                        f"💡 لتجديده: `/execute {trade.symbol} {'buy' if is_buy else 'sell'}"
                        f" {trade.size_usd:.0f} limit {trade.limit_price}`")
                    continue

                if reached:
                    if not is_buy:
                        trade.status, trade.close_reason, trade.closed_at = \
                            "CANCELLED", "sell_limit_unsupported", time.time()
                        self._save_trade_to_redis(trade)
                        await _n(trade.user_id,
                                 f"🚫 أمر بيع Limit لـ {trade.symbol} غير مدعوم في Spot "
                                 f"(لا بيع على المكشوف) — أُلغي.")
                        continue
                    qty = trade.size_usd / trade.limit_price if trade.limit_price > 0 else 0
                    result = await self.exchange.place_order(
                        symbol=trade.symbol, side="Buy",
                        qty=qty, order_type="LIMIT",
                        price=trade.limit_price,
                    )
                    if not result.success:
                        logger.warning(f"Limit {trade.trade_id} فشل: {result.error}")
                        trade.close_failures += 1
                        if trade.close_failures >= 3:
                            trade.status, trade.close_reason, trade.closed_at = \
                                "CANCELLED", "exchange_rejected", time.time()
                            self._save_trade_to_redis(trade)
                            await _n(trade.user_id,
                                     f"🚫 *تعذّر تنفيذ أمر Limit — {trade.symbol}*\n"
                                     f"• السبب: {(result.error or '')[:150]}")
                        continue
                    fill = await self._confirm_fill(result, trade.symbol)
                    trade.order_id = result.order_id
                    if fill["filled"] > 0 and fill["status"] in ("FILLED", "FILLED_FULLY", "FULL_FILL", "FULL-FILL", ""):
                        await self._activate(trade, fill["filled"], fill["avg"] or trade.limit_price, _n)
                    else:
                        trade.ex_pending = True      # الأمر قائم على المنصة — نتابعه
                        self._save_trade_to_redis(trade)
            except Exception as e:
                logger.warning(f"check_limit {trade.trade_id}: {e}")

    async def _activate(self, trade: LiveTrade, filled: float, avg: float, notify):
        """يحوّل Limit منفَّذ إلى صفقة مفتوحة مع حماية على المنصة."""
        trade.status        = "OPEN"
        trade.ex_pending    = False
        trade.qty           = float(filled)
        trade.entry_price   = float(avg)
        trade.size_usd      = trade.qty * trade.entry_price
        trade.highest_price = trade.entry_price
        trade.opened_at     = time.time()
        if trade.sl_pct > 0 and trade.tp_pct > 0:
            trade.stop_loss, trade.take_profit = self._levels(
                True, trade.entry_price, trade.sl_pct, trade.tp_pct)
        trade.profit_target_50pct = trade.entry_price + (trade.take_profit - trade.entry_price) * 0.5
        await self._place_protection(trade)
        self._save_trade_to_redis(trade)
        self._fire(self.on_opened, trade)
        logger.info(f"✅ Limit نُفِّذ: {trade.trade_id}")
        await notify(
            trade.user_id,
            f"🔔 *تم تنفيذ أمر الشراء!*\n\n"
            f"• {trade.symbol} | شراء\n"
            f"• الكمية: {trade.qty:.6g}\n"
            f"• سعر التنفيذ: ${trade.entry_price:,.6g}\n"
            f"• وقف الخسارة: ${trade.stop_loss:,.6g}\n"
            f"• هدف الربح: ${trade.take_profit:,.6g}\n"
            f"{trade.prot_note}")

    # ═══════════════════════════════════════════════════════════
    # Trailing Stop — M#92 (للذهبي وأعلى)
    # ═══════════════════════════════════════════════════════════
    async def _check_trailing_and_protect(self, notify_fn=None):
        async with self._cycle():
            await self._check_trailing_locked(notify_fn)

    async def _check_trailing_locked(self, notify_fn=None):
        """
        نظام الحماية التلقائي (M#92) — للذهبي وأعلى:
        - عند 50% من الهدف → يُفعِّل Trailing Stop
        - عند وقف الخسارة → يُغلق تلقائياً
        """
        notify = notify_fn or self.notify_fn
        open_trades = [t for t in self._trades.values()
                       if t.status == "OPEN" and t.auto_protect]
        for trade in open_trades:
            try:
                if await self._reconcile(trade):
                    continue
                price = await self._ticker(trade.symbol)
                if price <= 0:
                    continue
                is_long = trade.side.lower() in ("buy", "long")
                if not is_long:
                    continue

                if price > trade.highest_price:
                    trade.highest_price = price

                if not trade.trailing_activated:
                    if price >= trade.profit_target_50pct > 0:
                        trade.trailing_activated = True
                        atr_trail = abs(trade.take_profit - trade.entry_price) * 0.3
                        trade.trailing_stop_price = price - atr_trail
                        trade.trailing_stop_pct   = atr_trail / price * 100
                        # SL لا يتراجع أبداً: ارفعه إلى مستوى الـ Trailing
                        if trade.trailing_stop_price > trade.stop_loss:
                            trade.stop_loss = trade.trailing_stop_price
                        logger.info(f"🎯 Trailing Stop مُفعَّل: {trade.trade_id}")
                        pnl_now = (price - trade.entry_price) / trade.entry_price * 100
                        if notify:
                            await notify(
                                trade.user_id,
                                f"🎯 *Trailing Stop مُفعَّل — {trade.symbol}*\n\n"
                                f"• وصلت 50% من هدفك ✅\n"
                                f"• السعر الحالي: ${price:,.4f} (+{pnl_now:.1f}%)\n"
                                f"• Trailing Stop: ${trade.trailing_stop_price:,.4f}\n"
                                f"• الربح محمي تلقائياً 🛡️")
                        await self._sync_protection(trade)
                        self._save_trade_to_redis(trade)

                if trade.trailing_activated:
                    new_trail = price - trade.trailing_stop_pct / 100 * price
                    if new_trail > trade.trailing_stop_price:
                        trade.trailing_stop_price = new_trail
                        if new_trail > trade.stop_loss:
                            trade.stop_loss = new_trail
                        await self._sync_protection(trade)
                    if price <= trade.trailing_stop_price:
                        await self.close_trade(trade.trade_id, "trailing_stop")
                        continue

                if price <= trade.stop_loss:
                    await self.close_trade(trade.trade_id, "stop_loss_auto")
            except Exception as e:
                logger.warning(f"trailing_protect {trade.trade_id}: {e}")

    # ═══════════════════════════════════════════════════════════
    # الحلقة
    # ═══════════════════════════════════════════════════════════
    def start_monitoring(self, notify_fn=None):
        """
        notify_fn: async fn(user_id, message) لإرسال إشعارات للمستخدمين.
        """
        if notify_fn:
            self.notify_fn = notify_fn
        if not self._monitor_task:
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info(f"✅ Order Monitor started [{self.scope}]")

    def stop_monitoring(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def _monitor_loop(self, notify_fn=None):
        """يفحص كل 30 ثانية: أوامر المنصة، SL/TP، Limit، Trailing."""
        while True:
            try:
                await self._check_sl_tp()
                await self._check_limit_orders(notify_fn)
                await self._check_trailing_and_protect(notify_fn)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            await asyncio.sleep(30)

    async def _check_sl_tp(self):
        async with self._cycle():
            await self._check_sl_tp_locked()

    async def _check_sl_tp_locked(self):
        open_trades = [t for t in self._trades.values()
                       if t.status == "OPEN" and not t.auto_protect]
        # الصفقات auto_protect تُعالَج في _check_trailing_locked (تفادي التكرار)
        for trade in open_trades:
            try:
                if await self._reconcile(trade):
                    continue
                price = await self._ticker(trade.symbol)
                if price <= 0:
                    continue
                is_long = trade.side.lower() in ("buy", "long")

                # تحريك Trailing Stop إذا مُفعَّل
                if getattr(trade, "trailing_pct", 0) > 0:
                    t_pct = trade.trailing_pct
                    if is_long:
                        new_sl = price * (1 - t_pct / 100)
                        if new_sl > trade.stop_loss:
                            trade.stop_loss = new_sl
                            await self._sync_protection(trade)
                            self._save_trade_to_redis(trade)
                    else:
                        new_sl = price * (1 + t_pct / 100)
                        if new_sl < trade.stop_loss:
                            trade.stop_loss = new_sl

                if is_long and price <= trade.stop_loss:
                    logger.warning(f"🛑 Stop Loss: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "stop_loss")
                elif not is_long and price >= trade.stop_loss:
                    await self.close_trade(trade.trade_id, "stop_loss")
                elif is_long and price >= trade.take_profit:
                    logger.info(f"🎯 Take Profit: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "take_profit")
                elif not is_long and price <= trade.take_profit:
                    await self.close_trade(trade.trade_id, "take_profit")
            except Exception as e:
                logger.warning(f"Monitor {trade.trade_id}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════
    def set_trailing_stop(self, trade_id: str, trail_pct: float) -> bool:
        """تفعيل Trailing Stop على صفقة مفتوحة."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != "OPEN":
            return False
        trade.trailing_pct = trail_pct
        is_long = trade.side.lower() in ("buy", "long")
        if is_long:
            new_sl = trade.entry_price * (1 - trail_pct / 100)
            if new_sl > trade.stop_loss:
                trade.stop_loss = new_sl
        else:
            new_sl = trade.entry_price * (1 + trail_pct / 100)
            if new_sl < trade.stop_loss:
                trade.stop_loss = new_sl
        self._save_trade_to_redis(trade)
        logger.info(f"Trailing Stop {trail_pct}% مُفعَّل لـ {trade_id}")
        return True

    def find_open(self, symbol: str, user_id: int = None) -> Optional[LiveTrade]:
        for t in self._trades.values():
            if t.status == "OPEN" and t.symbol.upper() == symbol.upper() \
                    and (not user_id or t.user_id == user_id):
                return t
        return None

    def get_open_trades(self, user_id: int = None) -> List[LiveTrade]:
        trades = [t for t in self._trades.values() if t.status == "OPEN"]
        if user_id:
            trades = [t for t in trades if t.user_id == user_id]
        return trades

    def get_all_trades(self, user_id: int = None) -> List[LiveTrade]:
        trades = list(self._trades.values())
        if user_id:
            trades = [t for t in trades if t.user_id == user_id]
        return sorted(trades, key=lambda t: t.opened_at, reverse=True)

    def total_pnl(self, user_id: int = None) -> float:
        return sum(t.pnl_usd for t in self.get_all_trades(user_id)
                   if t.status == "CLOSED")

    def format_trade_ar(self, trade: LiveTrade) -> str:
        icon   = "🟢" if trade.side.lower() in ("buy", "long") else "🔴"
        status = {"OPEN": "🔵 مفتوحة", "CLOSED": "✅ مغلقة",
                   "CANCELLED": "🚫 ملغاة", "PENDING": "⏳ معلّقة"}.get(trade.status, trade.status)
        lines  = [
            f"{icon} *{trade.symbol}* — {trade.side}",
            f"• الحجم: ${trade.size_usd:,.0f} | الكمية: {trade.qty:.6f}",
            f"• سعر الدخول: ${trade.entry_price:,.4f}",
            f"• وقف الخسارة: ${trade.stop_loss:,.4f}",
            f"• هدف الربح: ${trade.take_profit:,.4f}",
            f"• الحالة: {status}",
        ]
        if trade.status == "OPEN" and trade.prot_note:
            lines.append(f"• {trade.prot_note}")
        if trade.fill_unconfirmed:
            lines.append("• ⚠️ لم يُؤكَّد سعر التنفيذ من المنصة (تقديري)")
        if trade.status == "CLOSED":
            pnl_icon = "📈" if trade.pnl_usd >= 0 else "📉"
            lines += [
                f"• سعر الخروج: ${trade.exit_price:,.4f}",
                f"• {pnl_icon} PnL: {trade.pnl_pct:+.2f}% (${trade.pnl_usd:+,.2f})",
                f"• السبب: {CLOSE_REASON_AR.get(trade.close_reason, trade.close_reason)}",
            ]
        return "\n".join(lines)

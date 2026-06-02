"""
📋 رائد — Order Manager
يتتبع الصفقات الحقيقية: PnL + Status + Stop Loss + Take Profit
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from core.exchange import BaseExchange, OrderResult

logger = logging.getLogger(__name__)


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


class OrderManager:

    def __init__(self, exchange: BaseExchange):
        self.exchange   = exchange
        self._trades:   Dict[str, LiveTrade] = {}
        self._monitor_task: Optional[asyncio.Task] = None

    # ═══════════════════════════════════════════════════════════
    # فتح صفقة
    # ═══════════════════════════════════════════════════════════
    async def open_trade(self, symbol: str, side: str,
                          size_usd: float, entry_price: float,
                          stop_loss_pct: float, take_profit_pct: float,
                          order_type: str = "MARKET",
                          user_id: int = 0,
                          limit_price: float = 0.0) -> Optional[LiveTrade]:
        """
        يفتح صفقة حقيقية ويُسجّلها.
        """
        # حساب الكمية
        qty = size_usd / entry_price if entry_price > 0 else 0
        if qty <= 0:
            logger.error(f"open_trade: qty={qty} غير صالح")
            return None

        # تنفيذ الأمر
        exec_price = limit_price if (order_type.upper()=="LIMIT" and limit_price>0)                      else (entry_price if order_type.lower()=="limit" else 0)
        result = await self.exchange.place_order(
            symbol=symbol, side=side,
            qty=qty, order_type=order_type,
            price=exec_price,
        )

        if not result.success:
            logger.error(f"open_trade فشل ({symbol}): {result.error}")
            return None

        # حساب مستويات SL/TP
        is_long = side.lower() in ("buy", "long")
        if is_long:
            sl_price = entry_price * (1 - stop_loss_pct / 100)
            tp_price = entry_price * (1 + take_profit_pct / 100)
        else:
            sl_price = entry_price * (1 + stop_loss_pct / 100)
            tp_price = entry_price * (1 - take_profit_pct / 100)

        trade_id = f"{symbol}_{int(time.time())}"
        trade = LiveTrade(
            trade_id=trade_id,
            symbol=symbol.upper(),
            side=side.capitalize(),
            entry_price=result.avg_price or entry_price,
            qty=result.filled_qty or qty,
            size_usd=size_usd,
            stop_loss=sl_price,
            take_profit=tp_price,
            order_id=result.order_id,
            user_id=user_id,
        )
        self._trades[trade_id] = trade
        self._save_trade_to_redis(trade) if hasattr(self,"_save_trade_to_redis") else None
        logger.info(f"✅ صفقة مفتوحة: {trade_id} | {side} {symbol} ${size_usd:,.0f}")
        return trade

    # ═══════════════════════════════════════════════════════════
    # إغلاق صفقة
    # ═══════════════════════════════════════════════════════════
    async def close_trade(self, trade_id: str,
                           reason: str = "manual") -> Optional[LiveTrade]:
        trade = self._trades.get(trade_id)
        if not trade or trade.status != "OPEN":
            return None

        close_side = "Sell" if trade.side == "Buy" else "Buy"
        result     = await self.exchange.place_order(
            symbol=trade.symbol, side=close_side,
            qty=trade.qty, order_type="MARKET",
        )

        if result.success:
            exit_price = result.avg_price or trade.entry_price
        else:
            # استخدام السعر الحالي
            exit_price = await self.exchange.get_price(trade.symbol)
            if exit_price <= 0:
                exit_price = trade.entry_price

        # حساب PnL
        if trade.side == "Buy":
            pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        else:
            pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

        pnl_usd = pnl_pct / 100 * trade.size_usd

        trade.status      = "CLOSED"
        trade.exit_price  = exit_price
        trade.pnl_usd     = pnl_usd
        trade.pnl_pct     = pnl_pct
        trade.closed_at   = time.time()
        trade.close_reason= reason

        if hasattr(self, "_save_trade_to_redis"):
            self._save_trade_to_redis(trade)
            self.record_lesson(trade)
        logger.info(
            f"🔒 صفقة مغلقة: {trade_id} | "
            f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+,.2f}) | {reason}")
        return trade

    # ═══════════════════════════════════════════════════════════
    # مراقبة SL/TP تلقائي
    # ═══════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════
    # Limit Orders — M#90/#91
    # ═══════════════════════════════════════════════════════════
    def add_pending_limit(self, symbol: str, side: str,
                           size_usd: float, limit_price: float,
                           stop_loss_pct: float, take_profit_pct: float,
                           user_id: int, auto_protect: bool = False) -> str:
        """يُضيف أمر Limit معلق — ينتظر وصول السعر."""
        trade_id = f"LIMIT_{symbol}_{int(time.time())}"
        entry    = limit_price
        is_long  = side.lower() in ("buy", "long")
        sl       = entry * (1 - stop_loss_pct/100)  if is_long else entry * (1 + stop_loss_pct/100)
        tp       = entry * (1 + take_profit_pct/100) if is_long else entry * (1 - take_profit_pct/100)
        tp_50    = entry + (tp - entry) * 0.5  # 50% من الهدف

        trade = LiveTrade(
            trade_id=trade_id, symbol=symbol.upper(), side=side.capitalize(),
            entry_price=limit_price, qty=0, size_usd=size_usd,
            stop_loss=sl, take_profit=tp,
            status="PENDING", limit_price=limit_price,
            order_type="LIMIT", user_id=user_id,
            auto_protect=auto_protect,
            profit_target_50pct=tp_50,
            remind_at=time.time() + 1800,  # تذكير بعد 30 دقيقة
            highest_price=limit_price,
        )
        self._trades[trade_id] = trade
        logger.info(f"📋 Limit pending: {trade_id} | {side} {symbol} @ ${limit_price}")
        return trade_id

    async def _check_limit_orders(self, notify_fn=None):
        """يفحص الأوامر المعلقة — هل وصل السعر؟"""
        pending = [t for t in self._trades.values() if t.status == "PENDING"]
        if not pending:
            return

        for trade in pending:
            try:
                price = await self.exchange.get_price(trade.symbol)
                if price <= 0:
                    continue

                is_buy  = trade.side in ("Buy", "buy", "long")
                reached = (is_buy and price <= trade.limit_price) or                           (not is_buy and price >= trade.limit_price)

                # تذكير بعد 30 دقيقة إذا لم يُنفَّذ
                if not trade.remind_sent and time.time() > trade.remind_at:
                    trade.remind_sent = True
                    if notify_fn:
                        age_min = int((time.time() - trade.opened_at) / 60)
                        await notify_fn(
                            trade.user_id,
                            f"⏰ *تذكير — أمر Limit معلق*\n\n"
                            f"• {trade.symbol} | {'شراء' if is_buy else 'بيع'}\n"
                            f"• الحجم: ${trade.size_usd:,.2f}\n"
                            f"• سعر Limit: ${trade.limit_price:,.4f}\n"
                            f"• السعر الحالي: ${price:,.4f}\n"
                            f"• منذ: {age_min} دقيقة\n\n"
                            f"الأمر سيُلغى تلقائياً بعد 24 ساعة."
                        )

                # إلغاء تلقائي بعد 24 ساعة
                if time.time() - trade.opened_at > 86400:
                    trade.status = "CANCELLED"
                    trade.close_reason = "expired_24h"
                    if notify_fn:
                        await notify_fn(
                            trade.user_id,
                            f"🚫 *أمر Limit مُلغى تلقائياً*\n\n"
                            f"• {trade.symbol} | Limit @ ${trade.limit_price:,.4f}\n"
                            f"• السبب: انتهت المدة (24 ساعة)\n\n"
                            f"💡 لتجديده: `/execute {trade.symbol} {'buy' if is_buy else 'sell'}"
                            f" {trade.size_usd:.0f} limit {trade.limit_price}`"
                        )
                    continue

                if reached:
                    # تنفيذ الأمر
                    qty = trade.size_usd / price if price > 0 else 0
                    result = await self.exchange.place_order(
                        symbol=trade.symbol, side=trade.side,
                        qty=qty, order_type="LIMIT",
                        price=trade.limit_price,
                    )
                    if result.success:
                        trade.status       = "OPEN"
                        trade.qty          = result.filled_qty or qty
                        trade.entry_price  = result.avg_price or trade.limit_price
                        trade.highest_price= trade.entry_price
                        trade.order_id     = result.order_id
                        trade.opened_at    = time.time()
                        logger.info(f"✅ Limit نُفِّذ: {trade.trade_id}")
                        if notify_fn:
                            await notify_fn(
                                trade.user_id,
                                f"🔔 *تم تنفيذ أمر الشراء!*\n\n"
                                f"• {trade.symbol} | {'شراء' if is_buy else 'بيع'}\n"
                                f"• الكمية: {trade.qty:.4f}\n"
                                f"• سعر التنفيذ: ${trade.entry_price:,.4f}\n"
                                f"• وقف الخسارة: ${trade.stop_loss:,.4f}\n"
                                f"• هدف الربح: ${trade.take_profit:,.4f}\n\n"
                                f"💡 هل تريد وضع أمر بيع؟\n"
                                f"`/execute {trade.symbol} sell {trade.size_usd:.0f} limit {trade.take_profit:.4f}`"
                            )
            except Exception as e:
                logger.warning(f"check_limit {trade.trade_id}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Trailing Stop — M#92 (للذهبي وأعلى)
    # ═══════════════════════════════════════════════════════════
    async def _check_trailing_and_protect(self, notify_fn=None):
        """
        نظام الحماية التلقائي (M#92) — للذهبي وأعلى:
        - عند 50% من الهدف → يُفعِّل Trailing Stop
        - عند وقف الخسارة → يُغلق تلقائياً
        """
        open_trades = [t for t in self._trades.values()
                       if t.status == "OPEN" and t.auto_protect]
        for trade in open_trades:
            try:
                price   = await self.exchange.get_price(trade.symbol)
                if price <= 0:
                    continue
                is_long = trade.side in ("Buy", "buy")

                # تحديث أعلى سعر
                if is_long and price > trade.highest_price:
                    trade.highest_price = price
                elif not is_long and (trade.highest_price == 0 or price < trade.highest_price):
                    trade.highest_price = price

                # ── تفعيل Trailing عند 50% من الهدف ──
                if is_long and not trade.trailing_activated:
                    if price >= trade.profit_target_50pct > 0:
                        trade.trailing_activated = True
                        atr_trail = abs(trade.take_profit - trade.entry_price) * 0.3
                        trade.trailing_stop_price = price - atr_trail
                        trade.trailing_stop_pct   = atr_trail / price * 100
                        logger.info(f"🎯 Trailing Stop مُفعَّل: {trade.trade_id}")
                        if notify_fn:
                            pnl_now = (price - trade.entry_price) / trade.entry_price * 100
                            await notify_fn(
                                trade.user_id,
                                f"🎯 *Trailing Stop مُفعَّل — {trade.symbol}*\n\n"
                                f"• وصلت 50% من هدفك ✅\n"
                                f"• السعر الحالي: ${price:,.4f} (+{pnl_now:.1f}%)\n"
                                f"• Trailing Stop: ${trade.trailing_stop_price:,.4f}\n"
                                f"• الربح محمي تلقائياً 🛡️"
                            )

                # ── تحديث Trailing مع ارتفاع السعر ──
                if trade.trailing_activated and is_long:
                    atr_trail = trade.trailing_stop_pct / 100 * price
                    new_trail = price - atr_trail
                    if new_trail > trade.trailing_stop_price:
                        trade.trailing_stop_price = new_trail

                # ── إغلاق عند Trailing Stop ──
                if trade.trailing_activated and is_long:
                    if price <= trade.trailing_stop_price:
                        await self.close_trade(trade.trade_id, "trailing_stop")
                        if notify_fn:
                            pnl = (price - trade.entry_price) / trade.entry_price * 100
                            await notify_fn(
                                trade.user_id,
                                f"✅ *Trailing Stop نُفِّذ — {trade.symbol}*\n\n"
                                f"• سعر الإغلاق: ${price:,.4f}\n"
                                f"• الربح المحقق: +{pnl:.1f}% 🎉\n"
                                f"• تم حماية ربحك تلقائياً"
                            )
                        continue

                # ── وقف الخسارة التلقائي ──
                sl_hit = (is_long and price <= trade.stop_loss) or                          (not is_long and price >= trade.stop_loss)
                if sl_hit:
                    await self.close_trade(trade.trade_id, "stop_loss_auto")
                    if notify_fn:
                        pnl = (price - trade.entry_price) / trade.entry_price * 100
                        await notify_fn(
                            trade.user_id,
                            f"🛑 *وقف الخسارة نُفِّذ تلقائياً — {trade.symbol}*\n\n"
                            f"• سعر الإغلاق: ${price:,.4f}\n"
                            f"• الخسارة: {pnl:.1f}%\n"
                            f"• تم حماية رأس مالك 🛡️"
                        )

            except Exception as e:
                logger.warning(f"trailing_protect {trade.trade_id}: {e}")


    def _get_redis(self):
        try:
            if hasattr(self,"_rc") and self._rc: return self._rc
            import os, redis as _r
            url = os.environ.get("REDIS_URL","")
            if not url: return None
            self._rc = _r.from_url(url, decode_responses=True, socket_timeout=3)
            self._rc.ping()
            return self._rc
        except Exception: return None

    def _save_trade_to_redis(self, trade):
        try:
            import json, time as _t
            r = self._get_redis()
            if not r: return
            data = {k: getattr(trade,k,None) for k in [
                "trade_id","symbol","side","entry_price","qty","size_usd",
                "stop_loss","take_profit","status","user_id","opened_at",
                "pnl_usd","pnl_pct","close_reason","order_type","limit_price","auto_protect"
            ]}
            r.setex(f"raed:trade:{trade.user_id}:{trade.trade_id}", 86400*30,
                    json.dumps(data, ensure_ascii=False, default=str))
        except Exception as e: pass

    def _load_trades_from_redis(self, user_id: int) -> list:
        try:
            import json
            r = self._get_redis()
            if not r: return []
            keys = r.keys(f"raed:trade:{user_id}:*")
            trades = [json.loads(r.get(k)) for k in keys if r.get(k)]
            return sorted(trades, key=lambda x: x.get("opened_at",0), reverse=True)
        except Exception: return []

    def get_lessons_summary(self, user_id: int) -> dict:
        try:
            import json
            r = self._get_redis()
            if not r: return {}
            keys = r.keys(f"raed:lesson:{user_id}:*")
            lessons = [json.loads(r.get(k)) for k in keys if r.get(k)]
            if not lessons: return {}
            wins = [l for l in lessons if float(l.get("pnl_pct",0)) > 0]
            return {"total":len(lessons),"wins":len(wins),"losses":len(lessons)-len(wins),
                    "win_rate":len(wins)/len(lessons)*100,
                    "best":max(lessons,key=lambda x:float(x.get("pnl_pct",0)),default={}),
                    "worst":min(lessons,key=lambda x:float(x.get("pnl_pct",0)),default={})}
        except Exception: return {}

    def record_lesson(self, trade, lesson_type="auto"):
        try:
            import json, time as _t
            r = self._get_redis()
            if not r: return
            lesson = {"trade_id":trade.trade_id,"symbol":trade.symbol,"side":trade.side,
                      "entry_price":trade.entry_price,"exit_price":trade.exit_price,
                      "pnl_pct":trade.pnl_pct,"close_reason":trade.close_reason,
                      "lesson_type":lesson_type,"ts":_t.time(),"user_id":trade.user_id}
            r.setex(f"raed:lesson:{trade.user_id}:{int(_t.time())}", 86400*365,
                    json.dumps(lesson, ensure_ascii=False))
        except Exception: pass

    def start_monitoring(self, notify_fn=None):
        """
        notify_fn: async fn(user_id, message) لإرسال إشعارات للمستخدمين.
        """
        self._notify_fn = notify_fn
        if not self._monitor_task:
            self._monitor_task = asyncio.create_task(self._monitor_loop(notify_fn))
            logger.info("✅ Order Monitor started (Limit + Trailing + Protect)")

    def stop_monitoring(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def _monitor_loop(self, notify_fn=None):
        """
        يفحص كل 30 ثانية:
        - SL/TP للصفقات المفتوحة
        - Limit Orders المعلقة
        - Trailing Stop وحماية الربح (ذهبي وأعلى)
        """
        while True:
            try:
                await self._check_sl_tp()
                await self._check_limit_orders(notify_fn)
                await self._check_trailing_and_protect(notify_fn)
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            await asyncio.sleep(30)

    async def _check_sl_tp(self):
        open_trades = [t for t in self._trades.values() if t.status == "OPEN"]
        if not open_trades:
            return

        for trade in open_trades:
            try:
                price = await self.exchange.get_price(trade.symbol)
                if price <= 0:
                    continue

                is_long = trade.side == "Buy"

                # فحص Stop Loss
                if is_long and price <= trade.stop_loss:
                    logger.warning(f"🛑 Stop Loss: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "stop_loss")

                elif not is_long and price >= trade.stop_loss:
                    logger.warning(f"🛑 Stop Loss: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "stop_loss")

                # فحص Take Profit
                elif is_long and price >= trade.take_profit:
                    logger.info(f"🎯 Take Profit: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "take_profit")

                elif not is_long and price <= trade.take_profit:
                    logger.info(f"🎯 Take Profit: {trade.trade_id} @ ${price:,.2f}")
                    await self.close_trade(trade.trade_id, "take_profit")

            except Exception as e:
                logger.warning(f"Monitor {trade.trade_id}: {e}")

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════
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
        icon   = "🟢" if trade.side == "Buy" else "🔴"
        status = {"OPEN": "🔵 مفتوحة", "CLOSED": "✅ مغلقة",
                   "CANCELLED": "🚫 ملغاة"}.get(trade.status, trade.status)
        lines  = [
            f"{icon} *{trade.symbol}* — {trade.side}",
            f"• الحجم: ${trade.size_usd:,.0f} | الكمية: {trade.qty:.6f}",
            f"• سعر الدخول: ${trade.entry_price:,.4f}",
            f"• وقف الخسارة: ${trade.stop_loss:,.4f}",
            f"• هدف الربح: ${trade.take_profit:,.4f}",
            f"• الحالة: {status}",
        ]
        if trade.status == "CLOSED":
            pnl_icon = "📈" if trade.pnl_usd >= 0 else "📉"
            lines += [
                f"• سعر الخروج: ${trade.exit_price:,.4f}",
                f"• {pnl_icon} PnL: {trade.pnl_pct:+.2f}% (${trade.pnl_usd:+,.2f})",
                f"• السبب: {trade.close_reason}",
            ]
        return "\n".join(lines)

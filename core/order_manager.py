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
    order_id:     str       = ""
    status:       str       = "OPEN"   # OPEN | CLOSED | CANCELLED
    exit_price:   float     = 0.0
    pnl_usd:      float     = 0.0
    pnl_pct:      float     = 0.0
    opened_at:    float     = field(default_factory=time.time)
    closed_at:    float     = 0.0
    close_reason: str       = ""
    user_id:      int       = 0


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
                          user_id: int = 0) -> Optional[LiveTrade]:
        """
        يفتح صفقة حقيقية ويُسجّلها.
        """
        # حساب الكمية
        qty = size_usd / entry_price if entry_price > 0 else 0
        if qty <= 0:
            logger.error(f"open_trade: qty={qty} غير صالح")
            return None

        # تنفيذ الأمر
        result = await self.exchange.place_order(
            symbol=symbol, side=side,
            qty=qty, order_type=order_type,
            price=entry_price if order_type.lower() == "limit" else 0,
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

        logger.info(
            f"🔒 صفقة مغلقة: {trade_id} | "
            f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+,.2f}) | {reason}")
        return trade

    # ═══════════════════════════════════════════════════════════
    # مراقبة SL/TP تلقائي
    # ═══════════════════════════════════════════════════════════
    def start_monitoring(self):
        if not self._monitor_task:
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("✅ Order Monitor started")

    def stop_monitoring(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def _monitor_loop(self):
        """يفحص SL/TP كل 30 ثانية."""
        while True:
            try:
                await self._check_sl_tp()
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
                f"• {pnl_icon} PnL: {trade.pnl_pct:+.2f}٪ (${trade.pnl_usd:+,.2f})",
                f"• السبب: {trade.close_reason}",
            ]
        return "\n".join(lines)

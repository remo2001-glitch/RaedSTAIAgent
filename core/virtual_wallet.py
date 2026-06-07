"""
🤖 رائد التداول الذكي — المحفظة الافتراضية
تداول وهمي للتدريب بدون مخاطر حقيقية
"""

from datetime import datetime, timezone
from loguru import logger
from core.config import E, VIRTUAL_WALLET_START, RISK


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VirtualWallet:
    """
    محفظة افتراضية لكل مستخدم
    رصيد افتراضي $10,000 — تداول وهمي — تتبع الأداء
    """

    def __init__(self, wallet_data: dict):
        self.balance   = wallet_data.get("balance",   VIRTUAL_WALLET_START)
        self.invested  = wallet_data.get("invested",  0.0)
        self.profit    = wallet_data.get("profit",    0.0)
        self.positions = wallet_data.get("positions", {})
        self.history   = wallet_data.get("history",   [])

    def to_dict(self) -> dict:
        return {
            "balance":   round(self.balance, 2),
            "invested":  round(self.invested, 2),
            "profit":    round(self.profit, 2),
            "positions": self.positions,
            "history":   self.history[-50:],  # آخر 50 صفقة
        }

    @property
    def total_value(self) -> float:
        return round(self.balance + self.invested, 2)

    @property
    def total_return_pct(self) -> float:
        if VIRTUAL_WALLET_START == 0:
            return 0.0
        return round((self.total_value - VIRTUAL_WALLET_START) / VIRTUAL_WALLET_START * 100, 2)

    # ── شراء ───────────────────────────────────────────────────────────────

    def buy(self, symbol: str, price: float, amount_usd: float) -> dict:
        """
        تنفيذ أمر شراء وهمي
        symbol: مثل BTCUSDT
        price: السعر الحالي
        amount_usd: المبلغ بالدولار
        """
        symbol = symbol.upper()

        # إصلاح K1/#840/#898: رفض الشراء إذا يوجد مركز مفتوح
        if symbol in self.positions:
            return {
                "ok": False,
                "msg": f"⚠️ لديك مركز مفتوح على {symbol} بالفعل — أغلقه أولاً"
            }

        # فحص الرصيد
        if amount_usd > self.balance:
            return {"ok": False, "msg": f"{E['error']} رصيدك غير كافٍ. المتاح: ${self.balance:,.2f}"}

        # فحص حد الصفقة (10% من المحفظة)
        max_allowed = self.total_value * (RISK["max_position_pct"] / 100)
        if amount_usd > max_allowed:
            return {
                "ok": False,
                "msg": (
                    f"{E['warn']} الحد الأقصى لصفقة واحدة هو "
                    f"{RISK['max_position_pct']}% من محفظتك "
                    f"(${max_allowed:,.2f})"
                )
            }

        quantity = amount_usd / price

        # تحديث أو إنشاء مركز
        if symbol in self.positions:
            pos = self.positions[symbol]
            total_qty   = pos["quantity"] + quantity
            total_cost  = pos["avg_price"] * pos["quantity"] + price * quantity
            avg_price   = total_cost / total_qty
            self.positions[symbol] = {
                **pos,
                "quantity":  round(total_qty, 8),
                "avg_price": round(avg_price, 6),
                "cost":      round(total_cost, 2),
                "updated_at": _now(),
            }
        else:
            self.positions[symbol] = {
                "symbol":     symbol,
                "quantity":   round(quantity, 8),
                "avg_price":  round(price, 6),
                "cost":       round(amount_usd, 2),
                "opened_at":  _now(),
                "updated_at": _now(),
                "stop_loss":  round(price * (1 - RISK["max_loss_pct"] / 100), 6),
                "take_profit": round(price * (1 + RISK["take_profit_pct"] / 100), 6),
            }

        self.balance  -= amount_usd
        self.invested += amount_usd

        trade = {
            "type":     "buy",
            "symbol":   symbol,
            "price":    price,
            "quantity": round(quantity, 8),
            "amount":   round(amount_usd, 2),
            "time":     _now(),
        }
        self.history.append(trade)

        return {
            "ok":  True,
            "msg": (
                f"{E['ok']} تم الشراء الوهمي!\n\n"
                f"{E['bank']} العملة: {symbol}\n"
                f"💲 السعر: ${price:,.4f}\n"
                f"{E['money']} الكمية: {quantity:.6f}\n"
                f"💵 المبلغ: ${amount_usd:,.2f}\n"
                f"💰 الرصيد المتبقي: ${self.balance:,.2f}\n\n"
                f"{E['warn']} وقف الخسارة: ${self.positions[symbol]['stop_loss']:,.4f}\n"
                f"🎯 هدف الربح: ${self.positions[symbol]['take_profit']:,.4f}\n\n"
                f"{E['virtual']} هذه صفقة تدريبية — لا أموال حقيقية"
            ),
            "trade": trade,
        }

    # ── بيع ────────────────────────────────────────────────────────────────

    def sell(self, symbol: str, price: float, quantity: float | None = None) -> dict:
        """
        تنفيذ أمر بيع وهمي
        quantity=None يعني بيع الكمية كاملة
        """
        symbol = symbol.upper()

        if symbol not in self.positions:
            return {"ok": False, "msg": f"{E['error']} لا يوجد مركز مفتوح على {symbol}"}

        pos = self.positions[symbol]
        qty_to_sell = quantity if quantity else pos["quantity"]
        qty_to_sell = min(qty_to_sell, pos["quantity"])

        if qty_to_sell <= 0:
            return {"ok": False, "msg": f"{E['error']} كمية غير صالحة"}

        # حساب الأرباح/الخسائر
        cost_basis  = pos["avg_price"] * qty_to_sell
        sale_value  = price * qty_to_sell
        pnl         = sale_value - cost_basis
        pnl_pct     = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        self.balance  += sale_value
        self.invested -= cost_basis
        self.profit   += pnl

        if qty_to_sell >= pos["quantity"]:
            del self.positions[symbol]
        else:
            self.positions[symbol]["quantity"] -= qty_to_sell
            self.positions[symbol]["cost"]     -= cost_basis

        emoji_pnl = E["up"] if pnl >= 0 else E["down"]
        sign      = "+" if pnl >= 0 else ""

        trade = {
            "type":     "sell",
            "symbol":   symbol,
            "price":    price,
            "quantity": round(qty_to_sell, 8),
            "amount":   round(sale_value, 2),
            "pnl":      round(pnl, 2),
            "pnl_pct":  round(pnl_pct, 2),
            "time":     _now(),
        }
        self.history.append(trade)

        return {
            "ok": True,
            "msg": (
                f"{E['ok']} تم البيع الوهمي!\n\n"
                f"{E['bank']} العملة: {symbol}\n"
                f"💲 سعر البيع: ${price:,.4f}\n"
                f"{E['money']} الكمية: {qty_to_sell:.6f}\n"
                f"💵 القيمة: ${sale_value:,.2f}\n\n"
                f"{emoji_pnl} الربح/الخسارة: {sign}${pnl:,.2f} ({sign}{pnl_pct:.2f}%)\n"
                f"💰 الرصيد الجديد: ${self.balance:,.2f}"
            ),
            "trade": trade,
        }

    # ── تقرير المحفظة ───────────────────────────────────────────────────────

    def report(self, current_prices: dict | None = None) -> str:
        """تقرير شامل للمحفظة الافتراضية"""
        current_prices = current_prices or {}

        # حساب القيمة الحالية للمراكز
        positions_value = 0.0
        positions_lines = []
        for sym, pos in self.positions.items():
            cur_price = current_prices.get(sym, pos["avg_price"])
            cur_value = cur_price * pos["quantity"]
            pos_pnl   = cur_value - pos["cost"]
            pos_pnl_p = (pos_pnl / pos["cost"] * 100) if pos["cost"] > 0 else 0
            positions_value += cur_value
            emoji = E["up"] if pos_pnl >= 0 else E["down"]
            sign  = "+" if pos_pnl >= 0 else ""
            positions_lines.append(
                f"  • {sym}: {pos['quantity']:.4f} @ ${cur_price:,.4f}\n"
                f"    {emoji} {sign}${pos_pnl:,.2f} ({sign}{pos_pnl_p:.1f}%)"
            )

        total = self.balance + positions_value
        total_pnl = total - VIRTUAL_WALLET_START
        total_pnl_pct = (total_pnl / VIRTUAL_WALLET_START * 100) if VIRTUAL_WALLET_START > 0 else 0
        emoji_total = E["up"] if total_pnl >= 0 else E["down"]
        sign_total  = "+" if total_pnl >= 0 else ""

        # إحصاء الصفقات
        sells       = [t for t in self.history if t["type"] == "sell"]
        wins        = [t for t in sells if t.get("pnl", 0) > 0]
        win_rate    = (len(wins) / len(sells) * 100) if sells else 0

        lines = [
            f"{E['virtual']} المحفظة الافتراضية\n",
            f"{'─' * 28}",
            f"💵 الرصيد النقدي:   ${self.balance:,.2f}",
            f"📊 قيمة المراكز:    ${positions_value:,.2f}",
            f"💰 إجمالي المحفظة: ${total:,.2f}",
            f"",
            f"{emoji_total} العائد الكلي: {sign_total}${total_pnl:,.2f} ({sign_total}{total_pnl_pct:.2f}%)",
            f"",
        ]

        if positions_lines:
            lines.append(f"📋 المراكز المفتوحة ({len(self.positions)}):")
            lines.extend(positions_lines)
            lines.append("")

        lines += [
            f"📈 إجمالي الصفقات: {len(sells)}",
            f"✅ نسبة الربح:     {win_rate:.1f}%",
            f"",
            f"{'─' * 28}",
            f"{E['virtual']} هذه محفظة تدريبية — لا أموال حقيقية",
            f"📊 NexusTrader | 🤖 رائد التداول الذكي",
        ]

        return "\n".join(lines)

    # ── إعادة الضبط ─────────────────────────────────────────────────────────

    def reset(self) -> str:
        self.balance   = VIRTUAL_WALLET_START
        self.invested  = 0.0
        self.profit    = 0.0
        self.positions = {}
        self.history   = []
        return (
            f"{E['ok']} تمت إعادة ضبط المحفظة الافتراضية!\n"
            f"💵 الرصيد الجديد: ${VIRTUAL_WALLET_START:,.2f}"
        )

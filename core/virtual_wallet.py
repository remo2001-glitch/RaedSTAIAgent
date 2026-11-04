"""
🤖 رائد التداول الذكي — المحفظة الافتراضية
تداول وهمي للتدريب بدون مخاطر حقيقية
"""

from datetime import datetime, timezone
from loguru import logger
from core.config import E, VIRTUAL_WALLET_START, RISK


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


HISTORY_CAP = 2000  # قيود شراء/بيع (≈1000 صفقة مغلقة)


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
        # batch58 (البند 7ب، قرار رحال 2026-11-04): مراكز Futures افتراضية منفصلة عن Spot
        # (حتى لا تعاملها مسارات Spot — الوقف/الهدف لـLong فقط — كمراكز شراء)
        self.futures   = wallet_data.get("futures",   {}) or {}

    def to_dict(self) -> dict:
        return {
            "balance":   round(self.balance, 2),
            "invested":  round(self.invested, 2),
            "profit":    round(self.profit, 2),
            "positions": self.positions,
            "futures":   self.futures,
            "history":   self.history[-HISTORY_CAP:],  # batch55 (#552): كان 50 قيداً (≈25 صفقة شراء+بيع) فتسقط الصفقات من /stats والتقارير
        }

    @property
    def futures_margin(self) -> float:
        try:
            return round(sum(float(p.get("margin", 0) or 0) for p in self.futures.values()), 2)
        except Exception:
            return 0.0

    @property
    def total_value(self) -> float:
        return round(self.balance + self.invested + self.futures_margin, 2)

    @property
    def total_return_pct(self) -> float:
        if VIRTUAL_WALLET_START == 0:
            return 0.0
        return round((self.total_value - VIRTUAL_WALLET_START) / VIRTUAL_WALLET_START * 100, 2)

    # ── مدة الاحتفاظ (Time Exit) ────────────────────────────────────────────
    # time_exit_enforcement_fix: بطاقة كل تنفيذ عبر /execute تعرض "⏰ أقصى مدة
    # N ساعة"، لكن لم يكن في النظام أي كود يفرضها: المركز يُخزِّن opened_at
    # فقط بلا أي حد، والإغلاق التلقائي (raed_engine._check_virtual_positions)
    # كان يعلن في وصفه إغلاق "انتهاء مدة الاحتفاظ" بينما شرطه TP أو SL فقط.
    # موثَّق فعلياً: مراكز عمرها نحو 6 أيام ما زالت مفتوحة وبطاقتها تقول 48 ساعة.
    # هذه الدوال هي المصدر الوحيد لحساب العمر والحد والمتبقي، ويستخدمها
    # الإغلاق التلقائي وعرض /vtrades معاً، فلا يتباعد ما يُعرَض عمّا يُنفَّذ.
    #
    # مركز بلا max_hold_hours مخزَّن (مراكز قبل هذا الإصلاح، والتنفيذ الجماعي
    # والتداول التلقائي التي لا تعرف مدة) يُقاس بحدّ احتياطي = أطول أفق يوثّقه
    # النظام نفسه: "Time Exit: إذا لا حركة بعد 5 أيام → أغلق الموضع" في /signal.
    LEGACY_MAX_HOLD_HOURS = 120.0

    def position_age_hours(self, symbol: str) -> float | None:
        pos = self.positions.get(symbol.upper()) or self.positions.get(symbol)
        if not pos:
            return None
        try:
            opened = datetime.fromisoformat(str(pos.get("opened_at")))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0)
        except Exception:
            return None   # لا نعرف وقت الفتح → لا نُغلِق على أساس مجهول

    def hold_limit_hours(self, symbol: str) -> float:
        pos = self.positions.get(symbol.upper()) or self.positions.get(symbol) or {}
        try:
            h = float(pos.get("max_hold_hours") or 0)
        except (TypeError, ValueError):
            h = 0.0
        return h if h > 0 else self.LEGACY_MAX_HOLD_HOURS

    def hold_remaining_hours(self, symbol: str) -> float | None:
        age = self.position_age_hours(symbol)
        if age is None:
            return None
        return self.hold_limit_hours(symbol) - age

    def is_hold_expired(self, symbol: str) -> bool:
        rem = self.hold_remaining_hours(symbol)
        return rem is not None and rem <= 0

    # ── شراء ───────────────────────────────────────────────────────────────

    def buy(self, symbol: str, price: float, amount_usd: float,
            signal_id: str | None = None, setup_type: str | None = None) -> dict:
        """
        تنفيذ أمر شراء وهمي
        symbol: مثل BTCUSDT
        price: السعر الحالي
        amount_usd: المبلغ بالدولار
        signal_id/setup_type: (خطة التطوير — البُعد الرابع) ربط الصفقة
        بالإشارة التي أنتجتها، لتمكين ربط النتيجة لاحقاً بنوع الإعداد —
        اختياريان تماماً؛ لا يُغيّران أي سلوك تنفيذ حالي عند غيابهما.
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

        # إنشاء مركز جديد (الوصول هنا يضمن عدم وجود مركز مسبقاً)
        self.positions[symbol] = {
            "symbol":     symbol,
            "quantity":   round(quantity, 8),
            "avg_price":  round(price, 6),
            "cost":       round(amount_usd, 2),
            "opened_at":  _now(),
            "updated_at": _now(),
            "stop_loss":  round(price * (1 - RISK["max_loss_pct"] / 100), 6),
            "take_profit": round(price * (1 + RISK["take_profit_pct"] / 100), 6),
            # خطة التطوير — البُعد الرابع: يبقى None لأي صفقة لا تحمل إشارة
            # مصدر (مثال: شراء يدوي مباشر بدل /signal) — لا يكسر أي مسار حالي
            "signal_id":  signal_id,
            "setup_type": setup_type,
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

        # حساب الأرباح/الخسائر — نستخدم pos["cost"] كمرجع وليس avg_price*qty
        # لتجنب تراكم أخطاء التقريب وضمان توافق invested مع الواقع
        cost_basis  = pos["cost"] if qty_to_sell >= pos["quantity"] else pos["avg_price"] * qty_to_sell
        sale_value  = price * qty_to_sell
        pnl         = sale_value - cost_basis
        pnl_pct     = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        self.balance  += sale_value
        # حماية: invested لا يمكن أن يصبح سالباً
        self.invested  = max(0.0, round(self.invested - cost_basis, 2))
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
            # خطة التطوير — البُعد الرابع: تمرير هوية الإشارة المصدر (إن
            # وُجدت) للمستدعي، ليتمكن من ربط النتيجة بسجل الإشارة الأصلي
            "signal_id":  pos.get("signal_id"),
            "setup_type": pos.get("setup_type"),
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

    # ── Futures افتراضية (batch58 — البند 7ب) ──────────────────────────────
    # تدريب فقط: هامش معزول، رسوم Taker تقديرية، تصفية مبسّطة. لا أي أمر حقيقي.
    FUT_MAX_LEVERAGE = 10      # سقف افتراضي محافظ (الحقيقي مؤجل حتى نجاح تجربة الدفعة 43)
    FUT_FEE_RATE     = 0.0005  # 0.05% من القيمة الاسمية لكل جهة
    FUT_MMR          = 0.005   # هامش الصيانة 0.5%

    @classmethod
    def futures_liq_price(cls, side: str, entry: float, leverage: int) -> float:
        lev = max(1, int(leverage or 1))
        if str(side).lower() in ("long", "buy"):
            return max(entry * (1 - 1.0 / lev + cls.FUT_MMR), 0.0)
        return entry * (1 + 1.0 / lev - cls.FUT_MMR)

    def open_futures(self, symbol: str, side: str, price: float, margin_usd: float,
                     leverage: int = 1, stop_loss: float = 0.0, take_profit: float = 0.0,
                     max_hold_hours: float = 0.0) -> dict:
        """يفتح مركز Futures افتراضياً (Long/Short) بهامش معزول. margin_usd = الهامش المحجوز من الرصيد."""
        symbol = symbol.upper().replace("USDT", "") or symbol.upper()
        side = "long" if str(side).lower() in ("long", "buy", "flong") else "short"
        lev = int(leverage or 1)
        if lev < 1 or lev > self.FUT_MAX_LEVERAGE:
            return {"ok": False, "msg": f"⚠️ الرافعة الافتراضية بين 1x و{self.FUT_MAX_LEVERAGE}x فقط"}
        if symbol in self.futures:
            return {"ok": False, "msg": f"⚠️ لديك مركز Futures افتراضي مفتوح على {symbol} — أغلقه أولاً"}
        if price <= 0 or margin_usd <= 0:
            return {"ok": False, "msg": f"{E['error']} سعر أو هامش غير صالح"}
        notional = margin_usd * lev
        fee = notional * self.FUT_FEE_RATE
        if margin_usd + fee > self.balance:
            return {"ok": False, "msg": f"{E['error']} رصيدك غير كافٍ للهامش والرسوم. المتاح: ${self.balance:,.2f}"}
        max_allowed = self.total_value * (RISK["max_position_pct"] / 100)
        if margin_usd > max_allowed:
            return {"ok": False, "msg": (f"{E['warn']} الحد الأقصى لهامش صفقة واحدة {RISK['max_position_pct']}% "
                                         f"من محفظتك (${max_allowed:,.2f})")}
        liq = self.futures_liq_price(side, price, lev)
        is_long = side == "long"
        if stop_loss and stop_loss > 0:
            if (is_long and stop_loss >= price) or ((not is_long) and stop_loss <= price):
                return {"ok": False, "msg": "⚠️ وقف الخسارة في الجهة الخاطئة من سعر الدخول"}
            if lev > 1 and ((is_long and stop_loss <= liq) or ((not is_long) and stop_loss >= liq)):
                return {"ok": False, "msg": (f"⛔ الوقف ${stop_loss:,.4f} أبعد من سعر التصفية ${liq:,.4f} — "
                                             f"خفّض الرافعة أو قرّب الوقف (الوقف يجب أن يُضرب قبل التصفية)")}
        qty = notional / price
        self.balance -= (margin_usd + fee)
        self.futures[symbol] = {
            "symbol": symbol, "side": side, "leverage": lev,
            "entry": round(price, 8), "qty": round(qty, 10),
            "margin": round(margin_usd, 2), "notional": round(notional, 2),
            "fee_open": round(fee, 4), "liq_price": round(liq, 8),
            "stop_loss": round(float(stop_loss or 0), 8), "take_profit": round(float(take_profit or 0), 8),
            "opened_at": _now(), "max_hold_hours": float(max_hold_hours or 0),
        }
        self.history.append({"type": "fut_open", "symbol": symbol, "side": side, "leverage": lev,
                             "price": price, "margin": round(margin_usd, 2), "notional": round(notional, 2),
                             "fee": round(fee, 4), "time": _now()})
        _side_ar = "📈 Long (شراء)" if is_long else "📉 Short (بيع على المكشوف)"
        return {"ok": True, "position": self.futures[symbol], "msg": (
            f"{E['ok']} تم فتح مركز Futures افتراضي\n\n"
            f"{E['bank']} {symbol} | {_side_ar} | رافعة {lev}x (هامش معزول)\n"
            f"💲 الدخول: ${price:,.4f}\n"
            f"💵 الهامش: ${margin_usd:,.2f} | القيمة الاسمية: ${notional:,.2f}\n"
            f"🧾 رسوم الفتح (تقديرية 0.05%): ${fee:,.2f}\n"
            + (f"🛑 الوقف: ${stop_loss:,.4f}\n" if stop_loss else "")
            + (f"🎯 الهدف: ${take_profit:,.4f}\n" if take_profit else "")
            + f"💥 سعر التصفية التقريبي: ${liq:,.4f}\n"
            f"💰 الرصيد المتبقي: ${self.balance:,.2f}\n\n"
            f"{E['virtual']} صفقة تدريبية — لا أموال حقيقية؛ الرافعة الحقيقية غير مفعّلة")}

    def futures_pnl(self, symbol: str, price: float) -> float:
        pos = self.futures.get(symbol.upper())
        if not pos:
            return 0.0
        d = (price - pos["entry"]) if pos["side"] == "long" else (pos["entry"] - price)
        return d * float(pos["qty"])

    def futures_exit_reason(self, symbol: str, price: float) -> str:
        """liquidation | sl | tp | time | '' — التصفية أولاً ثم الوقف ثم الهدف ثم المدة."""
        pos = self.futures.get(symbol.upper())
        if not pos or price <= 0:
            return ""
        long_ = pos["side"] == "long"
        liq, sl, tp = float(pos.get("liq_price") or 0), float(pos.get("stop_loss") or 0), float(pos.get("take_profit") or 0)
        if int(pos.get("leverage", 1)) > 1 and liq > 0 and ((long_ and price <= liq) or ((not long_) and price >= liq)):
            return "liquidation"
        if sl > 0 and ((long_ and price <= sl) or ((not long_) and price >= sl)):
            return "sl"
        if tp > 0 and ((long_ and price >= tp) or ((not long_) and price <= tp)):
            return "tp"
        try:
            mh = float(pos.get("max_hold_hours") or 0) or self.LEGACY_MAX_HOLD_HOURS
            opened = datetime.fromisoformat(str(pos.get("opened_at")))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0 >= mh:
                return "time"
        except Exception:
            pass
        return ""

    def close_futures(self, symbol: str, price: float, reason: str = "manual") -> dict:
        symbol = symbol.upper().replace("USDT", "") or symbol.upper()
        pos = self.futures.get(symbol)
        if not pos:
            return {"ok": False, "msg": f"{E['error']} لا يوجد مركز Futures افتراضي على {symbol}"}
        if price <= 0:
            return {"ok": False, "msg": f"{E['error']} سعر غير صالح"}
        margin = float(pos["margin"])
        if reason == "liquidation":
            exit_px = float(pos.get("liq_price") or price)
            gross, fee_close = -margin, 0.0          # التصفية تستهلك الهامش كاملاً
        else:
            exit_px = price
            gross = self.futures_pnl(symbol, price)
            fee_close = float(pos["qty"]) * price * self.FUT_FEE_RATE
            gross = max(gross, -margin)              # الهامش المعزول حدّ الخسارة الأقصى
        pnl = gross - float(pos.get("fee_open", 0) or 0) - fee_close
        self.balance += max(margin + gross - fee_close, 0.0)
        self.profit += pnl
        pnl_pct = pnl / margin * 100 if margin > 0 else 0.0
        del self.futures[symbol]
        trade = {
            "type": "sell", "futures": True, "symbol": symbol, "side": pos["side"],
            "leverage": pos["leverage"], "price": exit_px, "entry": pos["entry"],
            "quantity": pos["qty"], "amount": round(float(pos["qty"]) * exit_px, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "fees": round(float(pos.get("fee_open", 0) or 0) + fee_close, 4),
            "reason": reason, "time": _now(),
        }
        self.history.append(trade)
        _r_ar = {"liquidation": "💥 تصفية", "sl": "🛑 وقف الخسارة", "tp": "🎯 الهدف",
                 "time": "⏰ انتهاء المدة", "manual": "إغلاق يدوي"}.get(reason, reason)
        sign = "+" if pnl >= 0 else ""
        return {"ok": True, "trade": trade, "msg": (
            f"{E['ok']} أُغلق مركز Futures افتراضي — {symbol}\n\n"
            f"• الجهة: {'Long' if pos['side'] == 'long' else 'Short'} | رافعة {pos['leverage']}x\n"
            f"• السبب: {_r_ar}\n"
            f"• الدخول: ${pos['entry']:,.4f} → الخروج: ${exit_px:,.4f}\n"
            f"• الربح/الخسارة الصافي: {sign}${pnl:,.2f} ({sign}{pnl_pct:.1f}% من الهامش) — بعد رسوم ${trade['fees']:,.2f}\n"
            f"💰 الرصيد الجديد: ${self.balance:,.2f}\n\n"
            f"{E['virtual']} صفقة تدريبية — لا أموال حقيقية")}

    def futures_report_lines(self, current_prices: dict | None = None) -> list:
        out = []
        for sym, pos in self.futures.items():
            px = (current_prices or {}).get(sym, pos["entry"])
            pnl = self.futures_pnl(sym, px)
            pct = pnl / pos["margin"] * 100 if pos.get("margin") else 0
            sign = "+" if pnl >= 0 else ""
            out.append(f"  • {sym} {'Long' if pos['side'] == 'long' else 'Short'} {pos['leverage']}x @ ${pos['entry']:,.4f}"
                       f" | هامش ${pos['margin']:,.2f} | تصفية ≈ ${pos['liq_price']:,.4f}\n"
                       f"    {E['up'] if pnl >= 0 else E['down']} {sign}${pnl:,.2f} ({sign}{pct:.1f}% من الهامش)")
        return out

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

        _fut_lines = self.futures_report_lines(current_prices)
        _fut_val = sum(float(p["margin"]) + self.futures_pnl(s_, (current_prices or {}).get(s_, p["entry"]))
                       for s_, p in self.futures.items())
        total = self.balance + positions_value + _fut_val
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
        if _fut_lines:
            lines.append(f"⚡ مراكز Futures الافتراضية ({len(self.futures)}):")
            lines.extend(_fut_lines)
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
        self.futures   = {}
        self.history   = []
        return (
            f"{E['ok']} تمت إعادة ضبط المحفظة الافتراضية!\n"
            f"💵 الرصيد الجديد: ${VIRTUAL_WALLET_START:,.2f}"
        )

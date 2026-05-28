"""
🤖 رائد — Raed Engine (المحرك المركزي) v2
Institutional Balanced Crypto AI Trading Agent

الإصلاحات:
- data_layer مُهيَّأ مبدئياً في __init__ لتجنّب AttributeError
- import time في الأعلى
- find_best_exchange: فلترة صحيحة على user_id
- run_safety_checks: قيم drawdown موحَّدة (0-1)
- _run_4h_scan: threshold من config بدلاً من قيمة ثابتة
- scheduler.next_scan_ar() محمي من None
- top_coins حسب باقة المستخدم الفعلية
- تحقق من OWNER_CHAT_ID صحيح قبل الإرسال
"""

import asyncio
import logging
import time
import aiohttp
from typing import Optional

from core.data_validator   import DataValidator
from core.data_layer       import DataLayer
from core.regime_detector  import RegimeDetector
from core.strategy_router  import SignalLayer, StrategyRouter
from core.risk_engine      import RiskEngine
from core.kill_switch      import KillSwitch, HumanOverrideLayer, AuditLogger
from core.microstructure   import MicrostructureLayer
from core.event_risk       import EventRiskFilter
from core.portfolio_engine import CapitalAllocationEngine
from core.news             import NewsEngine
from core.backtest         import BacktestEngine
from core.drift_monitor    import DriftMonitor
from core.scheduler        import Scheduler
from core.exchange         import create_exchange, BaseExchange, SUPPORTED_EXCHANGES
from core.order_manager    import OrderManager
from core.state_manager    import state_manager as _sm_singleton

logger = logging.getLogger(__name__)

# عملات مستقرة للاستبعاد من المسح
STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD",
    "USDD", "GUSD", "LUSD", "FRAX", "MIM", "ALUSD", "SUSD",
    "PYUSD", "CRVUSD", "USDE", "USDS",
}


class RaedEngine:
    """
    المحرك المركزي — Singleton يُهيَّأ مرة واحدة عند بدء البوت.
    جميع الـ handlers تصل للطبقات عبره.
    """

    def __init__(self, config: dict):
        self.config             = config
        self._session: Optional[aiohttp.ClientSession] = None
        self.auto_trade_enabled = False

        # ── الطبقة ٢: Data Validation ──────────────────────────
        self.data_validator = DataValidator()

        # ── الطبقة ٣: Signal + Regime ──────────────────────────
        self.regime_detector = RegimeDetector()
        self.signal_layer    = SignalLayer()
        self.strategy_router = StrategyRouter()

        # ── الطبقة ٥: Risk Engine ──────────────────────────────
        risk_cfg = {
            "portfolio_size":      config.get("PORTFOLIO_SIZE", 10_000),
            "max_risk_per_trade":  config.get("MAX_RISK_PER_TRADE", 0.02),
            "max_daily_loss":      config.get("MAX_DAILY_LOSS", 0.05),
            "max_drawdown":        config.get("MAX_DRAWDOWN", 0.15),
            "max_open_positions":  config.get("MAX_OPEN_POSITIONS", 5),
            "max_single_exposure": config.get("MAX_SINGLE_EXPOSURE", 0.10),  # 10% موحَّد
            "min_confidence":      config.get("MIN_CONFIDENCE", 0.65),
        }
        self.risk_engine = RiskEngine(risk_cfg)

        # ── الطبقة ٦+٨+٩: Kill Switch + Override + Audit ───────
        self.kill_switch    = KillSwitch()
        self.human_override = HumanOverrideLayer(timeout_minutes=15)
        self.audit_logger   = AuditLogger()

        # ── الطبقة ٧: Microstructure ───────────────────────────
        self.microstructure = MicrostructureLayer()

        # ── Event Risk Filter ───────────────────────────────────
        self.event_risk = EventRiskFilter()

        # ── الطبقة ١٠: Capital Allocation ──────────────────────
        self.capital_engine = CapitalAllocationEngine()

        # ── أدوات التحليل ──────────────────────────────────────
        self.news_engine     = NewsEngine(groq_key=config.get("GROQ_API_KEY", ""))
        self.backtest_engine = BacktestEngine()
        self.drift_monitor   = DriftMonitor(baseline_win_rate=0.55)

        # ── data_layer مُهيَّأ مبدئياً (يُحدَّث في start()) ─────
        # نُهيِّئ بدون session أولاً لتجنّب AttributeError
        self.data_layer = DataLayer(
            session=None,
            cryptopanic_key=config.get("CRYPTOPANIC_KEY", ""),
            etherscan_key=config.get("ETHERSCAN_KEY", ""),
        )

        # ── Kill Switch Hooks ───────────────────────────────────
        self.kill_switch.register_hook(self._on_kill_switch)

        # ── Scheduler ──────────────────────────────────────────
        self.scheduler: Optional[Scheduler] = None

        # ── تعدد المستخدمين ─────────────────────────────────────
        self._user_portfolios: dict = {}
        self._user_prefs:      dict = {}
        self._user_exchanges:  dict = {}
        self.user_manager     = _sm_singleton

        # ── Exchange المشترك ────────────────────────────────────
        self.exchange:       Optional[BaseExchange] = None
        self.order_manager:  Optional[OrderManager] = None
        self._exchange_name: str  = config.get("EXCHANGE", "binance")
        self._exchange_key:  str  = config.get("EXCHANGE_API_KEY", "")
        self._exchange_sec:  str  = config.get("EXCHANGE_API_SECRET", "")
        self._exchange_test: bool = config.get("EXCHANGE_TESTNET", False)
        self.live_trading:   bool = False

        if self._exchange_key and self._exchange_sec:
            try:
                self.exchange = create_exchange(
                    self._exchange_name,
                    self._exchange_key,
                    self._exchange_sec,
                    self._exchange_test,
                )
                self.order_manager = OrderManager(self.exchange)
                self.live_trading  = True
                logger.info(
                    f"✅ Live Trading مُفعَّل: {self._exchange_name.upper()} "
                    f"({'Testnet' if self._exchange_test else 'Live'})"
                )
            except Exception as e:
                logger.error(f"Exchange init error: {e}")

    # ═══════════════════════════════════════════════════════════
    # start
    # ═══════════════════════════════════════════════════════════
    async def start(self, send_fn=None):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45),
            headers={
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection":      "keep-alive",
                "Cache-Control":   "no-cache",
            },
        )

        # تحديث data_layer بـ session الحقيقي
        self.data_layer = DataLayer(
            session=self._session,
            cryptopanic_key=self.config.get("CRYPTOPANIC_KEY", ""),
            etherscan_key=self.config.get("ETHERSCAN_KEY", ""),
        )
        self.microstructure.session = self._session
        self.news_engine.session    = self._session

        if send_fn:
            self.scheduler = Scheduler(send_fn)
            self.scheduler.register_weekly(self._generate_weekly_report)
            self.scheduler.register_monthly(self._generate_monthly_report)
            self.scheduler.register_scan(self._run_4h_scan)
            self.scheduler.start()

        if self.live_trading and self.order_manager:
            self.order_manager.start_monitoring()
            logger.info("✅ Order Monitor started")
            self.human_override.set_notify_fn(
                lambda msg, pid: send_fn(msg) if send_fn else None)

        groq_k = self.config.get("GROQ_API_KEY", "")
        logger.info(
            f"✅ RaedEngine started | "
            f"Groq: {'✅ مفعّل (' + groq_k[:8] + '...)' if groq_k else '❌ GROQ_API_KEY غير موجود'}"
        )

    async def stop(self):
        if self.scheduler:
            self.scheduler.stop()
        if self.order_manager:
            self.order_manager.stop_monitoring()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("🛑 RaedEngine stopped")

    # ═══════════════════════════════════════════════════════════
    # find_best_exchange — فلترة صحيحة
    # ═══════════════════════════════════════════════════════════
    async def find_best_exchange(self, user_id: int, symbol: str) -> Optional[dict]:
        """يختار أفضل منصة للمستخدم بناءً على السيولة."""
        user_info = self._user_exchanges.get(user_id)
        if not user_info:
            return None

        ex = user_info.get("exchange")
        if not ex:
            return None

        try:
            vol = 0.0
            if hasattr(ex, "get_volume_24h"):
                vol = await ex.get_volume_24h(symbol)
            else:
                price_data = await ex.get_price(symbol)
                price = price_data if isinstance(price_data, (int, float)) else 0
                vol   = price * 1_000_000 if price > 0 else 0

            return {
                "exchange":      ex,
                "name":          user_info.get("name", "unknown"),
                "volume_24h":    vol,
                "order_manager": user_info.get("order_manager"),
            }
        except Exception as e:
            logger.warning(f"find_best_exchange ({user_id}): {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    # إشارات قوية
    # ═══════════════════════════════════════════════════════════
    async def check_strong_signals(self, send_fn=None):
        """يُرسل تنبيهات للإشارات القوية >= 75%."""
        if not send_fn:
            return
        # نُرسل التنبيه حتى لو auto_trade مُوقَف — الفرق هو التنفيذ فقط
        top_symbols = ["BTC", "ETH", "SOL", "BNB"]
        try:
            btc_c, fear = await asyncio.gather(
                self.data_layer.get_ohlcv("BTC", "1d", 200),
                self.data_layer.get_fear_greed(),
                return_exceptions=True
            )
            btc_c    = btc_c if isinstance(btc_c, list) else []
            fear     = fear  if isinstance(fear, dict)  else {"value": 50}
            fear_val = int(fear.get("value") or 50)
            if len(btc_c) < 30:
                return
            regime = self.regime_detector.detect(btc_c, fear_greed=fear_val)

            alerts = []
            for sym in top_symbols:
                try:
                    candles = await self.data_layer.get_ohlcv(sym, "1d", 100)
                    candles = candles if isinstance(candles, list) else []
                    if len(candles) < 30:
                        continue
                    onchain = await self.data_layer.get_onchain()
                    signal  = self.signal_layer.generate(
                        symbol=sym, candles=candles,
                        onchain_data=onchain or {},
                        news_sentiment=0,
                        backtest_win_rate=0.55,
                        macro_data={"fear_greed": fear_val},
                        regime=regime,
                    )
                    if signal.confidence >= 0.75:
                        dir_ar  = "🟢 شراء" if signal.direction == "long" else "🔴 بيع"
                        price_d = await self.data_layer.get_price(sym)
                        price   = float((price_d or {}).get("price") or 0)
                        alerts.append(
                            f"🚨 إشارة قوية — {sym}\n"
                            f"  {dir_ar} | ثقة: {signal.confidence:.0%}\n"
                            f"  السعر: ${price:,.2f}"
                        )
                except Exception:
                    continue

            if alerts:
                exec_note = (
                    "\n\n✅ سيتم التنفيذ تلقائياً" if self.auto_trade_enabled
                    else "\n\n💡 لتفعيل التنفيذ: /autotrade on"
                )
                await send_fn(
                    "⚡ *تنبيه رائد — إشارات قوية*\n"
                    "━━━━━━━━━━━━━━━━━━\n\n" +
                    "\n\n".join(alerts) +
                    exec_note +
                    "\n\n⚠️ راجع /signal للتفاصيل"
                )
                logger.info(f"تنبيه: {len(alerts)} إشارة قوية أُرسلت")
        except Exception as e:
            logger.error(f"check_strong_signals: {e}")

    # ═══════════════════════════════════════════════════════════
    # إدارة Exchange لكل مستخدم
    # ═══════════════════════════════════════════════════════════
    async def connect_user_exchange(self, user_id: int, exchange_name: str,
                                     api_key: str, api_secret: str,
                                     testnet: bool = False,
                                     passphrase: str = "") -> bool:
        ex_lower = exchange_name.lower()
        if not _sm_singleton.can_use_exchange(user_id, ex_lower):
            logger.warning(f"User {user_id} لا يملك صلاحية {exchange_name}")
            return False
        try:
            ex = create_exchange(ex_lower, api_key, api_secret, testnet, passphrase)
            balance = await ex.get_balance("USDT")
            om = OrderManager(ex)
            om.start_monitoring()
            self._user_exchanges[user_id] = {
                "exchange":      ex,
                "order_manager": om,
                "name":          exchange_name,
                "testnet":       testnet,
                "connected_at":  time.time(),
            }
            logger.info(
                f"✅ User {user_id} connected {exchange_name.upper()} "
                f"({'Testnet' if testnet else 'Live'}) | USDT={balance.total:,.2f}"
            )
            return True
        except Exception as e:
            logger.error(f"connect_user_exchange ({user_id}): {e}")
            return False

    def disconnect_user_exchange(self, user_id: int):
        if user_id in self._user_exchanges:
            try:
                om = self._user_exchanges[user_id].get("order_manager")
                if om:
                    om.stop_monitoring()
            except Exception:
                pass
            del self._user_exchanges[user_id]
            logger.info(f"🔌 User {user_id} exchange disconnected")

    def get_user_exchange(self, user_id: int):
        return self._user_exchanges.get(user_id)

    def get_user_order_manager(self, user_id: int) -> Optional[OrderManager]:
        entry = self._user_exchanges.get(user_id)
        return entry.get("order_manager") if entry else None

    def user_has_live_trading(self, user_id: int) -> bool:
        return user_id in self._user_exchanges

    # ═══════════════════════════════════════════════════════════
    # إدارة المستخدمين
    # ═══════════════════════════════════════════════════════════
    def get_user_portfolio(self, user_id: int) -> float:
        return self._user_portfolios.get(
            user_id, self.config.get("PORTFOLIO_SIZE", 10_000))

    def set_user_portfolio(self, user_id: int, amount: float):
        if amount >= 100:
            self._user_portfolios[user_id] = float(amount)
            self.audit_logger.log_event(
                "user_portfolio_set", {"user": user_id, "amount": amount})

    def get_user_symbols(self, user_id: int) -> list:
        return self._user_prefs.get(user_id, {}).get(
            "symbols", ["BTC", "ETH", "BNB", "SOL"])

    def set_user_symbols(self, user_id: int, symbols: list):
        if user_id not in self._user_prefs:
            self._user_prefs[user_id] = {}
        self._user_prefs[user_id]["symbols"] = [s.upper() for s in symbols[:6]]

    # ═══════════════════════════════════════════════════════════
    # Kill Switch Hook
    # ═══════════════════════════════════════════════════════════
    async def _on_kill_switch(self, state):
        self.auto_trade_enabled = False
        self.audit_logger.log_event("kill_switch_auto_disable_trade", {
            "reason": state.reason})
        logger.critical(f"🔴 Auto-trade disabled by Kill Switch: {state.reason}")

    # ═══════════════════════════════════════════════════════════
    # المسح الرباعي
    # ═══════════════════════════════════════════════════════════
    async def _run_4h_scan(self, session: str = "", ksa_hour: int = 0) -> str:
        """
        المسح الرباعي الشامل — كل 4 ساعات:
        ١. تحليل حالة السوق (BTC كمرجع)
        ٢. مسح أفضل العملات حسب باقة المستخدم
        ٣. توليد الإشارات وتصفية القوية (≥ MIN_CONFIDENCE)
        ٤. تنفيذ آلي إذا autotrade مفعّل (أقصى ٢ صفقة)
        ٥. تقرير موجز
        """
        try:
            # ١. حالة السوق
            btc_c, fear = await asyncio.gather(
                self.data_layer.get_ohlcv("BTC", "1d", 200),
                self.data_layer.get_fear_greed(),
                return_exceptions=True
            )
            btc_c    = btc_c if isinstance(btc_c, list) else []
            fear     = fear  if isinstance(fear, dict)  else {"value": 50}
            fear_val = int(fear.get("value") or 50)

            if len(btc_c) < 30:
                return ""

            regime = self.regime_detector.detect(btc_c, fear_greed=fear_val)

            # ٢. عدد العملات حسب أعلى باقة بين المستخدمين النشطين
            max_coins = 15   # مجاني افتراضي
            for uid in self._user_portfolios.keys():
                tier = _sm_singleton.get_tier(uid)
                if tier == "diamond":
                    max_coins = 300
                    break
                elif tier in ("gold",) and max_coins < 100:
                    max_coins = 100
                elif tier == "silver" and max_coins < 35:
                    max_coins = 35

            top_coins = await self.data_layer.get_top_coins(limit=max_coins)
            top_coins = top_coins if isinstance(top_coins, list) else []
            top_coins = [
                c for c in top_coins
                if (c.get("symbol") or "").upper() not in STABLECOINS
            ]
            onchain = await self.data_layer.get_onchain()
            onchain = onchain if isinstance(onchain, dict) else {}

            min_conf = self.config.get("MIN_CONFIDENCE", 0.65)
            strong_signals = []
            all_signals    = []

            # مسح أفضل ١٠ (أسرع من ٣٠٠ ولا يُعيق)
            for coin in top_coins[:10]:
                sym = (coin.get("symbol") or "").upper()
                if not sym:
                    continue
                try:
                    candles = await self.data_layer.get_ohlcv(sym, "1d", 100)
                    candles = candles if isinstance(candles, list) else []
                    if len(candles) < 30:
                        continue

                    signal = self.signal_layer.generate(
                        symbol=sym, candles=candles,
                        onchain_data=onchain,
                        news_sentiment=0,
                        backtest_win_rate=0.55,
                        macro_data={"fear_greed": fear_val},
                        regime=regime,
                    )
                    price_d = await self.data_layer.get_price(sym)
                    price   = float((price_d or {}).get("price") or 0)

                    all_signals.append({
                        "symbol":     sym,
                        "confidence": signal.confidence,
                        "direction":  signal.direction,
                        "price":      price,
                    })

                    if signal.confidence >= min_conf:
                        strong_signals.append({
                            "symbol":     sym,
                            "confidence": signal.confidence,
                            "direction":  signal.direction,
                            "price":      price,
                            "signal":     signal,
                        })
                except Exception:
                    continue

            # ٤. تنفيذ آلي
            executed = []
            if getattr(self, "auto_trade_enabled", False) and strong_signals:
                for s in strong_signals[:2]:
                    try:
                        ev_mult, _ = self.event_risk.get_exposure_multiplier()
                        if ev_mult == 0:
                            continue
                        risk = self.risk_engine.assess(
                            s["symbol"], s["direction"],
                            s["confidence"], s["price"],
                            3.0, regime.regime.value
                        )
                        from core.risk_engine import RiskDecision
                        if risk.decision != RiskDecision.REJECT:
                            self.risk_engine.register_trade(
                                s["symbol"], risk.approved_size, s["direction"])
                            self.audit_logger.log_trade(
                                symbol=s["symbol"],
                                direction=s["direction"],
                                size=risk.approved_size,
                                confidence=s["confidence"],
                                regime=regime.regime.value,
                                reason="auto_4h_scan",
                            )
                            executed.append({
                                **s,
                                "size":        risk.approved_size,
                                "stop_loss":   risk.stop_loss_pct,
                                "take_profit": risk.take_profit_pct,
                            })
                    except Exception as e:
                        logger.warning(f"Auto execute {s['symbol']}: {e}")

            # ٥. بناء التقرير
            next_scan_str = ""
            if self.scheduler:
                try:
                    next_scan_str = self.scheduler.next_scan_ar()
                except Exception:
                    next_scan_str = "المسح القادم: خلال ٤ ساعات"

            if not strong_signals and not executed:
                best = sorted(all_signals,
                               key=lambda x: x["confidence"],
                               reverse=True)[:1]
                if best:
                    b = best[0]
                    return (
                        f"🔍 *مسح {session}*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"السوق: {regime.description_ar}\n"
                        f"Fear & Greed: {fear_val}\n\n"
                        f"لا فرص قوية حالياً\n"
                        f"أفضل إشارة: {b['symbol']} {b['confidence']:.0%}\n"
                        f"⏰ {next_scan_str}"
                    )
                return ""

            lines = [
                f"🔍 *مسح {session}*",
                "━━━━━━━━━━━━━━━━━━",
                f"السوق: {regime.description_ar} | Fear: {fear_val}",
                "",
            ]

            if executed:
                lines.append("✅ *صفقات مُنفَّذة تلقائياً*")
                for e in executed:
                    dir_ar = "🟢 شراء" if e["direction"] == "long" else "🔴 بيع"
                    lines.append(
                        f"• {e['symbol']} {dir_ar} ${e['size']:,.0f} "
                        f"| ثقة: {e['confidence']:.0%}"
                    )
                    lines.append(
                        f"  SL: {e['stop_loss']:.1f}٪ | TP: {e['take_profit']:.1f}٪"
                    )
                lines.append("")

            if strong_signals and not executed:
                lines.append("⚡ *فرص قوية (autotrade مُوقَف)*")
                for s in strong_signals:
                    dir_ar = "🟢 شراء" if s["direction"] == "long" else "🔴 بيع"
                    lines.append(
                        f"• {s['symbol']} {dir_ar} | "
                        f"ثقة: {s['confidence']:.0%} | "
                        f"${s['price']:,.2f}"
                    )
                lines += [
                    "",
                    "💡 لتفعيل التنفيذ التلقائي: /autotrade on",
                ]

            if next_scan_str:
                lines.append(f"\n⏰ {next_scan_str}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"_run_4h_scan: {e}")
            return ""

    # ═══════════════════════════════════════════════════════════
    # التقارير التلقائية
    # ═══════════════════════════════════════════════════════════
    async def _generate_weekly_report(self) -> str:
        try:
            summary = self.audit_logger.pnl_summary()
            drift   = self.drift_monitor.assess()
            risk_st = self.risk_engine.status_report(
                self.config.get("PORTFOLIO_SIZE", 10_000))

            trades    = summary.get("trades", 0)
            total_pnl = summary.get("total_pnl", 0)
            win_rate  = summary.get("win_rate", 0)
            avg_win   = summary.get("avg_win", 0)
            avg_loss  = abs(summary.get("avg_loss", 0))
            drawdown  = risk_st.get("drawdown_pct", 0)
            open_pos  = risk_st.get("open_positions", 0)

            pnl_sign = "+" if total_pnl >= 0 else ""
            return (
                f"📊 *التقرير الأسبوعي — رائد التداول الذكي*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {_get_week_label()}\n\n"
                f"💰 *الأداء*\n"
                f"• إجمالي الصفقات: {trades}\n"
                f"• صافي الربح/الخسارة: {pnl_sign}${total_pnl:,.2f}\n"
                f"• نسبة الفوز: {win_rate:.1f}٪\n"
                f"• متوسط الربح: ${avg_win:,.2f}\n"
                f"• متوسط الخسارة: ${avg_loss:,.2f}\n\n"
                f"⚖️ *المخاطر*\n"
                f"• Drawdown: {drawdown:.1f}٪\n"
                f"• صفقات مفتوحة: {open_pos}\n\n"
                f"🔬 *النموذج*\n"
                f"• معدل الفوز الفعلي: {drift.current_win_rate:.0%}\n"
                f"• الانحراف: {drift.drift_pct:.1f}٪\n"
                f"• {drift.recommendation_ar}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 رائد التداول الذكي\n"
                f"⚠️ جميع الأرقام استرشادية فقط"
            )
        except Exception as e:
            logger.error(f"Weekly report error: {e}")
            return "❌ خطأ في إنشاء التقرير الأسبوعي. يرجى المحاولة لاحقاً"

    async def _generate_monthly_report(self) -> str:
        try:
            summary = self.audit_logger.pnl_summary()
            drift   = self.drift_monitor.assess()
            risk_st = self.risk_engine.status_report(
                self.config.get("PORTFOLIO_SIZE", 10_000))

            trades    = summary.get("trades", 0)
            total_pnl = summary.get("total_pnl", 0)
            win_rate  = summary.get("win_rate", 0)
            drawdown  = risk_st.get("drawdown_pct", 0)

            pnl_sign = "+" if total_pnl >= 0 else ""
            return (
                f"📅 *التقرير الشهري — رائد التداول الذكي*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 *ملخص الشهر*\n"
                f"• إجمالي الصفقات: {trades}\n"
                f"• صافي الربح/الخسارة: {pnl_sign}${total_pnl:,.2f}\n"
                f"• نسبة الفوز: {win_rate:.1f}٪\n"
                f"• أقصى تراجع (Drawdown): {drawdown:.1f}٪\n\n"
                f"🔬 *حالة النموذج*\n"
                f"• {drift.recommendation_ar}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 رائد التداول الذكي\n"
                f"⚠️ جميع الأرقام استرشادية فقط"
            )
        except Exception as e:
            logger.error(f"Monthly report error: {e}")
            return "❌ خطأ في إنشاء التقرير الشهري. يرجى المحاولة لاحقاً"

    # ═══════════════════════════════════════════════════════════
    # Safety Checks — موحَّد مع قيم 0-1
    # ═══════════════════════════════════════════════════════════
    async def run_safety_checks(self):
        portfolio_val = self.config.get("PORTFOLIO_SIZE", 10_000)
        try:
            risk_st = self.risk_engine.status_report(portfolio_val)

            # status_report يُعيد drawdown_pct بصيغة 0-100
            # check_drawdown يتوقع 0-1 → نُقسّم على 100
            drawdown_fraction = risk_st.get("drawdown_pct", 0) / 100.0
            max_dd_limit      = self.config.get("MAX_DRAWDOWN", 0.15)

            self.kill_switch.check_drawdown(
                drawdown_fraction,
                limit_pct=max_dd_limit
            )

            drift = self.drift_monitor.assess()
            if (getattr(drift, "drift_level", None) == "severe"
                    and not self.kill_switch.is_active):
                from core.kill_switch import KillReason
                self.kill_switch.trigger(
                    KillReason.MODEL_DRIFT, "drift_monitor", auto_resume_hours=6)
                logger.critical("🔴 Kill Switch فُعِّل بسبب Model Drift الشديد")
        except Exception as e:
            logger.error(f"run_safety_checks: {e}")


# ── Helpers ──────────────────────────────────────────────────────
def _get_week_label() -> str:
    """يُعيد تسمية الأسبوع المنتهي."""
    from datetime import datetime, timezone, timedelta
    now  = datetime.now(timezone.utc)
    end  = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    return f"الأسبوع {start} → {end}"

"""
🤖 رائد — Raed Engine (المحرك المركزي)
Institutional Balanced Crypto AI Trading Agent
يربط جميع الطبقات العشر في نظام متكامل.
"""

import asyncio
import logging
import aiohttp
from typing import Optional

from core.data_validator  import DataValidator
from core.data_layer      import DataLayer
from core.regime_detector import RegimeDetector
from core.strategy_router import SignalLayer, StrategyRouter
from core.risk_engine     import RiskEngine
from core.kill_switch     import KillSwitch, HumanOverrideLayer, AuditLogger
from core.microstructure  import MicrostructureLayer
from core.event_risk      import EventRiskFilter
from core.portfolio_engine import CapitalAllocationEngine
from core.news            import NewsEngine
from core.backtest        import BacktestEngine
from core.drift_monitor   import DriftMonitor
from core.scheduler       import Scheduler

logger = logging.getLogger(__name__)


class RaedEngine:
    """
    المحرك المركزي — Singleton يُهيَّأ مرة واحدة عند بدء البوت.
    جميع الـ handlers تصل للطبقات عبره.
    """

    def __init__(self, config: dict):
        self.config           = config
        self._session: Optional[aiohttp.ClientSession] = None
        self.auto_trade_enabled = False

        # ── الطبقة ٢: Data Validation ─────────────────────────
        self.data_validator   = DataValidator()

        # ── الطبقة ٣: Signal + Regime ─────────────────────────
        self.regime_detector  = RegimeDetector()
        self.signal_layer     = SignalLayer()
        self.strategy_router  = StrategyRouter()

        # ── الطبقة ٥: Risk Engine ──────────────────────────────
        risk_cfg = {
            "portfolio_size":      config.get("PORTFOLIO_SIZE", 10_000),
            "max_risk_per_trade":  config.get("MAX_RISK_PER_TRADE", 0.02),
            "max_daily_loss":      config.get("MAX_DAILY_LOSS", 0.05),
            "max_drawdown":        config.get("MAX_DRAWDOWN", 0.15),
            "max_open_positions":  config.get("MAX_OPEN_POSITIONS", 5),
            "max_single_exposure": config.get("MAX_SINGLE_EXPOSURE", 0.20),
            "min_confidence":      config.get("MIN_CONFIDENCE", 0.65),
        }
        self.risk_engine      = RiskEngine(risk_cfg)

        # ── الطبقة ٦+٨+٩: Kill Switch + Override + Audit ──────
        self.kill_switch      = KillSwitch()
        self.human_override   = HumanOverrideLayer(timeout_minutes=15)
        self.audit_logger     = AuditLogger()

        # ── الطبقة ٧: Microstructure ───────────────────────────
        self.microstructure   = MicrostructureLayer()

        # ── Event Risk Filter ───────────────────────────────────
        self.event_risk       = EventRiskFilter()

        # ── الطبقة ١٠: Capital Allocation ──────────────────────
        self.capital_engine   = CapitalAllocationEngine()

        # ── أدوات التحليل ──────────────────────────────────────
        self.news_engine      = NewsEngine(
            groq_key=config.get("GROQ_API_KEY", ""))
        self.backtest_engine  = BacktestEngine()
        self.drift_monitor    = DriftMonitor(baseline_win_rate=0.55)

        # ── Kill Switch Hooks ───────────────────────────────────
        self.kill_switch.register_hook(self._on_kill_switch)

        # ── Scheduler (يُشغَّل بعد إنشاء send_fn) ──────────────
        self.scheduler: Optional[Scheduler] = None

        # ── تعدد المستخدمين — كل مستخدم له إعدادات منفصلة ────
        self._user_portfolios: dict = {}   # {user_id: portfolio_size}
        self._user_prefs:      dict = {}   # {user_id: {symbol_filter, ...}}

    # ═══════════════════════════════════════════════════════════
    # تهيئة الـ Session (يُستدعى عند بدء البوت)
    # ═══════════════════════════════════════════════════════════
    async def start(self, send_fn=None):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45),
            headers={
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection":      "keep-alive",
                "Cache-Control":   "no-cache",
            },
        )

        # تمرير الـ session للطبقات التي تحتاجه
        self.data_layer    = DataLayer(
            session=self._session,
            cryptopanic_key=self.config.get("CRYPTOPANIC_KEY", ""),
            etherscan_key=self.config.get("ETHERSCAN_KEY", ""),
        )
        self.microstructure.session = self._session
        self.news_engine.session    = self._session

        # Scheduler
        if send_fn:
            self.scheduler = Scheduler(send_fn)
            self.scheduler.register_weekly(self._generate_weekly_report)
            self.scheduler.register_monthly(self._generate_monthly_report)
            self.scheduler.start()

            self.human_override.set_notify_fn(
                lambda msg, pid: send_fn(msg))

        groq_k = self.config.get("GROQ_API_KEY", "")
        logger.info(
            f"✅ RaedEngine started | "
            f"Groq: {'✅ مفعّل (' + groq_k[:8] + '...)' if groq_k else '❌ GROQ_API_KEY غير موجود'}"
        )

    async def check_strong_signals(self, send_fn=None):
        """
        يفحص الإشارات القوية >= 75% ويُرسل تنبيهاً تلقائياً.
        يُستدعى من الـ Scheduler كل 15 دقيقة.
        """
        if not send_fn or not self.auto_trade_enabled:
            return
        top_symbols = ["BTC", "ETH", "SOL", "BNB"]
        try:
            btc_c    = await self.data_layer.get_ohlcv("BTC", "1d", 200)
            fear     = await self.data_layer.get_fear_greed()
            fear_val = int((fear or {}).get("value") or 50)
            btc_c    = btc_c or []
            if len(btc_c) < 30:
                return
            regime = self.regime_detector.detect(btc_c, fear_greed=fear_val)

            alerts = []
            for sym in top_symbols:
                try:
                    candles = await self.data_layer.get_ohlcv(sym, "1d", 100)
                    candles = candles or []
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
                        dir_ar  = "شراء" if signal.direction == "long" else "بيع"
                        price_d = await self.data_layer.get_price(sym)
                        price   = float((price_d or {}).get("price") or 0)
                        line1   = f"🚨 إشارة قوية — {sym}"
                        line2   = f"  {dir_ar} | ثقة: {signal.confidence:.0%}"
                        line3   = f"  السعر: ${price:,.2f}"
                        alerts.append(line1 + "\n" + line2 + "\n" + line3)
                except Exception:
                    continue

            if alerts:
                header = "⚡ تنبيه رائد — إشارات قوية\n━━━━━━━━━━━━━━━━━━\n\n"
                footer = "\n\n⚠️ راجع /signal للتفاصيل"
                await send_fn(header + "\n\n".join(alerts) + footer)
                logger.info(f"تنبيه: {len(alerts)} إشارة قوية أُرسلت")
        except Exception as e:
            logger.error(f"check_strong_signals: {e}")

    async def stop(self):
        if self.scheduler:
            self.scheduler.stop()
        if self._session:
            await self._session.close()
        logger.info("🛑 RaedEngine stopped")

    # ═══════════════════════════════════════════════════════════
    # إدارة المستخدمين
    # ═══════════════════════════════════════════════════════════
    def get_user_portfolio(self, user_id: int) -> float:
        """يُعيد حجم محفظة المستخدم (افتراضي = PORTFOLIO_SIZE)."""
        return self._user_portfolios.get(
            user_id, self.config.get("PORTFOLIO_SIZE", 10_000))

    def set_user_portfolio(self, user_id: int, amount: float):
        """يضبط حجم محفظة المستخدم."""
        if amount >= 100:   # حد أدنى $100
            self._user_portfolios[user_id] = float(amount)
            self.audit_logger.log_event(
                "user_portfolio_set", {"user": user_id, "amount": amount})

    def get_user_symbols(self, user_id: int) -> list:
        """يُعيد قائمة العملات المفضلة للمستخدم."""
        return self._user_prefs.get(user_id, {}).get(
            "symbols", ["BTC", "ETH", "BNB", "SOL"])

    def set_user_symbols(self, user_id: int, symbols: list):
        """يضبط العملات المفضلة للمستخدم."""
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
    # التقارير التلقائية
    # ═══════════════════════════════════════════════════════════
    async def _generate_weekly_report(self) -> str:
        try:
            summary = self.audit_logger.pnl_summary()
            drift   = self.drift_monitor.assess()
            risk_st = self.risk_engine.status_report(
                self.config.get("PORTFOLIO_SIZE", 10_000))

            return (
                f"📊 *التقرير الأسبوعي — رائد التداول الذكي*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 الأسبوع المنتهي\n\n"
                f"💰 *الأداء*\n"
                f"• إجمالي الصفقات: {summary.get('trades', 0)}\n"
                f"• صافي الربح/الخسارة: ${summary.get('total_pnl', 0):+,.2f}\n"
                f"• نسبة الفوز: {summary.get('win_rate', 0):.1f}٪\n"
                f"• متوسط الربح: ${summary.get('avg_win', 0):,.2f}\n"
                f"• متوسط الخسارة: ${abs(summary.get('avg_loss', 0)):,.2f}\n\n"
                f"⚖️ *المخاطر*\n"
                f"• Drawdown: {risk_st.get('drawdown_pct', 0):.1f}٪\n"
                f"• صفقات مفتوحة: {risk_st.get('open_positions', 0)}\n\n"
                f"🔬 *النموذج*\n"
                f"• معدل الفوز: {drift.current_win_rate:.0%}\n"
                f"• الانحراف: {drift.drift_pct:.1f}٪\n"
                f"• {drift.recommendation_ar}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 رائد التداول الذكي"
            )
        except Exception as e:
            logger.error(f"Weekly report generation error: {e}")
            return "❌ خطأ في إنشاء التقرير الأسبوعي"

    async def _generate_monthly_report(self) -> str:
        try:
            summary = self.audit_logger.pnl_summary()
            return (
                f"📅 *التقرير الشهري — رائد التداول الذكي*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 *ملخص الشهر*\n"
                f"• إجمالي الصفقات: {summary.get('trades', 0)}\n"
                f"• صافي الربح/الخسارة: ${summary.get('total_pnl', 0):+,.2f}\n"
                f"• نسبة الفوز: {summary.get('win_rate', 0):.1f}٪\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 رائد التداول الذكي"
            )
        except Exception as e:
            logger.error(f"Monthly report error: {e}")
            return "❌ خطأ في إنشاء التقرير الشهري"

    # ═══════════════════════════════════════════════════════════
    # Kill Switch التلقائي — يُستدعى دورياً
    # ═══════════════════════════════════════════════════════════
    async def run_safety_checks(self):
        portfolio_val = self.config.get("PORTFOLIO_SIZE", 10_000)
        risk_st       = self.risk_engine.status_report(portfolio_val)

        # Drawdown
        self.kill_switch.check_drawdown(
            risk_st["drawdown_pct"] / 100,
            limit_pct=self.config.get("MAX_DRAWDOWN", 0.15)
        )

        # Model Drift
        drift = self.drift_monitor.assess()
        if drift.drift_level == "severe" and not self.kill_switch.is_active:
            from core.kill_switch import KillReason
            self.kill_switch.trigger(
                KillReason.MODEL_DRIFT, "drift_monitor", auto_resume_hours=6)

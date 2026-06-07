"""
🤖 رائد — Raed Engine (المحرك المركزي)
Institutional Balanced Crypto AI Trading Agent
يربط جميع الطبقات العشر في نظام متكامل.
"""

import asyncio
import time
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
from core.exchange        import create_exchange, BaseExchange, SUPPORTED_EXCHANGES
from core.order_manager   import OrderManager
from core.state_manager   import state_manager as _sm_singleton

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

        # ── الطبقة 2: Data Validation ─────────────────────────
        self.data_validator   = DataValidator()

        # ── الطبقة 3: Signal + Regime ─────────────────────────
        self.regime_detector  = RegimeDetector()
        self.signal_layer     = SignalLayer()
        self.strategy_router  = StrategyRouter()

        # ── الطبقة 5: Risk Engine ──────────────────────────────
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

        # ── الطبقة 6+8+9: Kill Switch + Override + Audit ──────
        self.kill_switch      = KillSwitch()
        self.human_override   = HumanOverrideLayer(timeout_minutes=15)
        self.audit_logger     = AuditLogger()

        # ── الطبقة 7: Microstructure ───────────────────────────
        self.microstructure   = MicrostructureLayer()

        # ── Event Risk Filter ───────────────────────────────────
        self.event_risk       = EventRiskFilter()

        # ── الطبقة 10: Capital Allocation ──────────────────────
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

        # ── Semaphore للأوامر الثقيلة (#178) ──────────────────
        # يمنع تراكم الطلبات عند تزامن عدة مستخدمين
        # 3 طلبات ثقيلة في آن واحد كحد أقصى
        self._heavy_semaphore: Optional[asyncio.Semaphore] = None

        # ── تعدد المستخدمين — كل مستخدم له إعدادات منفصلة ────
        self._user_portfolios: dict = {}   # {user_id: float}
        # _sm يُستورد عند الحاجة — لا نحفظه في __init__ لتجنب circular import
        self._user_prefs:      dict = {}   # {user_id: dict}
        self._user_exchanges:  dict = {}   # {user_id: {"exchange": obj, "order_manager": obj, "name": str, "testnet": bool}}
        self.user_manager     = _sm_singleton  # state_manager مباشرة

        # ── Exchange المشترك (اختياري — من Railway Variables) ──
        self.exchange:       Optional[BaseExchange] = None
        self.order_manager:  Optional[OrderManager] = None
        self._exchange_name: str  = config.get("EXCHANGE", "binance")
        self._exchange_key:  str  = config.get("EXCHANGE_API_KEY", "")
        self._exchange_sec:  str  = config.get("EXCHANGE_API_SECRET", "")
        self._exchange_test: bool = config.get("EXCHANGE_TESTNET", False)
        # يُفعَّل فقط إذا أضاف المشرف مفاتيح في Railway
        self.live_trading:   bool = False  # افتراضي: محفظة افتراضية للجميع
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
            self._send_fn = send_fn  # حفظ للاستخدام في _run_4h_scan
            self.scheduler = Scheduler(send_fn)
            self.scheduler.register_weekly(self._generate_weekly_report)
            self.scheduler.register_monthly(self._generate_monthly_report)
            self.scheduler.register_scan(self._run_4h_scan)
            self.scheduler.start()

        # تشغيل Order Monitor إذا Live Trading مفعّل
        if self.live_trading and self.order_manager:
            self.order_manager.start_monitoring()
            logger.info("✅ Order Monitor started")

            self.human_override.set_notify_fn(
                lambda msg, pid: send_fn(msg))

        # ── استرجاع ربطات المنصات المحفوظة من Redis ──────────
        await self._restore_exchanges_from_redis()

        # تهيئة semaphore داخل event loop (#178)
        self._heavy_semaphore = asyncio.Semaphore(3)

        groq_k = self.config.get("GROQ_API_KEY", "")
        logger.info(
            f"✅ RaedEngine started | "
            f"Groq: {'✅ مفعّل (' + groq_k[:8] + '...)' if groq_k else '❌ GROQ_API_KEY غير موجود'}"
        )

    async def _restore_exchanges_from_redis(self):
        """
        يسترجع جميع ربطات المنصات المحفوظة في Redis بعد كل Deploy.
        يُعيد الاتصال تلقائياً لجميع المستخدمين.
        """
        try:
            exchange_users = _sm_singleton.get_all_exchange_users()
            if not exchange_users:
                logger.info("لا توجد ربطات محفوظة في Redis")
                return

            logger.info(f"🔄 استرجاع {len(exchange_users)} ربط من Redis...")
            restored = 0

            for user_id in exchange_users:
                creds = _sm_singleton.load_exchange_credentials(user_id)
                if not creds:
                    continue
                try:
                    ex_name    = creds["exchange_name"]
                    api_key    = creds["api_key"]
                    api_secret = creds["api_secret"]
                    passphrase = creds["passphrase"]
                    testnet    = creds["testnet"]

                    if not api_key or not api_secret:
                        continue

                    ex = create_exchange(ex_name, api_key, api_secret,
                                         testnet, passphrase)
                    om = OrderManager(ex)
                    om.start_monitoring()
                    self._user_exchanges[user_id] = {
                        "exchange":      ex,
                        "order_manager": om,
                        "name":          ex_name,
                        "testnet":       testnet,
                        "connected_at":  creds.get("saved_at", time.time()),
                    }
                    restored += 1
                    logger.info(f"✅ استُرجع ربط: user={user_id} ex={ex_name}")
                except Exception as e:
                    logger.warning(f"فشل استرجاع ربط user={user_id}: {e}")
                    # إذا فشل الاسترجاع → احذف credentials الفاسدة
                    _sm_singleton.delete_exchange_credentials(user_id)

            logger.info(f"✅ استُرجع {restored}/{len(exchange_users)} ربط من Redis")
        except Exception as e:
            logger.error(f"_restore_exchanges_from_redis: {e}")

    async def find_best_exchange(self, user_id: int, symbol: str) -> Optional[dict]:
        """
        يختار أفضل منصة للتنفيذ بناءً على:
        1. المنصات المرتبطة بالمستخدم فعلاً
        2. حجم التداول 24h (الأعلى = الأفضل سيولة)
        يُعيد: {"exchange": obj, "name": str, "volume_24h": float}
        """
        user_exchanges = self._user_exchanges
        if not user_exchanges:
            return None

        best_vol  = -1.0
        best_info = None

        for uid, info in user_exchanges.items():
            if uid != user_id:
                continue
            ex = info.get("exchange")
            if not ex:
                continue
            try:
                vol = 0.0
                if hasattr(ex, "get_volume_24h"):
                    vol = await ex.get_volume_24h(symbol)
                else:
                    # fallback: استخدام السعر × 1M كتقدير
                    price = await ex.get_price(symbol)
                    vol   = price * 1_000_000 if price > 0 else 0

                if vol > best_vol:
                    best_vol  = vol
                    best_info = {
                        "exchange":   ex,
                        "name":       info.get("name", "unknown"),
                        "volume_24h": vol,
                        "order_manager": info.get("order_manager"),
                    }
            except Exception as e:
                logger.warning(f"find_best_exchange ({info.get('name')}): {e}")

        if best_info:
            logger.info(
                f"Best exchange for {symbol}: "
                f"{best_info['name'].upper()} vol=${best_info['volume_24h']/1e6:.0f}M"
            )
        return best_info

    async def _check_virtual_positions(self, regime) -> None:
        """
        يفحص المراكز المفتوحة في virtual wallet ويُغلقها إذا:
        - بلغت Take Profit
        - بلغت Stop Loss
        - انتهت مدة الاحتفاظ (Time Exit)
        يُسجّل النتيجة في drift_monitor للتعلم الذاتي.
        """
        from core.virtual_wallet import VirtualWallet as _VW_c
        from core.state_manager  import state_manager as _sm_c
        import time as _tc

        try:
            for _uid in _sm_c.get_autotrade_users():
                _wdata = _sm_c.get_virtual_wallet(_uid)
                if not _wdata or not _wdata.get("positions"):
                    continue
                _vw = _VW_c(_wdata)
                _changed = False

                for sym in list(_vw.positions.keys()):
                    pos = _vw.positions.get(sym)
                    if not pos:
                        continue
                    try:
                        pd    = await self.data_layer.get_price(sym.replace("USDT",""))
                        price = float((pd or {}).get("price") or 0)
                        if price <= 0:
                            continue
                        tp = float(pos.get("take_profit", 0))
                        sl = float(pos.get("stop_loss",   0))
                        # فحص TP أو SL
                        should_close = (
                            (tp > 0 and price >= tp) or
                            (sl > 0 and price <= sl)
                        )
                        if should_close:
                            result = _vw.sell(sym, price)
                            if result.get("ok"):
                                _changed = True
                                pnl = result.get("trade", {}).get("pnl", 0)
                                was_win = pnl > 0
                                # تسجيل في drift_monitor
                                self.drift_monitor.record_outcome(was_win)
                                reason = "TP ✅" if (tp > 0 and price >= tp) else "SL 🛑"
                                logger.info(
                                    f"Auto-close {sym} uid={_uid}: {reason} "
                                    f"PnL=${pnl:+,.2f}"
                                )
                    except Exception as ep:
                        logger.debug(f"_check_virtual_positions {sym}: {ep}")

                if _changed:
                    _sm_c.save_virtual_wallet(_uid, _vw.to_dict())
        except Exception as e:
            logger.debug(f"_check_virtual_positions: {e}")

    async def _notify_real_users(self, signals: list, regime, send_fn) -> None:
        """
        يُرسل إشعاراً للمستخدمين الحقيقيين (has_live=True)
        مع زرَّي: ✅ نفّذ | ❌ تجاهل
        """
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            import json

            # بناء نص الإشعار
            lines = ["⚡ *فرصة تداول — موافقة مطلوبة*", ""]
            for s in signals[:2]:
                dir_ar = "🟢 شراء" if s["direction"] == "long" else "🔴 بيع"
                lines.append(
                    f"• {s['symbol']} {dir_ar} | "
                    f"ثقة: {s['confidence']:.0%} | "
                    f"${s['price']:,.2f}"
                )
            lines += ["", f"السوق: {regime.description_ar}",
                      "", "هل تريد تنفيذ هذه الصفقات؟"]
            text = "\n".join(lines)

            # بناء callback_data
            sig_data = ",".join(
                f"{s['symbol']}_{s['direction']}_{s['price']:.2f}"
                for s in signals[:2]
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ نفّذ الصفقات",
                    callback_data=f"autotrade_confirm_{sig_data[:60]}"
                ),
                InlineKeyboardButton(
                    "❌ تجاهل",
                    callback_data="autotrade_ignore"
                ),
            ]])

            # إرسال لكل مستخدم حقيقي لديه autotrade مفعّل
            from core.state_manager import state_manager as _sm
            for uid, portfolio in self._user_portfolios.items():
                try:
                    if not _sm.is_autotrade_on(uid):
                        continue
                    ex_info = self.get_user_exchange(uid)
                    if not ex_info:  # فقط المستخدمين الحقيقيين
                        continue
                    await send_fn(
                        chat_id    = uid,
                        text       = text,
                        parse_mode = "Markdown",
                        reply_markup = kb,
                    )
                    logger.info(f"📩 Real user {uid} notified for approval")
                except Exception as ue:
                    logger.debug(f"Notify user {uid}: {ue}")
        except Exception as e:
            logger.warning(f"_notify_real_users: {e}")

    async def check_strong_signals(self, send_fn=None):
        """
        يفحص الإشارات القوية >= 75% ويُرسل تنبيهاً تلقائياً.
        يُستدعى من الـ Scheduler كل 15 دقيقة.
        """
        if not send_fn or not self.auto_trade_enabled:
            return
        # dedup: لا نُرسل نفس مجموعة الإشارات مرتين في 30 دقيقة
        import time as _time_ds
        if not hasattr(self, "_last_alert_ts"):
            self._last_alert_ts = {}
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
                    if signal.confidence >= 0.75 and signal.direction != "neutral":
                        # dedup: لا نُرسل نفس الإشارة مرتين في 30 دقيقة
                        _sig_key = f"{sym}_{signal.direction}"
                        _now_t   = _time_ds.time()
                        if _now_t - self._last_alert_ts.get(_sig_key, 0) < 1800:
                            continue
                        self._last_alert_ts[_sig_key] = _now_t

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
        if self.order_manager:
            self.order_manager.stop_monitoring()
        if self._session:
            await self._session.close()
        logger.info("🛑 RaedEngine stopped")

    # ═══════════════════════════════════════════════════════════
    # إدارة Exchange لكل مستخدم
    # ═══════════════════════════════════════════════════════════
    async def connect_user_exchange(self, user_id: int, exchange_name: str,
                                     api_key: str, api_secret: str,
                                     testnet: bool = False,
                                     passphrase: str = "") -> bool:
        """يربط Exchange خاص بالمستخدم مع فحص صلاحية الباقة."""
        ex_lower = exchange_name.lower()
        # فحص: هل المستخدم مسموح له بهذه المنصة؟
        if not _sm_singleton.can_use_exchange(user_id, ex_lower):
            logger.warning(
                f"User {user_id} لا يملك صلاحية {exchange_name}")
            return False
        try:
            # تنظيف credentials من مسافات ومحارف خفية
            api_key    = api_key.strip()
            api_secret = api_secret.strip()
            passphrase = passphrase.strip()

            ex = create_exchange(ex_lower, api_key, api_secret, testnet, passphrase)

            # ── التحقق من صحة الـ credentials أولاً ──────────────
            if hasattr(ex, "verify_credentials"):
                valid, reason = await ex.verify_credentials()
                if not valid:
                    logger.error(
                        f"connect_user_exchange ({user_id}) {exchange_name}: "
                        f"credentials فاشلة — {reason}"
                    )
                    raise ValueError(reason)

            # ── جلب الرصيد ────────────────────────────────────────
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

            # ── حفظ credentials في Redis للاسترجاع بعد Deploy ──
            _sm_singleton.save_exchange_credentials(
                user_id, exchange_name,
                api_key, api_secret, passphrase, testnet
            )

            logger.info(
                f"✅ User {user_id} connected {exchange_name.upper()} "
                f"({'Testnet' if testnet else 'Live'}) | USDT={balance.total:,.2f}"
            )
            return True
        except Exception as e:
            logger.error(f"connect_user_exchange ({user_id}) {exchange_name}: {e}")
            return False

    def disconnect_user_exchange(self, user_id: int):
        """يفصل Exchange المستخدم ويحذف credentials من Redis."""
        if user_id in self._user_exchanges:
            try:
                om = self._user_exchanges[user_id].get("order_manager")
                if om:
                    om.stop_monitoring()
            except Exception:
                pass
            del self._user_exchanges[user_id]
        # حذف credentials من Redis
        _sm_singleton.delete_exchange_credentials(user_id)
        logger.info(f"🔌 User {user_id} exchange disconnected")

    def get_user_exchange(self, user_id: int):
        """يُعيد Exchange المستخدم أو None."""
        return self._user_exchanges.get(user_id)

    def get_user_order_manager(self, user_id: int) -> Optional[OrderManager]:
        """يُعيد OrderManager المستخدم أو None."""
        entry = self._user_exchanges.get(user_id)
        return entry.get("order_manager") if entry else None

    def user_has_live_trading(self, user_id: int) -> bool:
        """هل المستخدم لديه تداول حقيقي مُفعَّل؟"""
        return user_id in self._user_exchanges

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
    async def acquire_heavy(self):
        """
        context manager للأوامر الثقيلة (#178).
        يضمن عدم تزامن أكثر من 3 طلبات في آن واحد.
        استخدام: async with await engine.acquire_heavy(): ...
        """
        if self._heavy_semaphore is None:
            self._heavy_semaphore = asyncio.Semaphore(3)
        return self._heavy_semaphore

    async def _on_kill_switch(self, state):
        self.auto_trade_enabled = False
        self.audit_logger.log_event("kill_switch_auto_disable_trade", {
            "reason": state.reason})
        logger.critical(f"🔴 Auto-trade disabled by Kill Switch: {state.reason}")

    # ═══════════════════════════════════════════════════════════
    # التقارير التلقائية
    # ═══════════════════════════════════════════════════════════
    async def _run_4h_scan(self, session: str = "", ksa_hour: int = 0) -> str:
        """
        المسح الرباعي الشامل — يعمل كل 4 ساعات:
        1. تحليل حالة السوق
        2. مسح أفضل 5 عملات
        3. اقتناص الفرص القوية
        4. تنفيذ آلي إذا autotrade مفعّل + إشارة ≥ 65%
        5. تقرير موجز للمستخدم
        """
        try:
            # 1. حالة السوق
            btc_c    = await self.data_layer.get_ohlcv("BTC", "1d", 200)
            fear     = await self.data_layer.get_fear_greed()
            btc_c    = btc_c or []
            fear_val = int((fear or {}).get("value") or 50)
            if len(btc_c) < 30:
                return ""

            regime = self.regime_detector.detect(btc_c, fear_greed=fear_val)

            # 2. مسح العملات حسب الباقة
            # افتراضي: 30 للمجاني, 150 للمدفوع
            # نُحدد أقصى حد من جميع المستخدمين النشطين
            max_coins = 150 if any(
                _sm_singleton.is_premium(uid)
                for uid in self._user_portfolios.keys()
            ) else 30
            top_coins = await self.data_layer.get_top_coins(limit=max_coins)
            top_coins = top_coins or []
            # استبعاد العملات المستقرة
            STABLECOINS = {"USDT","USDC","BUSD","DAI","TUSD","USDP","FDUSD",
                            "USDD","GUSD","LUSD","FRAX","MIM","ALUSD","SUSD"}
            top_coins = [
                c for c in top_coins
                if (c.get("symbol") or "").upper() not in STABLECOINS
            ]
            onchain   = await self.data_layer.get_onchain() or {}

            strong_signals = []
            all_signals    = []

            for coin in top_coins[:5]:
                sym = (coin.get("symbol") or "").upper()
                if not sym:
                    continue
                try:
                    candles = await self.data_layer.get_ohlcv(sym, "1d", 100)
                    candles = candles or []
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

                    # 3. إشارات قوية ≥ 65%
                    # إضافة إشارة قوية — يجب أن تكون long أو short (ليس neutral)
                    if signal.confidence >= 0.65 and signal.direction != "neutral":
                        strong_signals.append({
                            "symbol":     sym,
                            "confidence": signal.confidence,
                            "direction":  signal.direction,
                            "price":      price,
                            "signal":     signal,
                        })
                except Exception:
                    continue

            # 3.5 فحص وإغلاق المراكز التي بلغت TP أو SL
            await self._check_virtual_positions(regime)

            # 4. تنفيذ آلي للإشارات القوية
            executed = []
            # إصلاح #569: نتحقق من state_manager بدلاً من self.auto_trade_enabled
            from core.state_manager import state_manager as _sm_chk
            _has_autotrade_users = bool(_sm_chk.get_autotrade_users())
            if _has_autotrade_users and strong_signals:
                from core.risk_engine import RiskDecision
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
                        if risk.decision == RiskDecision.REJECT:
                            continue

                        self.risk_engine.register_trade(
                            s["symbol"], risk.approved_size, s["direction"])
                        self.audit_logger.log_trade(
                            symbol=s["symbol"], direction=s["direction"],
                            size=risk.approved_size, confidence=s["confidence"],
                            regime=regime.regime.value, reason="auto_4h_scan",
                        )
                        trade_rec = {
                            **s,
                            "size":        risk.approved_size,
                            "stop_loss":   risk.stop_loss_pct,
                            "take_profit": risk.take_profit_pct,
                        }

                        # ── Virtual Wallet: تنفيذ فوري لكل مستخدم autotrade ──
                        from core.virtual_wallet import VirtualWallet as _VW
                        from core.state_manager  import state_manager as _sm_vw
                        # Q1-Q5: تحديد نوع المسح لتطبيق القيود
                        import datetime as _dt_scan
                        _now_h = _dt_scan.datetime.now(_dt_scan.timezone.utc).hour
                        _scan_type = "daily"  # الافتراضي = مسح ساعي

                        _autotrade_uids = _sm_vw.get_autotrade_users()
                        if not _autotrade_uids:
                            logger.info("Virtual trade: لا يوجد مستخدمون autotrade نشطون")
                        for _uid in _autotrade_uids:
                            try:
                                # تحميل المحفظة من Redis
                                _wdata = _sm_vw.get_virtual_wallet(_uid)
                                if not _wdata:
                                    _wdata = {"balance": 10000.0, "invested": 0.0,
                                              "profit": 0.0, "positions": {}, "history": []}
                                _vw = _VW(_wdata)

                                # Q1-Q5: فحص قيود التنفيذ
                                _can, _buy_amt, _limit_reason = _sm_vw.can_execute_trade(
                                    _uid, s["symbol"], _scan_type, _vw.total_value)
                                if not _can:
                                    logger.info(f"Trade limit [{_scan_type}] {s['symbol']}: {_limit_reason}")
                                    continue
                                _buy_amt = min(_buy_amt, _vw.balance * 0.95)
                                _buy_amt = max(_buy_amt, 50)

                                _result = _vw.buy(
                                    symbol     = s["symbol"],
                                    price      = s["price"],
                                    amount_usd = _buy_amt,
                                )
                                if _result.get("ok"):
                                    _sm_vw.save_virtual_wallet(_uid, _vw.to_dict())
                                    # Q1-Q5: تسجيل الصفقة
                                    _sm_vw.record_auto_trade(_uid, s["symbol"], _scan_type, _buy_amt)
                                    trade_rec["virtual_executed"] = True
                                    logger.info(
                                        f"✅ Virtual buy: {s['symbol']} "
                                        f"${_buy_amt:,.0f} [{_scan_type}] for user {_uid}")
                                    # إشعار تأكيد للمستخدم (#608)
                                    _sfn_notify = getattr(self, "_send_fn", None)
                                    if _sfn_notify:
                                        _dir_ar = "🟢 شراء" if s["direction"] == "long" else "🔴 بيع"
                                        _confirm_msg = (
                                            "✅ *تم تنفيذ صفقة افتراضية*\n\n"
                                            f"• العملة: {s['symbol']} {_dir_ar}\n"
                                            f"• السعر: ${s['price']:,.2f}\n"
                                            f"• المبلغ: ${_buy_amt:,.0f}\n"
                                            f"• الرصيد المتبقي: ${_vw.balance:,.0f}\n\n"
                                            "🎮 محفظتك الافتراضية — /portfolio للتفاصيل"
                                        )
                                        try:
                                            await _sfn_notify(
                                                _confirm_msg,
                                                user_id=_uid,
                                                parse_mode="Markdown"
                                            )
                                        except Exception:
                                            pass
                                else:
                                    logger.warning(
                                        f"Virtual buy rejected {s['symbol']} "
                                        f"user {_uid}: {_result.get('msg','')}")
                            except Exception as ve:
                                logger.warning(
                                    f"Virtual wallet {s['symbol']} uid={_uid}: {ve}")
                        trade_rec["virtual_executed"] = True

                        executed.append(trade_rec)

                    except Exception as e:
                        logger.warning(f"Auto execute {s['symbol']}: {e}")

            # 4b. إشعار المستخدمين الحقيقيين بزرَّي تأكيد/رفض
            _sfn = getattr(self, "_send_fn", None)
            if strong_signals and _sfn:
                await self._notify_real_users(strong_signals, regime, _sfn)

            # 5. بناء التقرير
            if not strong_signals and not executed:
                # لا شيء مهم — تقرير موجز فقط
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
                        f"⏰ المسح القادم: {self.scheduler.next_scan_ar()}"
                    )
                return ""

            # تقرير الفرص والتنفيذ
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
                        f"  SL: {e['stop_loss']:.1f}% | TP: {e['take_profit']:.1f}%"
                    )
                lines.append("")

            from core.state_manager import state_manager as _sm_stat
            from core.config        import ADMIN_ID
            _at_users  = _sm_stat.get_autotrade_users()
            _at_active = bool(_at_users)
            if strong_signals and not executed:
                if _at_active:
                    # #719: عدد المستخدمين للمدير فقط
                    _is_admin = (send_fn and hasattr(send_fn, '__self__') and
                                 getattr(send_fn, '_admin_mode', False))
                    lines.append("⚡ *فرص قوية — autotrade نشط ✅*")
                else:
                    lines.append("⚡ *فرص قوية (autotrade مُوقَف)*")
                    lines.append("💡 /autotrade on للتفعيل")
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

            lines.append(f"\n⏰ {self.scheduler.next_scan_ar()}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"_run_4h_scan: {e}")
            return ""

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
                f"• نسبة الفوز: {summary.get('win_rate', 0):.1f}%\n"
                f"• متوسط الربح: ${summary.get('avg_win', 0):,.2f}\n"
                f"• متوسط الخسارة: ${abs(summary.get('avg_loss', 0)):,.2f}\n\n"
                f"⚖️ *المخاطر*\n"
                f"• Drawdown: {risk_st.get('drawdown_pct', 0):.1f}%\n"
                f"• صفقات مفتوحة: {risk_st.get('open_positions', 0)}\n\n"
                f"🔬 *النموذج*\n"
                f"• معدل الفوز: {drift.current_win_rate:.0%}\n"
                f"• الانحراف: {drift.drift_pct:.1f}%\n"
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
                f"• نسبة الفوز: {summary.get('win_rate', 0):.1f}%\n\n"
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

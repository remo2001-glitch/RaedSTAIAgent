"""
📊 رائد — Backtest Engine
٣ سنوات بيانات حقيقية من CoinGecko (مجاني)
يقيس: Win Rate · Sharpe · Max Drawdown · Profit Factor · Calmar
"""

import math
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    symbol:          str
    period_days:     int
    total_trades:    int
    winning_trades:  int
    losing_trades:   int
    win_rate:        float
    total_return:    float          # %
    annual_return:   float          # %
    max_drawdown:    float          # %
    sharpe_ratio:    float
    calmar_ratio:    float
    profit_factor:   float
    avg_win:         float          # %
    avg_loss:        float          # %
    best_trade:      float          # %
    worst_trade:     float          # %
    avg_hold_days:   float
    expectancy:      float          # متوسط الربح المتوقع لكل صفقة %
    confidence_score: float         # 0-1 — مدى موثوقية النموذج
    strategy_used:   str
    summary_ar:      str


class BacktestEngine:
    """
    يُطبق استراتيجية على البيانات التاريخية ويقيس الأداء الفعلي.
    يدعم: Trend Following · Mean Reversion · Breakout
    بيانات: CoinGecko Daily OHLCV (مجاني — ٣٦٥ يوم/طلب × ٣ طلبات = ٣ سنوات)
    """

    MIN_PERIODS  = 90    # أقل حد للبيانات المقبولة
    TARGET_YEARS = 3

    # ── معاملات الاستراتيجيات ─────────────────────────────────
    STRATEGIES = {
        "trend_following": {
            "ema_fast": 20, "ema_slow": 50,
            "entry": "ema_cross_bull", "exit": "ema_cross_bear",
            "stop_pct": 0.05, "target_pct": 0.12,
        },
        "mean_reversion": {
            "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
            "entry": "rsi_oversold", "exit": "rsi_mid",
            "stop_pct": 0.04, "target_pct": 0.08,
        },
        "breakout": {
            "lookback": 20,
            "entry": "high_breakout", "exit": "target_or_stop",
            "stop_pct": 0.04, "target_pct": 0.10,
        },
    }

    async def run(self, symbol: str, price_data: List[Dict],
                   strategy: str = "trend_following") -> BacktestResult:
        """
        price_data: [{timestamp, price, volume}, ...] مرتبة تصاعدياً
        """
        if len(price_data) < self.MIN_PERIODS:
            return self._insufficient_data(symbol, len(price_data))

        strat_cfg = self.STRATEGIES.get(strategy, self.STRATEGIES["trend_following"])
        prices    = [d["price"] for d in price_data]
        volumes   = [d.get("volume", 0) for d in price_data]
        timestamps= [d["timestamp"] for d in price_data]

        # ── تشغيل الاستراتيجية ───────────────────────────────
        trades = self._simulate(prices, timestamps, strat_cfg, strategy)

        if not trades:
            return self._no_trades(symbol, strategy, len(price_data))

        return self._compute_metrics(symbol, trades, price_data, strategy)

    def _simulate(self, prices: List[float], timestamps: List[float],
                   cfg: Dict, strategy: str) -> List[Dict]:
        trades  = []
        in_trade = False
        entry_price = entry_ts = 0.0

        # حساب المؤشرات مرة واحدة
        emas_fast  = self._ema_series(prices, cfg.get("ema_fast", 20))
        emas_slow  = self._ema_series(prices, cfg.get("ema_slow", 50))
        rsis       = self._rsi_series(prices, cfg.get("rsi_period", 14))
        highs_roll = self._rolling_max(prices, cfg.get("lookback", 20))

        stop_pct   = cfg.get("stop_pct",   0.05)
        target_pct = cfg.get("target_pct", 0.12)
        start_idx  = max(cfg.get("ema_slow", 50), cfg.get("lookback", 20)) + 1

        for i in range(start_idx, len(prices)):
            price = prices[i]
            prev  = prices[i-1]

            if not in_trade:
                signal = False

                if strategy == "trend_following":
                    # EMA cross bullish
                    signal = (emas_fast[i] > emas_slow[i] and
                               emas_fast[i-1] <= emas_slow[i-1])

                elif strategy == "mean_reversion":
                    # RSI oversold
                    signal = rsis[i] < cfg.get("rsi_oversold", 30)

                elif strategy == "breakout":
                    # كسر أعلى سعر للـ N يوم
                    signal = (price > highs_roll[i-1] * 1.001 and
                               price > prev)

                if signal:
                    in_trade    = True
                    entry_price = price
                    entry_ts    = timestamps[i]

            else:
                # فحص الخروج
                pnl_pct = (price - entry_price) / entry_price

                exit_signal = False
                exit_reason = ""

                # Stop Loss
                if pnl_pct <= -stop_pct:
                    exit_signal = True
                    exit_reason = "stop_loss"

                # Take Profit
                elif pnl_pct >= target_pct:
                    exit_signal = True
                    exit_reason = "take_profit"

                # إشارة خروج من الاستراتيجية
                elif strategy == "trend_following":
                    if emas_fast[i] < emas_slow[i] and emas_fast[i-1] >= emas_slow[i-1]:
                        exit_signal = True
                        exit_reason = "ema_cross_bear"

                elif strategy == "mean_reversion":
                    if rsis[i] > cfg.get("rsi_overbought", 70):
                        exit_signal = True
                        exit_reason = "rsi_overbought"

                if exit_signal:
                    hold_days = (timestamps[i] - entry_ts) / 86400
                    trades.append({
                        "entry":       entry_price,
                        "exit":        price,
                        "pnl_pct":     round(pnl_pct * 100, 3),
                        "hold_days":   round(hold_days, 1),
                        "exit_reason": exit_reason,
                    })
                    in_trade = False

        return trades

    def _compute_metrics(self, symbol: str, trades: List[Dict],
                           price_data: List[Dict], strategy: str) -> BacktestResult:
        pnls      = [t["pnl_pct"] for t in trades]
        wins      = [p for p in pnls if p > 0]
        losses    = [p for p in pnls if p <= 0]
        hold_days = [t["hold_days"] for t in trades]

        win_rate      = len(wins) / len(pnls) if pnls else 0
        total_return  = sum(pnls)
        period_days   = (price_data[-1]["timestamp"] - price_data[0]["timestamp"]) / 86400
        annual_return = total_return / (period_days / 365) if period_days > 0 else 0

        # Max Drawdown (cumulative)
        cum_returns = []
        cum = 100.0
        for p in pnls:
            cum *= (1 + p / 100)
            cum_returns.append(cum)

        peak = 100.0
        max_dd = 0.0
        for cr in cum_returns:
            if cr > peak:
                peak = cr
            dd = (peak - cr) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe
        if len(pnls) > 1:
            mean_r = sum(pnls) / len(pnls)
            std_r  = math.sqrt(sum((p - mean_r)**2 for p in pnls) / len(pnls))
            sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        # Calmar
        calmar = annual_return / max_dd if max_dd > 0 else 0

        # Profit Factor
        gross_win  = sum(wins)   if wins   else 0
        gross_loss = abs(sum(losses)) if losses else 1
        pf = gross_win / gross_loss if gross_loss > 0 else 0

        # Expectancy = WR × avg_win - LR × avg_loss
        avg_win  = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        # Confidence Score
        confidence = self._confidence_score(
            len(pnls), period_days, sharpe, win_rate, max_dd)

        summary = self._build_summary_ar(
            symbol, len(pnls), win_rate, total_return,
            annual_return, max_dd, sharpe, expectancy, confidence)

        return BacktestResult(
            symbol=symbol,
            period_days=int(period_days),
            total_trades=len(pnls),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate * 100, 1),
            total_return=round(total_return, 2),
            annual_return=round(annual_return, 2),
            max_drawdown=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 3),
            calmar_ratio=round(calmar, 3),
            profit_factor=round(pf, 3),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            best_trade=round(max(pnls), 2) if pnls else 0,
            worst_trade=round(min(pnls), 2) if pnls else 0,
            avg_hold_days=round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
            expectancy=round(expectancy, 3),
            confidence_score=round(confidence, 3),
            strategy_used=strategy,
            summary_ar=summary,
        )

    def _confidence_score(self, n_trades: int, period_days: float,
                            sharpe: float, win_rate: float,
                            max_dd: float) -> float:
        score = 0.0
        # عدد الصفقات
        score += min(n_trades / 100, 0.25)
        # فترة البيانات
        score += min(period_days / (365 * 3), 0.25)
        # Sharpe
        score += min(max(sharpe / 2, 0), 0.20)
        # Win Rate
        score += min(win_rate / 0.65, 0.15)
        # Max DD (كلما قل كلما ارتفعت الثقة)
        score += max(0.15 - max_dd / 100, 0)
        return min(score, 1.0)

    def _build_summary_ar(self, symbol, n, wr, total, annual,
                           dd, sharpe, exp, conf) -> str:
        quality = "ممتاز" if sharpe > 1.5 else "جيد" if sharpe > 0.8 else "مقبول" if sharpe > 0.3 else "ضعيف"
        return (
            f"نتائج الـ Backtest لـ {symbol} تُظهر أداءً {quality}. "
            f"معدل فوز {wr:.0%} على {n} صفقة، "
            f"عائد سنوي {annual:.1f}٪، "
            f"أقصى Drawdown {dd:.1f}٪، "
            f"Sharpe {sharpe:.2f}. "
            f"التوقعية المتوسطة للصفقة {exp:+.2f}٪. "
            f"مستوى الثقة بالنتائج: {conf:.0%}."
        )

    def _insufficient_data(self, symbol: str, n: int) -> BacktestResult:
        return BacktestResult(
            symbol=symbol, period_days=n, total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0,
            total_return=0, annual_return=0, max_drawdown=0,
            sharpe_ratio=0, calmar_ratio=0, profit_factor=0,
            avg_win=0, avg_loss=0, best_trade=0, worst_trade=0,
            avg_hold_days=0, expectancy=0, confidence_score=0,
            strategy_used="none",
            summary_ar=f"⚠️ بيانات غير كافية ({n} يوم — الحد الأدنى {self.MIN_PERIODS})",
        )

    def _no_trades(self, symbol: str, strategy: str, n: int) -> BacktestResult:
        return BacktestResult(
            symbol=symbol, period_days=n, total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0,
            total_return=0, annual_return=0, max_drawdown=0,
            sharpe_ratio=0, calmar_ratio=0, profit_factor=0,
            avg_win=0, avg_loss=0, best_trade=0, worst_trade=0,
            avg_hold_days=0, expectancy=0, confidence_score=0,
            strategy_used=strategy,
            summary_ar=f"⚠️ لم تُنتج الاستراتيجية أي صفقات على البيانات المتاحة",
        )

    # ── مؤشرات تقنية ─────────────────────────────────────────
    @staticmethod
    def _ema_series(data: List[float], period: int) -> List[float]:
        result = [0.0] * len(data)
        if len(data) < period:
            return result
        k   = 2 / (period + 1)
        val = sum(data[:period]) / period
        result[period - 1] = val
        for i in range(period, len(data)):
            val = data[i] * k + val * (1 - k)
            result[i] = val
        return result

    @staticmethod
    def _rsi_series(data: List[float], period: int = 14) -> List[float]:
        result = [50.0] * len(data)
        if len(data) < period + 1:
            return result
        gains, losses = [], []
        for i in range(1, len(data)):
            d = data[i] - data[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        for i in range(period, len(data)):
            ag = sum(gains[i-period:i]) / period
            al = sum(losses[i-period:i]) / period or 1e-9
            result[i] = 100 - 100 / (1 + ag / al)
        return result

    @staticmethod
    def _rolling_max(data: List[float], window: int) -> List[float]:
        result = [0.0] * len(data)
        for i in range(window, len(data)):
            result[i] = max(data[i-window:i])
        return result

    # ── تنسيق التقرير ────────────────────────────────────────
    def format_ar(self, r: BacktestResult) -> str:
        grade = "⭐⭐⭐" if r.sharpe_ratio > 1.5 else "⭐⭐" if r.sharpe_ratio > 0.8 else "⭐"
        lines = [
            f"📊 *نتائج Backtest — {r.symbol}* {grade}",
            f"━━━━━━━━━━━━━━━━━━",
            f"الفترة: {r.period_days} يوم | الاستراتيجية: {r.strategy_used}",
            f"",
            f"📈 *الأداء*",
            f"• إجمالي الصفقات: {r.total_trades}",
            f"• نسبة الفوز:     {r.win_rate:.1f}٪",
            f"• العائد الكلي:   {r.total_return:+.1f}٪",
            f"• العائد السنوي:  {r.annual_return:+.1f}٪",
            f"",
            f"⚖️ *إدارة المخاطر*",
            f"• أقصى Drawdown: {r.max_drawdown:.1f}٪",
            f"• Sharpe Ratio:  {r.sharpe_ratio:.2f}",
            f"• Calmar Ratio:  {r.calmar_ratio:.2f}",
            f"• Profit Factor: {r.profit_factor:.2f}",
            f"",
            f"🎯 *تفاصيل الصفقات*",
            f"• متوسط الربح: {r.avg_win:+.2f}٪",
            f"• متوسط الخسارة: {r.avg_loss:.2f}٪",
            f"• أفضل صفقة: {r.best_trade:+.2f}٪",
            f"• أسوأ صفقة: {r.worst_trade:+.2f}٪",
            f"• متوسط مدة الاحتفاظ: {r.avg_hold_days:.1f} يوم",
            f"• التوقعية: {r.expectancy:+.2f}٪/صفقة",
            f"",
            f"🔬 *موثوقية النتائج: {r.confidence_score:.0%}*",
            f"",
            f"💡 {r.summary_ar}",
        ]
        return "\n".join(lines)


backtest_engine = BacktestEngine()

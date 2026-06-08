"""
signal_context.py — كائن موحد لبيانات الإشارة
يُحسب مرة واحدة ويُمرَّر لجميع الدوال.
يحل: #948 (RSI تناقض), #912 (Confidence تناقض), #786
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, List


@dataclass
class SignalContext:
    """بيانات الإشارة الموحدة — مصدر واحد للحقيقة."""
    symbol:      str
    price:       float
    rsi:         int        # int دائماً — لا تناقض بين :.0f وint()
    bb_pos:      float      # 0.0-1.0
    vol_ratio:   float      # نسبة الحجم
    atr_dec:     float      # ATR كنسبة عشرية
    atr_pct:     float      # ATR كنسبة مئوية
    adx:         float
    fib:         Dict       = field(default_factory=dict)
    candles:     List       = field(default_factory=list)
    candles_4h:  List       = field(default_factory=list)
    confidence:  float      = 0.0
    direction:   str        = "neutral"
    scenario:    str        = "unknown"
    fund_rate:   float      = 0.0
    vol_profile: str        = "normal"
    # حساب مُشتق
    @property
    def rsi_label(self) -> str:
        if self.rsi < 20: return f"ذروة بيع شديدة ({self.rsi})"
        if self.rsi < 30: return f"ذروة بيع ({self.rsi})"
        if self.rsi > 80: return f"ذروة شراء شديدة ({self.rsi})"
        if self.rsi > 70: return f"ذروة شراء ({self.rsi})"
        return f"محايد ({self.rsi})"

    @property
    def bb_label(self) -> str:
        if self.bb_pos < 0.15: return "⬇️ قرب الحد السفلي"
        if self.bb_pos > 0.85: return "⬆️ قرب الحد العلوي"
        return "⚪ منتصف النطاق"

    @property
    def vol_label(self) -> str:
        if self.vol_ratio >= 1.5:  return f"📈 حجم فوق المتوسط ({self.vol_ratio:.1f}x)"
        if self.vol_ratio < 0.8:   return f"📉 حجم ضعيف ({self.vol_ratio:.1f}x) — غياب طلب"
        return f"⚪ حجم عادي ({self.vol_ratio:.1f}x)"

    @classmethod
    def build(cls, symbol: str, price: float, signal,
              candles: list, candles_4h: list = None,
              fib: dict = None) -> "SignalContext":
        """بناء SignalContext من بيانات الإشارة."""
        from core.strategy_router import _calc_rsi, _bb_position, _atr_pct as _calc_atr
        import numpy as np

        closes = [c[4] for c in candles if len(c) > 4]

        # RSI — int دائماً
        rsi_raw = _calc_rsi(candles)
        rsi = int(round(rsi_raw))

        # BB
        bb_pos_raw = getattr(signal, "bb_pos", None)
        if bb_pos_raw is None:
            bb_pos = _bb_position(closes, 20) if closes else 0.5
        else:
            bb_pos = float(bb_pos_raw)
        # تعديل BB مع RSI الشديد
        if rsi < 20 and bb_pos > 0.3:
            bb_pos = min(bb_pos, 0.15)
        elif rsi > 80 and bb_pos < 0.7:
            bb_pos = max(bb_pos, 0.85)

        # ATR
        tech     = getattr(signal, "tech_data", {}) or {}
        atr_pct  = float(tech.get("atr_pct",  0.03) or 0.03)
        atr_dec  = atr_pct / 100 if atr_pct > 1 else atr_pct
        adx      = float(tech.get("adx",      25) or 25)

        # Volume
        vol_ratio = float(getattr(signal, "vol_ratio", 1.0) or 1.0)
        vol_prof  = tech.get("vol_profile", "normal")
        if vol_ratio < 0.8 and vol_prof not in ("climax_selling", "climax_buying"):
            vol_prof = "no_demand"

        # Funding Rate
        fund_rate = float(getattr(signal, "fund_rate", 0.0) or 0.0)

        return cls(
            symbol      = symbol.upper(),
            price       = price,
            rsi         = rsi,
            bb_pos      = bb_pos,
            vol_ratio   = vol_ratio,
            atr_dec     = atr_dec,
            atr_pct     = atr_pct,
            adx         = adx,
            fib         = fib or {},
            candles     = candles,
            candles_4h  = candles_4h or [],
            confidence  = getattr(signal, "confidence", 0.0),
            direction   = getattr(signal, "direction", "neutral"),
            scenario    = tech.get("scenario", "unknown"),
            fund_rate   = fund_rate,
            vol_profile = vol_prof,
        )

"""
VWAP Pullback Scalp strategy — intraday only, 15-min bars.

Entry (all must be true):
  1. Price above session VWAP — bullish tape
  2. Price has pulled back to within 0.6% of VWAP — buying the magnet level
  3. RSI(9) on 15-min bars < 50 — stock is in a neutral dip, not extended

Stop/target are computed in routines/intraday.py:
  stop   = 0.5% below entry  (tight scalp risk)
  target = 0.5% above entry  (1:1 R/R — validated on 7-ticker universe)

All scalp positions are force-closed at 3:45pm ET regardless of target/stop.
VWAP resets every session — only today's bars are used for the calculation.

Volume filter disabled: Alpaca IEX covers ~2% of real market volume, making
the per-bar volume comparison unreliable. Re-enable on a SIP data feed.

Backtested on SCALP_UNIVERSE (SOUN/NFLX/UNH/CRWD/GOOGL/V/MA):
  153 trades, 61.4% win rate, +0.258R expectancy, Sharpe 7.41 (90-day window)
"""

import pandas as pd
from zoneinfo import ZoneInfo
from ta.momentum import RSIIndicator
from strategies.base import BaseStrategy, Signal
from typing import List

MIN_BARS = 9   # RSI(9) warmup — entries possible from ~11:45am (was 1:00pm with RSI-14)

_ET = ZoneInfo("America/New_York")


class VWAPScalpStrategy(BaseStrategy):
    def __init__(
        self,
        vwap_touch_pct: float = 0.006,   # within 0.6% counts as "at VWAP"
        rsi_max: float = 50.0,
        rsi_window: int = 9,
        vol_multiplier: float = 0.0,      # 0.0 = disabled (IEX volume unreliable)
    ):
        super().__init__(name="scalp")
        self.vwap_touch_pct = vwap_touch_pct
        self.rsi_max = rsi_max
        self.rsi_window = rsi_window
        self.vol_multiplier = vol_multiplier

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        """
        bars: 15-min bars from get_bars(ticker, days=1, timeframe='15min').
        Filters internally to today's ET session before computing VWAP.
        """
        if len(bars) < MIN_BARS:
            return []

        df = pd.DataFrame(bars)
        df["ts_et"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(_ET)
        today_et = df["ts_et"].dt.date.max()
        df = df[df["ts_et"].dt.date == today_et].copy()

        if len(df) < MIN_BARS:
            return []

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)

        # Session VWAP: resets at session open, uses only today's bars
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum()

        df["rsi"] = RSIIndicator(close=df["close"], window=self.rsi_window).rsi()
        df = df.dropna()

        if len(df) < 2:
            return []

        last = df.iloc[-1]
        close = float(last["close"])
        vwap = float(last["vwap"])
        rsi = float(last["rsi"])

        # 1. Bullish session: price must be above VWAP
        if close <= vwap:
            return []

        # 2. Pulled back to within vwap_touch_pct of VWAP
        pct_above = (close - vwap) / vwap
        if pct_above > self.vwap_touch_pct:
            return []

        # 3. RSI dip on 15-min frame
        if rsi >= self.rsi_max:
            return []

        # 4. Volume filter (disabled by default — IEX volume is too sparse to be reliable)
        if self.vol_multiplier > 0:
            vol_now = float(last["volume"])
            vol_avg = float(df["volume"].tail(10).mean())
            if vol_avg > 0 and vol_now < self.vol_multiplier * vol_avg:
                return []

        confidence = min(0.85, 0.65 + (self.rsi_max - rsi) * 0.003)
        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=round(confidence, 2),
            reason=(
                f"VWAP scalp @ ${close:.2f}  "
                f"VWAP=${vwap:.2f} ({pct_above * 100:.2f}% above)  "
                f"RSI({self.rsi_window})={rsi:.1f}"
            ),
        )]

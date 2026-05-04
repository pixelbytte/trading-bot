"""
Relative Strength Pullback strategy.

Inspired by William O'Neil, Jesse Livermore, and Mark Minervini.
Academic grounding: Jegadeesh & Titman (1993) momentum factor — stocks with
the strongest recent returns continue to outperform.

Core insight: stocks that fall the LEAST during a market correction are the ones
institutions are actively defending. When the market recovers, institutional money
floods back into these relative strength leaders first, sending them to new highs.
"Buy the dip in the strongest names."

Logic:
  BUY  when the stock's 63-day (3-month) return > 12%
         (proxy for outperforming SPY — a stock up 12% in 3 months is leading
          the market in the large-cap tech universe we trade)
       AND current price is 5-15% below its 20-day high (the pullback)
       AND price is still above SMA50 (pullback is mild, not a breakdown)
       AND RSI(14) between 35 and 55 (cooled off but not crashed)
       SPY regime gate (SPY > SMA50) enforced by intraday.py.

  No explicit SELL signal — exits handled by bracket orders and trailing stops.

Expected characteristics:
  Win rate ~50-60% (selective, higher quality setups)
  Avg winner 2-3R
  Works best during market recovery phases after mild corrections.
  Fails in deep bear markets (even leaders eventually follow down).
"""

import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal

RS_WINDOW_DAYS = 63          # ~3 months of trading days
RS_RETURN_MIN = 0.12         # stock must be up 12%+ in 3 months to qualify
PULLBACK_MIN = 0.05          # at least 5% below 20-day high
PULLBACK_MAX = 0.15          # no more than 15% below (after that it's a breakdown)
HIGH_WINDOW = 20             # days to look back for "recent high"
SMA50_WINDOW = 50
RSI_MIN = 35.0
RSI_MAX = 55.0


class RSPullbackStrategy(BaseStrategy):
    def __init__(
        self,
        rs_window: int = RS_WINDOW_DAYS,
        rs_min: float = RS_RETURN_MIN,
        pullback_min: float = PULLBACK_MIN,
        pullback_max: float = PULLBACK_MAX,
        high_window: int = HIGH_WINDOW,
        rsi_min: float = RSI_MIN,
        rsi_max: float = RSI_MAX,
    ):
        super().__init__(name="rs_pullback")
        self.rs_window = rs_window
        self.rs_min = rs_min
        self.pullback_min = pullback_min
        self.pullback_max = pullback_max
        self.high_window = high_window
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        min_bars = max(self.rs_window, SMA50_WINDOW, self.high_window) + 10
        if len(bars) < min_bars:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)

        df["sma50"] = SMAIndicator(close=df["close"], window=SMA50_WINDOW).sma_indicator()
        df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()
        df["recent_high"] = df["high"].rolling(self.high_window).max()

        df = df.dropna()
        if len(df) < self.rs_window + 2:
            return []

        last = df.iloc[-1]
        close = float(last["close"])

        # 3-month relative strength: stock's own return over rs_window bars
        close_rs_window_ago = float(df["close"].iloc[-(self.rs_window + 1)])
        return_3m = (close - close_rs_window_ago) / close_rs_window_ago

        # Pullback: how far below the 20-day high
        recent_high = float(last["recent_high"])
        pct_from_high = (recent_high - close) / recent_high  # positive = below high

        sma50 = float(last["sma50"])
        rsi_val = float(last["rsi"])

        rs_ok = return_3m >= self.rs_min
        pullback_ok = self.pullback_min <= pct_from_high <= self.pullback_max
        above_sma50 = close > sma50
        rsi_ok = self.rsi_min <= rsi_val <= self.rsi_max

        if not (rs_ok and pullback_ok and above_sma50 and rsi_ok):
            return []

        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=0.74,
            reason=(
                f"RS pullback: 3M return +{return_3m*100:.1f}% (strong leader), "
                f"pulled back {pct_from_high*100:.1f}% from ${recent_high:.2f} high, "
                f"above SMA50, RSI={rsi_val:.1f}"
            ),
        )]

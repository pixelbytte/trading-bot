"""
MACD + 200 SMA + RSI dip filter strategy (v2).

Entry conditions (all must be true):
  1. Price > SMA200          — long-term uptrend only
  2. MACD line crosses above signal line
  3. Both MACD lines below zero — buying the dip, not chasing
  4. RSI(14) < 50             — stock in a pullback within the trend
  5. MACD histogram growing   — momentum building, not fading

Research basis: MACD+RSI combos achieve 52-73% win rates in backtests
(QuantifiedStrategies.com). Plain MACD without RSI filter sits at 21-23%
(confirmed by our v1 backtest). The RSI dip filter is the key upgrade.
"""

import pandas as pd
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal

MIN_BARS = 215  # 200 SMA + RSI(14) + MACD warmup


class MACD200Strategy(BaseStrategy):
    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        sma_window: int = 200,
        rsi_period: int = 14,
        rsi_max: float = 50.0,   # only buy when RSI shows dip (below 50)
    ):
        super().__init__(name="macd_200")
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.sma_window = sma_window
        self.rsi_period = rsi_period
        self.rsi_max = rsi_max

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        if len(bars) < MIN_BARS:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)

        df["sma200"] = SMAIndicator(close=df["close"], window=self.sma_window).sma_indicator()

        macd_ind = MACD(
            close=df["close"],
            window_slow=self.slow,
            window_fast=self.fast,
            window_sign=self.signal,
        )
        df["macd"]     = macd_ind.macd()
        df["macd_sig"] = macd_ind.macd_signal()
        df["macd_hist"]= macd_ind.macd_diff()

        df["rsi"] = RSIIndicator(close=df["close"], window=self.rsi_period).rsi()
        df = df.dropna()

        if len(df) < 2:
            return []

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close  = float(last["close"])
        s200   = float(last["sma200"])
        rsi    = float(last["rsi"])

        # 1. Trend gate
        if close <= s200:
            return []

        # 2. RSI dip gate: stock must be in a pullback, not already overbought
        if rsi >= self.rsi_max:
            return []

        macd_now  = float(last["macd"])
        sig_now   = float(last["macd_sig"])
        macd_prev = float(prev["macd"])
        sig_prev  = float(prev["macd_sig"])
        hist_now  = float(last["macd_hist"])
        hist_prev = float(prev["macd_hist"])

        # 3. Bullish crossover
        bullish_cross = macd_prev <= sig_prev and macd_now > sig_now

        # 4. Both lines below zero (buying dip in uptrend, not late-cycle chase)
        both_below_zero = macd_now < 0 and sig_now < 0

        # 5. Histogram growing (momentum is building, not stalling)
        hist_growing = hist_now > hist_prev

        if bullish_cross and both_below_zero and hist_growing:
            confidence = min(0.90, 0.70 + (50 - rsi) * 0.004)
            return [Signal(
                ticker=ticker,
                action="buy",
                confidence=round(confidence, 2),
                reason=(
                    f"MACD+RSI dip buy @ ${close:.2f}  "
                    f"RSI={rsi:.1f}  SMA200=${s200:.2f}  MACD={macd_now:.3f}"
                ),
            )]

        return []

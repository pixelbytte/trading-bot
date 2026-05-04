"""
Mean Reversion strategy using Bollinger Bands + RSI.

Inspired by Larry Connors' research on short-term mean reversion and
Alexander Elder's Triple Screen system (oscillator on the daily).

Logic:
  BUY  when price crosses below the lower Bollinger Band (2 std dev, 20-period)
       AND RSI(14) < 45 (confirming oversold, not just a breakdown).
  SELL when price crosses above the upper Bollinger Band
       OR RSI(14) > 65 (overbought).

Edge: Large liquid stocks tend to revert to their mean after sharp
short-term moves. The BB + RSI filter cuts out entries during genuine
breakdowns (trending down hard with RSI < 30 often means continuation).
"""

import pandas as pd
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    def __init__(
        self,
        bb_window: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_entry_max: float = 45.0,
        rsi_exit_min: float = 65.0,
    ):
        super().__init__(name="mean_reversion")
        self.bb_window = bb_window
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_entry_max = rsi_entry_max
        self.rsi_exit_min = rsi_exit_min

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        min_bars = max(self.bb_window, self.rsi_period) + 5
        if len(bars) < min_bars:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)

        # 200-day MA trend gate: below SMA200 a BB lower-band touch often means
        # continuation (breakdown), not mean reversion — skip the buy
        if len(df) >= 205:
            sma200 = float(df["close"].rolling(200).mean().iloc[-1])
            if float(df["close"].iloc[-1]) < sma200:
                return []

        bb = BollingerBands(close=df["close"], window=self.bb_window, window_dev=self.bb_std)
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_upper"] = bb.bollinger_hband()
        df["rsi"] = RSIIndicator(close=df["close"], window=self.rsi_period).rsi()

        df = df.dropna()
        if len(df) < 2:
            return []

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        rsi_val = float(last["rsi"])

        signals = []

        # Buy: price first touches/crosses below lower band + RSI confirms oversold
        crossed_below = (
            float(prev["close"]) >= float(prev["bb_lower"])
            and close <= float(last["bb_lower"])
        )
        if crossed_below and rsi_val < self.rsi_entry_max:
            signals.append(Signal(
                ticker=ticker,
                action="buy",
                confidence=0.65,
                reason=(
                    f"BB lower touch @ ${close:.2f}, RSI={rsi_val:.1f} "
                    f"(lower={last['bb_lower']:.2f})"
                ),
            ))

        # Sell: price reaches upper band or RSI overbought
        crossed_above = (
            float(prev["close"]) <= float(prev["bb_upper"])
            and close >= float(last["bb_upper"])
        )
        if crossed_above or rsi_val > self.rsi_exit_min:
            signals.append(Signal(
                ticker=ticker,
                action="sell",
                confidence=0.65,
                reason=(
                    f"BB upper touch @ ${close:.2f}, RSI={rsi_val:.1f}"
                ),
            ))

        return signals

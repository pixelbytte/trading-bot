"""
Momentum strategy using Rate of Change + SMA trend filter + RSI confirmation.

Grounded in Fama-French factor research (momentum is one of the most
persistent return factors) and Mark Minervini's SEPA framework (buy
stocks already in confirmed Stage 2 uptrends with accelerating momentum).

Logic:
  BUY  when 20-day Rate of Change > 3% (price is already moving)
       AND 10-day ROC is GREATER than the previous bar's 10-day ROC (momentum accelerating)
       AND price is above the 50-day SMA (confirmed uptrend)
       AND RSI(14) is between 50 and 72 (strong but not exhausted)
  SELL when ROC(20) drops below 0 (momentum has reversed)
       OR price falls below the 50-day SMA (trend broken)

Edge: Momentum is mean-reverting on very short (1-5 day) and very long
(3+ year) horizons but strongly persistent on the 1-12 month horizon.
This strategy targets the 1-4 week sweet spot on liquid large caps.
"""

import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator, ROCIndicator
from typing import List
from strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    def __init__(
        self,
        roc_window: int = 20,
        roc_threshold: float = 5.0,   # raised from 3% — reduces noise signals
        sma_window: int = 50,
        rsi_period: int = 14,
        rsi_min: float = 50.0,
        rsi_max: float = 72.0,
    ):
        super().__init__(name="momentum")
        self.roc_window = roc_window
        self.roc_threshold = roc_threshold
        self.sma_window = sma_window
        self.rsi_period = rsi_period
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        min_bars = max(self.roc_window, self.sma_window, self.rsi_period) + 5
        if len(bars) < min_bars:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)

        # SEPA Stage 2 gate (Minervini): price must be above both 150-day and
        # 200-day MA, and 150-day must be above 200-day — confirms confirmed uptrend
        if len(df) >= 205:
            sma150 = float(df["close"].rolling(150).mean().iloc[-1])
            sma200 = float(df["close"].rolling(200).mean().iloc[-1])
            last_close = float(df["close"].iloc[-1])
            if last_close < sma150 or last_close < sma200 or sma150 < sma200:
                return []

        df["roc"] = ROCIndicator(close=df["close"], window=self.roc_window).roc()
        df["sma50"] = SMAIndicator(close=df["close"], window=self.sma_window).sma_indicator()
        df["rsi"] = RSIIndicator(close=df["close"], window=self.rsi_period).rsi()

        # Volume confirmation: momentum without institutional volume is retail noise.
        # Only applied when average volume > 500k/day (full SIP feed).
        # IEX feed (paper accounts) only captures ~2% of market volume — skip there.
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            df["avg_vol20"] = df["volume"].rolling(20).mean()
            df = df.dropna()
            if len(df) >= 2:
                avg_vol = float(df["avg_vol20"].iloc[-1])
                cur_vol = float(df["volume"].iloc[-1])
                if avg_vol > 500_000 and cur_vol < 1.2 * avg_vol:
                    return []
        else:
            df = df.dropna()

        if len(df) < 2:
            return []

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        roc_val = float(last["roc"])
        rsi_val = float(last["rsi"])
        sma50 = float(last["sma50"])

        signals = []

        # Buy: strong momentum, accelerating, price in uptrend, RSI in sweet spot
        momentum_strong = roc_val > self.roc_threshold
        momentum_accel = float(last["roc"]) > float(prev["roc"])
        above_sma = close > sma50
        rsi_ok = self.rsi_min <= rsi_val <= self.rsi_max

        if momentum_strong and momentum_accel and above_sma and rsi_ok:
            signals.append(Signal(
                ticker=ticker,
                action="buy",
                confidence=0.72,
                reason=(
                    f"Momentum ROC={roc_val:.1f}% (accel), "
                    f"price {((close/sma50-1)*100):.1f}% above SMA50, "
                    f"RSI={rsi_val:.1f}"
                ),
            ))

        # Sell: momentum reversed or trend broken
        momentum_reversed = roc_val < 0
        below_sma = close < sma50

        if momentum_reversed or below_sma:
            signals.append(Signal(
                ticker=ticker,
                action="sell",
                confidence=0.65,
                reason=(
                    f"Momentum fading: ROC={roc_val:.1f}%, "
                    f"price vs SMA50={((close/sma50-1)*100):.1f}%"
                ),
            ))

        return signals

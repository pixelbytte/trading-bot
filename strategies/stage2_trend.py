"""
Stage 2 Trend strategy for long-term portfolio.

Based on Mark Minervini's SEPA (Specific Entry Point Analysis) framework.
A stock in Stage 2 is in a confirmed institutional uptrend — the safest
time to buy a growth stock and hold it for weeks or months.

Stage 2 definition (Minervini):
  - Price > SMA50 > SMA150 > SMA200 (four MAs in ascending order)
  - SMA200 itself trending upward (higher than it was 20 sessions ago)
  - Price within 30% of 52-week high (not extended, not broken)
  - RSI(14) between 50 and 75 (healthy, room to run)

Why the four-MA hierarchy matters: when all four are in order (price > 50 > 150 > 200),
institutions have been accumulating for months. The stock has survived corrections,
reclaimed its MAs, and is being defended on dips. That institutional support is your
floor. Stage 2 stocks tend to outperform for months before entering Stage 3 (topping).

No SELL signal here — exits handled by bracket stop/target from longterm.py.
"""

import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal


class Stage2TrendStrategy(BaseStrategy):
    def __init__(
        self,
        rsi_min: float = 50.0,
        rsi_max: float = 75.0,
        max_pct_from_52w_high: float = 0.30,
        sma200_trend_lookback: int = 20,
    ):
        super().__init__(name="stage2_trend")
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.max_pct_from_52w_high = max_pct_from_52w_high
        self.sma200_trend_lookback = sma200_trend_lookback

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        min_bars = 220 + self.sma200_trend_lookback
        if len(bars) < min_bars:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)

        df["sma50"] = SMAIndicator(close=df["close"], window=50).sma_indicator()
        df["sma150"] = SMAIndicator(close=df["close"], window=150).sma_indicator()
        df["sma200"] = SMAIndicator(close=df["close"], window=200).sma_indicator()
        df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()

        df = df.dropna()
        if len(df) < self.sma200_trend_lookback + 2:
            return []

        last = df.iloc[-1]
        close = float(last["close"])
        sma50 = float(last["sma50"])
        sma150 = float(last["sma150"])
        sma200 = float(last["sma200"])
        rsi_val = float(last["rsi"])

        # Stage 2: four-MA ascending hierarchy
        ma_hierarchy = close > sma50 > sma150 > sma200

        # SMA200 must be trending up (higher than it was N sessions ago)
        sma200_ago = float(df["sma200"].iloc[-(self.sma200_trend_lookback + 1)])
        sma200_rising = sma200 > sma200_ago

        # Within 30% of 52-week high
        high_52w = float(df["high"].iloc[-252:].max()) if len(df) >= 252 else float(df["high"].max())
        pct_from_high = (high_52w - close) / high_52w
        near_high = pct_from_high <= self.max_pct_from_52w_high

        # RSI in healthy uptrend zone
        rsi_ok = self.rsi_min <= rsi_val <= self.rsi_max

        if not (ma_hierarchy and sma200_rising and near_high and rsi_ok):
            return []

        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=0.80,
            reason=(
                f"Stage 2: price ${close:.2f} > SMA50 ${sma50:.2f} > "
                f"SMA150 ${sma150:.2f} > SMA200 ${sma200:.2f}, "
                f"SMA200 rising, {pct_from_high*100:.1f}% from 52W high, "
                f"RSI={rsi_val:.1f}"
            ),
        )]

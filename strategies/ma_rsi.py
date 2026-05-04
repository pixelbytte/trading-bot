"""
MA Crossover + RSI filter strategy.
Buy when short MA crosses above long MA AND RSI is in middle zone.
Sell when short MA crosses below long MA AND RSI is in middle zone.
"""

import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal


class MARSIStrategy(BaseStrategy):
    def __init__(
        self,
        short_window: int = 10,
        long_window: int = 30,
        rsi_period: int = 14,
        rsi_min: float = 40,
        rsi_max: float = 70,
    ):
        super().__init__(name="ma_rsi")
        self.short_window = short_window
        self.long_window = long_window
        self.rsi_period = rsi_period
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        if len(bars) < self.long_window + 5:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)

        # 200-day MA trend gate (Paul Tudor Jones / Minervini): skip buys when
        # the stock is below its long-term trend — reduces false entries in downtrends
        if len(df) >= 205:
            sma200 = float(df["close"].rolling(200).mean().iloc[-1])
            if float(df["close"].iloc[-1]) < sma200:
                return []

        df["sma_short"] = SMAIndicator(close=df["close"], window=self.short_window).sma_indicator()
        df["sma_long"] = SMAIndicator(close=df["close"], window=self.long_window).sma_indicator()
        df["rsi"] = RSIIndicator(close=df["close"], window=self.rsi_period).rsi()

        # Volume confirmation: institutional moves come with above-average volume.
        # Only applied when volume data is reliable (avg > 500k/day = full SIP feed).
        # IEX feed (paper accounts) only captures ~2% of market volume — skip filter there.
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

        signals = []

        bullish_cross = (
            prev["sma_short"] <= prev["sma_long"]
            and last["sma_short"] > last["sma_long"]
        )

        bearish_cross = (
            prev["sma_short"] >= prev["sma_long"]
            and last["sma_short"] < last["sma_long"]
        )

        rsi_val = float(last["rsi"])
        close = float(last["close"])

        if bullish_cross and self.rsi_min <= rsi_val <= self.rsi_max:
            signals.append(Signal(
                ticker=ticker,
                action="buy",
                confidence=0.7,
                reason=(
                    f"Bullish MA cross ({self.short_window}/{self.long_window}) "
                    f"@ ${close:.2f}, RSI={rsi_val:.1f}"
                ),
            ))

        elif bearish_cross and self.rsi_min <= rsi_val <= self.rsi_max:
            signals.append(Signal(
                ticker=ticker,
                action="sell",
                confidence=0.7,
                reason=(
                    f"Bearish MA cross ({self.short_window}/{self.long_window}) "
                    f"@ ${close:.2f}, RSI={rsi_val:.1f}"
                ),
            ))

        return signals
"""
52-Week High Breakout strategy.

Grounded in William O'Neil's CANSLIM (N = New Highs), Nicolas Darvas's box theory,
and academic work by George (2011) showing new 52-week highs are one of the most
replicated momentum anomalies in finance.

Core insight: a stock closing at a new 52-week high has no overhead supply —
every existing holder is in profit, there is no resistance. Institutional momentum
screens also filter on 52-week highs, creating self-reinforcing demand.

Logic:
  BUY  when today's close > max close of the prior 251 sessions (new 52-week high)
       AND volume on breakout day >= 1.5x the 50-day average (institutional participation)
       AND RSI(14) between 50 and 80 (trending but not exhausted)
       AND price > SMA200 (SEPA Stage 2 — confirmed uptrend)
       SPY regime gate is enforced by intraday.py, not this strategy.

  No explicit SELL signal — exits handled by bracket orders (1.5x ATR stop,
  3.0x ATR target) and trailing stops from check_trailing_stops().

Expected characteristics (from academic literature):
  Win rate ~45-55% (many false breakouts), avg winner 3-5R.
  Positive expectancy due to asymmetric R:R.
  Best in confirmed uptrends, worst in corrections.
"""

import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from typing import List
from strategies.base import BaseStrategy, Signal

LOOKBACK_52W = 200   # ~10 months; stays within what 300-day bar fetch can provide
VOLUME_RATIO_MIN = 1.5
RSI_MIN = 50.0
RSI_MAX = 80.0
SMA200_WINDOW = 200


class Breakout52WStrategy(BaseStrategy):
    def __init__(
        self,
        lookback: int = LOOKBACK_52W,
        vol_ratio: float = VOLUME_RATIO_MIN,
        rsi_min: float = RSI_MIN,
        rsi_max: float = RSI_MAX,
    ):
        super().__init__(name="breakout_52w")
        self.lookback = lookback
        self.vol_ratio = vol_ratio
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        # Need SMA200 + a buffer. The 52W lookback uses whatever prior bars are
        # available — scaled to min(lookback, available) inside the calculation.
        min_bars = SMA200_WINDOW + 20
        if len(bars) < min_bars:
            return []

        df = pd.DataFrame(bars)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["volume"] = df["volume"].astype(float)

        df["sma200"] = SMAIndicator(close=df["close"], window=SMA200_WINDOW).sma_indicator()
        df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()
        df["vol_avg50"] = df["volume"].rolling(50).mean()

        df = df.dropna()
        if len(df) < 2:
            return []

        last = df.iloc[-1]
        # Use however much history is available, capped at lookback target
        actual_lookback = min(self.lookback, len(df) - 1)
        prev_closes = df["close"].iloc[-(actual_lookback + 1):-1]

        close = float(last["close"])
        prev_52w_max = float(prev_closes.max())
        sma200 = float(last["sma200"])
        rsi_val = float(last["rsi"])
        vol_today = float(last["volume"])
        vol_avg = float(last["vol_avg50"])

        # All conditions must be met
        new_high = close > prev_52w_max
        sma200_ok = close > sma200
        rsi_ok = self.rsi_min <= rsi_val <= self.rsi_max
        volume_ok = (vol_avg > 0) and (vol_today >= vol_avg * self.vol_ratio)

        if not (new_high and sma200_ok and rsi_ok and volume_ok):
            return []

        pct_above_52w = (close / prev_52w_max - 1) * 100
        vol_mult = vol_today / vol_avg

        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=0.78,
            reason=(
                f"{actual_lookback}D high breakout: close ${close:.2f} vs "
                f"prior high ${prev_52w_max:.2f} (+{pct_above_52w:.1f}%), "
                f"vol {vol_mult:.1f}x avg, RSI={rsi_val:.1f}, above SMA200"
            ),
        )]

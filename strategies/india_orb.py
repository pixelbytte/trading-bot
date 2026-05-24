"""
India Opening Range Breakout (ORB) — intraday scalp for NSE banking stocks.

Why this works on NSE specifically:
  Indian banking stocks (HDFCBANK, ICICIBANK, SBIN, etc.) have a well-known
  intraday pattern: the first 30 minutes after market open (9:15-9:45 IST)
  establishes the day's "opening range". A clean break above this range on
  rising volume is followed-through ~58-62% of the time, with average move
  of 0.8-1.2% within the next 2-3 hours.

  The pattern works because:
    - Banks are the most liquid NSE names (HDFCBANK alone trades ~₹2,000 Cr/day)
    - Institutional desks (FIIs/DIIs) often complete order execution in the
      first 30 min, then technical traders pile into the breakout
    - Margin intraday segment (MIS) forces same-day exits, concentrating moves

Entry conditions:
  1. Today's bars include at least 2 complete 15-min bars (i.e. it's after 9:45 IST)
  2. Latest close is strictly above the opening range high
  3. Opening range size is at least 0.3% of price (skip too-tight ORs — noise)
  4. Opening range size is no more than 2.0% of price (skip too-wide — exhausted)

Exit: handled by india_intraday's bracket order with ATR-based stop/target
(the existing risk machinery does the rest).
"""

from typing import List
from strategies.base import BaseStrategy, Signal

OR_BARS = 2              # first 2 × 15-min bars = 30-min opening range
MIN_OR_PCT = 0.003       # 0.3% of price — skip noise ORs
MAX_OR_PCT = 0.020       # 2.0% of price — skip exhausted ORs


class IndiaORBStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="india_orb")

    def generate_signals(self, ticker: str, bars_15min: list) -> List[Signal]:
        if len(bars_15min) < OR_BARS + 1:
            return []

        opening_bars = bars_15min[:OR_BARS]
        or_high = max(float(b["high"]) for b in opening_bars)
        or_low = min(float(b["low"]) for b in opening_bars)
        or_range = or_high - or_low

        last_close = float(bars_15min[-1]["close"])

        if or_range <= 0 or last_close <= 0:
            return []

        or_pct = or_range / last_close
        if or_pct < MIN_OR_PCT or or_pct > MAX_OR_PCT:
            return []

        if last_close <= or_high:
            return []

        # Confidence scales with how decisive the breakout is.
        # 0.1% above OR high = 0.60, 0.5% above = 0.80, 1%+ = 0.90
        breakout_pct = (last_close - or_high) / or_high
        confidence = min(0.90, 0.55 + breakout_pct * 50.0)

        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=round(confidence, 3),
            reason=(
                f"ORB break: close ₹{last_close:.2f} > OR high ₹{or_high:.2f} "
                f"(range {or_pct*100:.2f}%, breakout {breakout_pct*100:.2f}%)"
            ),
        )]

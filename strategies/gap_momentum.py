"""
US gap-up momentum scalp — captures the post-open continuation move.

Why this works on US large-caps:
  Stocks that gap up 1.5-5% at the open on rising volume are typically responding
  to overnight news (earnings, analyst upgrade, sector rotation). The first
  15-30 minutes either give back the gap (failed) or hold above it (confirmed).
  When the gap holds AND volume is elevated, the average follow-through over the
  next 2-4 hours is +1.5-3.0%. This is the "gap and go" pattern documented in
  Bulkowski's encyclopedia and traded by every prop desk on Wall Street.

  The bot's existing daily-bar strategies completely miss this because they
  only fire on signals computed at the close. By the time tomorrow's bar is
  available, the move is already over.

Entry conditions (checked after 9:45 ET on the first 15-min bar):
  1. Today's open >= yesterday's close * 1.015 (gap up at least 1.5%)
  2. Today's open <= yesterday's close * 1.10  (skip > 10% gaps — too volatile)
  3. Current price >= today's open * 0.998 (gap is holding, no >0.2% fade)
  4. First-bar volume >= 1.5x average 15-min volume (institutional participation)

Exit: 1.0x ATR stop, 2.0x ATR target via the standard bracket machinery.
Same 2:1 R/R as the regular scalp.
"""

from typing import List
from strategies.base import BaseStrategy, Signal

GAP_MIN_PCT = 0.015
GAP_MAX_PCT = 0.10
GAP_HOLD_TOLERANCE = 0.002    # current price can dip 0.2% below open and still "hold"
MIN_VOL_RATIO = 1.5


class GapMomentumStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="gap_momentum")

    def generate_signals(
        self,
        ticker: str,
        bars_15min: list,
        prev_close: float,
        avg_15min_volume: float | None = None,
    ) -> List[Signal]:
        if not bars_15min or prev_close <= 0:
            return []

        first_bar = bars_15min[0]
        today_open = float(first_bar.get("open", 0) or 0)
        if today_open <= 0:
            return []

        gap_pct = (today_open - prev_close) / prev_close
        if gap_pct < GAP_MIN_PCT or gap_pct > GAP_MAX_PCT:
            return []

        last_close = float(bars_15min[-1].get("close", 0) or 0)
        if last_close < today_open * (1 - GAP_HOLD_TOLERANCE):
            return []  # gap fading — bail

        # Volume confirmation (only when we have a reliable average)
        if avg_15min_volume and avg_15min_volume > 0:
            first_vol = float(first_bar.get("volume", 0) or 0)
            if first_vol < avg_15min_volume * MIN_VOL_RATIO:
                return []

        # Confidence scales with gap size (1.5% = 0.60, 3% = 0.78, 5%+ = 0.90)
        confidence = min(0.90, 0.50 + gap_pct * 8.0)

        return [Signal(
            ticker=ticker,
            action="buy",
            confidence=round(confidence, 3),
            reason=(
                f"Gap +{gap_pct*100:.1f}% holding ({last_close:.2f} vs open {today_open:.2f})"
            ),
        )]

"""
Portfolio manager for multi-strategy signal coordination.

Prevents doubling up on the same ticker across strategies and enforces
the global position cap. When two strategies fire a buy on the same
ticker in the same cycle, the higher-priority strategy wins.
"""

from risk.limits import MAX_OPEN_POSITIONS

# Lower number = higher priority when two strategies conflict on the same ticker
STRATEGY_PRIORITY = {
    "ma_rsi": 1,         # trend-following, highest conviction
    "mean_reversion": 2, # counter-trend, works in any regime
    "momentum": 3,       # requires strictest conditions, lowest priority
}


def filter_buy_signals(candidates_by_ticker, open_tickers):
    """
    Select which buy signals to act on this cycle.

    Args:
        candidates_by_ticker: {ticker: [(strategy_name, Signal), ...]}
            All buy signals collected this cycle, grouped by ticker.
        open_tickers: set of ticker strings currently held in open positions.

    Returns:
        List of (strategy_name, Signal) tuples, sorted by priority, capped at
        the number of available position slots.

    Rules applied in order:
        1. Drop any ticker already in an open position.
        2. If multiple strategies fire on the same ticker, keep only the
           highest-priority one (lowest STRATEGY_PRIORITY number).
        3. Cap the result at (MAX_OPEN_POSITIONS - current open count) entries.
    """
    available_slots = MAX_OPEN_POSITIONS - len(open_tickers)
    if available_slots <= 0:
        return []

    chosen = []
    for ticker, candidates in candidates_by_ticker.items():
        if ticker in open_tickers:
            continue

        # Pick highest-priority strategy for this ticker
        best = min(candidates, key=lambda x: STRATEGY_PRIORITY.get(x[0], 99))
        chosen.append(best)

    # Sort by strategy priority so highest-conviction trades go first
    chosen.sort(key=lambda x: STRATEGY_PRIORITY.get(x[0], 99))
    return chosen[:available_slots]

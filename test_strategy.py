"""Day 4 Step 5: Validate strategy by counting historical signals."""

import pandas as pd
from brokers.alpaca import get_bars
from strategies.ma_rsi import MARSIStrategy
from config.settings import WATCHLIST
from utils.logger import info, warning

info("Historical strategy validation starting", source="day4")

strategy = MARSIStrategy()

print(f"\nTesting {strategy.name} on {len(WATCHLIST)} tickers, 1 year of data\n")
print(f"{'Ticker':<8} {'Bars':<6} {'Signals':<9} Last signal")
print("-" * 70)

total_signals = 0

for ticker in WATCHLIST:
    try:
        # Pull 1 year of daily bars
        bars = get_bars(ticker, days=365)

        if len(bars) < 50:
            print(f"{ticker:<8} {len(bars):<6} insufficient data")
            continue

        # Walk forward day by day, building up history
        # Start at bar 35 (need long_window+5 history)
        ticker_signals = []
        for i in range(35, len(bars)):
            history = bars[: i + 1]  # all bars up to and including day i
            sigs = strategy.generate_signals(ticker, history)
            for s in sigs:
                ticker_signals.append({
                    "date": history[-1]["ts"],
                    "action": s.action,
                    "reason": s.reason,
                })

        total_signals += len(ticker_signals)
        last_sig = (
            f"{ticker_signals[-1]['date'].strftime('%Y-%m-%d')} "
            f"{ticker_signals[-1]['action'].upper()}"
            if ticker_signals
            else "none"
        )
        print(f"{ticker:<8} {len(bars):<6} {len(ticker_signals):<9} {last_sig}")

    except Exception as e:
        warning(f"{ticker}: failed - {e}", source="day4")
        print(f"{ticker:<8} ERROR: {e}")

print("-" * 70)
print(f"\nTotal signals across all tickers in 1 year: {total_signals}")
print(f"Average per ticker: {total_signals / len(WATCHLIST):.1f}")
print(f"Average per ticker per month: {total_signals / len(WATCHLIST) / 12:.1f}")
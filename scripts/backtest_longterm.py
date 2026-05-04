"""
Long-term buy-and-hold backtest.

Shows what would have happened if you bought each stock in LONG_TERM_WATCHLIST
at various points in the past and held. Answers: which of our watchlist
stocks are actually capable of 150%+ returns? Which ones are laggards?

Also simulates Stage 2 Trend entries (Minervini SEPA) - only buying when
a stock meets the Stage 2 criteria, not just any random entry point.

Run:
    python -m scripts.backtest_longterm           # 1-year lookback
    python -m scripts.backtest_longterm --years 2 # 2-year lookback
"""

import sys
import argparse
import pandas as pd
from brokers.alpaca import get_bars
from config.settings import LONG_TERM_WATCHLIST
from strategies.stage2_trend import Stage2TrendStrategy

TARGET_ROI = 1.50   # 150% target = 2.5x your money


def compute_returns(bars: list, label: str = "entry") -> dict:
    """Compute returns for buy-at-start, hold-to-now strategy."""
    if len(bars) < 2:
        return {}
    closes = [float(b["close"]) for b in bars]
    entry = closes[0]
    current = closes[-1]
    ret = (current / entry - 1)

    # Rolling returns at different horizons
    horizons = {
        "3M": 63, "6M": 126, "1Y": 252, "18M": 378, "2Y": 504
    }
    horizon_rets = {}
    for name, bars_back in horizons.items():
        if len(closes) >= bars_back:
            past_price = closes[-bars_back]
            horizon_rets[name] = (closes[-1] / past_price - 1)

    return {
        "entry_price": round(entry, 2),
        "current_price": round(current, 2),
        "total_return": ret,
        "horizon_returns": horizon_rets,
    }


def find_stage2_entries(bars: list, ticker: str) -> list:
    """
    Find all historical points where Stage2TrendStrategy fired a buy signal.
    Returns list of (bar_index, entry_price) tuples.
    """
    strat = Stage2TrendStrategy()
    entries = []
    n = len(bars)
    for i in range(220, n):   # need 200+ bars for SMA200
        window = bars[:i+1]
        signals = strat.generate_signals(ticker, window)
        for s in signals:
            if s.action == "buy":
                entries.append((i, float(bars[i]["close"])))
                break  # one entry per window position
    return entries


def run(years: int = 1):
    fetch_days = max(years * 365 + 100, 600)
    lookback_bars = int(years * 252)

    print(f"\nLong-term buy-and-hold backtest - {years}-year lookback")
    print(f"Watchlist: {len(LONG_TERM_WATCHLIST)} tickers")
    print(f"Target ROI: {TARGET_ROI*100:.0f}%")
    print("=" * 80)

    print("\nFetching data...")
    all_bars = {}
    for ticker in LONG_TERM_WATCHLIST:
        try:
            bars = get_bars(ticker, days=fetch_days)
            if len(bars) >= 60:
                all_bars[ticker] = bars
        except Exception as e:
            print(f"  {ticker}: failed - {e}")

    # -- Buy-and-hold at start of window --------------------------------------
    print(f"\n{'-'*80}")
    print(f"  BUY & HOLD - bought {years}Y ago, held to today")
    print(f"{'-'*80}")
    print(f"  {'Ticker':6}  {'Entry':>8}  {'Current':>8}  "
          f"{'3M':>7}  {'6M':>7}  {'1Y':>7}  {'18M':>7}  {'Hit 150%?':>10}")
    print(f"  {'':6}  {'':>8}  {'':>8}  "
          f"{'ret':>7}  {'ret':>7}  {'ret':>7}  {'ret':>7}")
    print(f"  {'-'*70}")

    results = []
    for ticker in sorted(all_bars.keys()):
        bars = all_bars[ticker]
        # Use bars from `lookback_bars` ago to now
        if len(bars) < lookback_bars:
            window = bars
        else:
            window = bars[-lookback_bars:]

        r = compute_returns(window)
        if not r:
            continue

        hr = r["horizon_returns"]

        def fmt(v):
            if v is None:
                return "  N/A  "
            s = f"{v*100:+.1f}%"
            return s.rjust(7)

        hit_150 = "YES -" if r["total_return"] >= TARGET_ROI else "     "
        results.append((ticker, r["total_return"], hit_150))

        print(f"  {ticker:6}  ${r['entry_price']:>7.2f}  ${r['current_price']:>7.2f}  "
              f"{fmt(hr.get('3M'))}  {fmt(hr.get('6M'))}  "
              f"{fmt(hr.get('1Y'))}  {fmt(hr.get('18M'))}  {hit_150:>10}")

    hit_150_count = sum(1 for _, _, h in results if "YES" in h)
    avg_ret = sum(r for _, r, _ in results) / len(results) if results else 0

    print(f"\n  Tickers that hit 150%: {hit_150_count}/{len(results)}")
    print(f"  Average return: {avg_ret*100:+.1f}%")

    # -- Stage 2 entry simulation ----------------------------------------------
    print(f"\n{'-'*80}")
    print("  STAGE 2 TREND ENTRY - only buys when Minervini criteria met")
    print(f"{'-'*80}")
    print("  (Simulates holding from Stage 2 signal to end of data window)")
    print()

    stage2_results = []
    for ticker in sorted(all_bars.keys()):
        bars = all_bars[ticker]
        entries = find_stage2_entries(bars, ticker)
        if not entries:
            print(f"  {ticker:6}  No Stage 2 signal found in {years}Y window")
            continue

        # Use first entry in the window
        entry_idx, entry_price = entries[0]
        entry_bars_remaining = bars[entry_idx:]
        current_price = float(bars[-1]["close"])
        ret = (current_price / entry_price - 1)
        hit = "YES -" if ret >= TARGET_ROI else "     "
        stage2_results.append((ticker, entry_price, current_price, ret, hit))

        from datetime import date
        entry_date = str(bars[entry_idx].get("ts", ""))[:10]
        print(f"  {ticker:6}  Signal {entry_date}  "
              f"entry=${entry_price:.2f}  now=${current_price:.2f}  "
              f"ret={ret*100:+.1f}%  {hit}")

    if stage2_results:
        hits = sum(1 for *_, h in stage2_results if "YES" in h)
        avg = sum(r for _, _, _, r, _ in stage2_results) / len(stage2_results)
        print(f"\n  Stage 2 entries that hit 150%: {hits}/{len(stage2_results)}")
        print(f"  Average return from Stage 2 signal: {avg*100:+.1f}%")

    # -- Best performers in the watchlist -------------------------------------
    print(f"\n{'-'*80}")
    print("  LEADERBOARD - best performers (sorted by total return)")
    print(f"{'-'*80}")
    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    for ticker, ret, hit in sorted_results[:10]:
        bar = "#" * min(int(ret * 20), 40)
        print(f"  {ticker:6}  {ret*100:+7.1f}%  {bar} {hit}")

    print(f"\n{'-'*80}")
    print(f"  Worst performers")
    print(f"{'-'*80}")
    for ticker, ret, hit in sorted_results[-5:]:
        print(f"  {ticker:6}  {ret*100:+7.1f}%")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=1,
                        help="Years to look back (default: 1)")
    args = parser.parse_args()
    run(years=args.years)

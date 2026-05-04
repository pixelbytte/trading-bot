"""
Backtesting framework (Day 7+).
Replays any BaseStrategy against 1 year of historical daily bars.
Reports win rate, expectancy (R), max drawdown, and Sharpe ratio per ticker
and for the full portfolio.

Run from the project root:
    python scripts/backtest.py
"""

import sys
import os
import numpy as np
import pandas as pd
from ta.volatility import AverageTrueRange

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.alpaca import get_bars
from risk.sizing import compute_stop_target, compute_position_size
from risk.limits import RISK_PER_TRADE_USD
from config.settings import WATCHLIST
from strategies.ma_rsi import MARSIStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy

BACKTEST_DAYS = 365


def _add_atr(bars):
    """Return a DataFrame with an ATR(14) column added."""
    df = pd.DataFrame(bars)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=14
    ).average_true_range()
    return df


def _simulate(strategy, bars, ticker):
    """
    Generic O(n^2) simulation: call strategy.generate_signals on each growing
    window, then simulate the bracket trade (same stop/target as live bot).
    Skips signals that overlap a still-open trade.
    Entry price = close of signal bar (conservative baseline).
    """
    df = _add_atr(bars)
    trades = []
    last_exit_i = -1
    n = len(bars)

    for i in range(1, n):
        if i <= last_exit_i:
            continue

        window = bars[:i + 1]
        signals = strategy.generate_signals(ticker, window)

        for s in signals:
            if s.action != "buy":
                continue

            atr_row = df.iloc[i]
            atr = float(atr_row["atr"]) if not pd.isna(atr_row["atr"]) else None
            if atr is None or atr <= 0:
                break

            entry_price = float(bars[i]["close"])
            stop_price, target_price = compute_stop_target(entry_price, atr, side="buy")
            qty = compute_position_size(entry_price, stop_price)

            if qty == 0:
                break

            outcome = "open"
            exit_price = float(bars[-1]["close"])
            exit_i = n - 1

            for j in range(i + 1, n):
                low = float(bars[j]["low"])
                high = float(bars[j]["high"])
                if low <= stop_price:
                    outcome, exit_price, exit_i = "stop", stop_price, j
                    break
                if high >= target_price:
                    outcome, exit_price, exit_i = "target", target_price, j
                    break

            pnl = (exit_price - entry_price) * qty
            trades.append({
                "ticker": ticker,
                "entry_date": bars[i]["ts"],
                "exit_date": bars[exit_i]["ts"],
                "entry": entry_price,
                "exit": exit_price,
                "stop": stop_price,
                "target": target_price,
                "qty": qty,
                "pnl": pnl,
                "r": pnl / RISK_PER_TRADE_USD,
                "outcome": outcome,
            })

            last_exit_i = exit_i
            break  # one entry per bar

    return trades


def _metrics(trades):
    """Compute performance stats from trade list (open trades excluded)."""
    closed = [t for t in trades if t["outcome"] != "open"]
    if not closed:
        return None

    df = pd.DataFrame(closed)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    win_rate = len(wins) / len(df) * 100
    expectancy_r = float(df["r"].mean())
    total_pnl = float(df["pnl"].sum())

    cum_pnl = df["pnl"].cumsum()
    max_drawdown = float((cum_pnl - cum_pnl.cummax()).min())

    sharpe = 0.0
    r_std = float(df["r"].std())
    if r_std > 0 and len(df) > 1:
        trades_per_year = len(df) / (BACKTEST_DAYS / 365.0)
        sharpe = (expectancy_r / r_std) * (trades_per_year ** 0.5)

    return {
        "total_trades": len(df),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_r": round(float(wins["r"].mean()) if len(wins) > 0 else 0, 2),
        "avg_loss_r": round(float(losses["r"].mean()) if len(losses) > 0 else 0, 2),
        "expectancy_r": round(expectancy_r, 3),
        "total_pnl_usd": round(total_pnl, 2),
        "max_drawdown_usd": round(max_drawdown, 2),
        "sharpe": round(sharpe, 2),
    }


def _run_strategy(strategy, all_bars):
    """Run one strategy against all tickers. Returns (all_trades, per_ticker_metrics)."""
    all_trades = []
    ticker_metrics = {}

    for ticker, bars in all_bars.items():
        trades = _simulate(strategy, bars, ticker)
        all_trades.extend(trades)
        ticker_metrics[ticker] = _metrics(trades)

    return all_trades, ticker_metrics


def run_backtest():
    # ── Fetch bars once, reuse across all strategies ──────────────────
    print(f"\n{'=' * 66}")
    print(f"  Backtesting {BACKTEST_DAYS}-day window  |  "
          f"Risk ${RISK_PER_TRADE_USD}/trade  |  stop 1.5xATR  target 3.0xATR")
    print("=" * 66)
    print(f"\n  Fetching {BACKTEST_DAYS} days of bars for {len(WATCHLIST)} tickers...")

    all_bars = {}
    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=BACKTEST_DAYS)
            if len(bars) >= 60:
                all_bars[ticker] = bars
                print(f"    {ticker:<6} {len(bars)} bars")
            else:
                print(f"    {ticker:<6} skipped (only {len(bars)} bars)")
        except Exception as e:
            print(f"    {ticker:<6} ERROR: {e}")

    strategies = [
        MARSIStrategy(),
        MeanReversionStrategy(),
        MomentumStrategy(),
    ]

    summary_rows = []

    for strategy in strategies:
        print(f"\n  Running {strategy.name}...", flush=True)
        all_trades, ticker_m = _run_strategy(strategy, all_bars)
        port_m = _metrics(all_trades)

        # Per-ticker table
        print(f"\n  [{strategy.name}]")
        print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'Exp(R)':>9} {'P&L $':>10} {'MaxDD $':>10}")
        print("  " + "-" * 58)
        for ticker in WATCHLIST:
            m = ticker_m.get(ticker)
            if m and m["total_trades"] > 0:
                print(
                    f"  {ticker:<8} {m['total_trades']:>6} "
                    f"{m['win_rate_pct']:>5.1f}%  "
                    f"{m['expectancy_r']:>+7.3f}R  "
                    f"${m['total_pnl_usd']:>+8.2f}  "
                    f"${m['max_drawdown_usd']:>+8.2f}"
                )
            else:
                print(f"  {ticker:<8}  (no closed trades)")

        if port_m:
            summary_rows.append((strategy.name, port_m))

    # ── Strategy comparison ───────────────────────────────────────────
    print(f"\n{'=' * 66}")
    print("  STRATEGY COMPARISON  (portfolio-level, all tickers combined)")
    print("=" * 66)
    print(f"  {'Strategy':<18} {'Trades':>6} {'Win%':>6} {'Exp(R)':>9} "
          f"{'Total $':>10} {'MaxDD $':>10} {'Sharpe':>7}")
    print("  " + "-" * 62)
    for name, m in summary_rows:
        print(
            f"  {name:<18} {m['total_trades']:>6} "
            f"{m['win_rate_pct']:>5.1f}%  "
            f"{m['expectancy_r']:>+7.3f}R  "
            f"${m['total_pnl_usd']:>+8.2f}  "
            f"${m['max_drawdown_usd']:>+8.2f}  "
            f"{m['sharpe']:>6.2f}"
        )

    print()
    best = max(summary_rows, key=lambda x: x[1]["expectancy_r"])
    print(f"  Best expectancy: {best[0]} at {best[1]['expectancy_r']:+.3f}R/trade")
    safest = max(summary_rows, key=lambda x: x[1]["sharpe"])
    print(f"  Best Sharpe:     {safest[0]} at {safest[1]['sharpe']:.2f}")
    print("=" * 66 + "\n")


if __name__ == "__main__":
    run_backtest()

"""
India backtesting framework.
Replays all 4 NSE strategies against 500 days of historical daily bars
fetched via yfinance (same source the paper bot uses live).

Reports win rate, expectancy (R), max drawdown, and Sharpe ratio per ticker
and for the full NSE watchlist combined.

Run from the project root:
    python scripts/backtest_india.py

No credentials needed -- uses public Yahoo Finance data.
"""

import sys
import os
import numpy as np
import pandas as pd
from ta.volatility import AverageTrueRange

# Ensure UTF-8 output so the Rs. symbol renders on all platforms
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.upstox_paper import get_bars
from risk.sizing import compute_stop_target, compute_position_size
from risk.india_limits import RISK_PER_TRADE_INR
from config.india_settings import NSE_WATCHLIST, REGIME_PROXY

from strategies.ma_rsi import MARSIStrategy
from strategies.momentum import MomentumStrategy
from strategies.breakout_52w import Breakout52WStrategy
from strategies.rs_pullback import RSPullbackStrategy

BACKTEST_DAYS = 500

STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
    Breakout52WStrategy(),
    RSPullbackStrategy(),
]


def _add_atr(bars):
    df = pd.DataFrame(bars)
    df["close"] = df["close"].astype(float)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    df["atr"]   = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=14
    ).average_true_range()
    return df


def _simulate(strategy, bars, ticker):
    """
    Replay strategy on each growing window. Entry = close of signal bar.
    Bracket: stop = 1.5xATR, target = 3.0xATR (same as live bot).
    Skips new signals while a trade is open.
    """
    df = _add_atr(bars)
    trades = []
    last_exit_i = -1
    n = len(bars)

    for i in range(1, n):
        if i <= last_exit_i:
            continue

        window = bars[:i + 1]
        try:
            signals = strategy.generate_signals(ticker, window)
        except Exception:
            continue

        for s in signals:
            if s.action != "buy":
                continue

            atr = float(df.iloc[i]["atr"])
            if pd.isna(atr) or atr <= 0:
                break

            entry_price = float(bars[i]["close"])
            stop_price, target_price = compute_stop_target(entry_price, atr, side="buy")
            qty = compute_position_size(
                entry_price, stop_price, risk_override=RISK_PER_TRADE_INR
            )
            if qty <= 0:
                # manual fallback (same as india_intraday)
                stop_dist = abs(entry_price - stop_price)
                qty = max(1, int(RISK_PER_TRADE_INR / stop_dist)) if stop_dist > 0 else 1

            outcome = "open"
            exit_price = float(bars[-1]["close"])
            exit_i = n - 1

            for j in range(i + 1, n):
                lo = float(bars[j]["low"])
                hi = float(bars[j]["high"])
                if lo <= stop_price:
                    outcome, exit_price, exit_i = "stop", stop_price, j
                    break
                if hi >= target_price:
                    outcome, exit_price, exit_i = "target", target_price, j
                    break

            pnl = (exit_price - entry_price) * qty
            trades.append({
                "ticker":     ticker,
                "entry_date": bars[i]["ts"],
                "exit_date":  bars[exit_i]["ts"],
                "entry":      entry_price,
                "exit":       exit_price,
                "stop":       stop_price,
                "target":     target_price,
                "qty":        qty,
                "pnl":        pnl,
                "r":          pnl / RISK_PER_TRADE_INR,
                "outcome":    outcome,
            })

            last_exit_i = exit_i
            break

    return trades


def _metrics(trades):
    closed = [t for t in trades if t["outcome"] != "open"]
    if not closed:
        return None

    df = pd.DataFrame(closed)
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    win_rate    = len(wins) / len(df) * 100
    expectancy  = float(df["r"].mean())
    total_pnl   = float(df["pnl"].sum())

    cum_pnl = df["pnl"].cumsum()
    max_dd  = float((cum_pnl - cum_pnl.cummax()).min())

    sharpe = 0.0
    r_std  = float(df["r"].std())
    if r_std > 0 and len(df) > 1:
        trades_per_year = len(df) / (BACKTEST_DAYS / 365.0)
        sharpe = (expectancy / r_std) * (trades_per_year ** 0.5)

    return {
        "total_trades":    len(df),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate_pct":    round(win_rate, 1),
        "avg_win_r":       round(float(wins["r"].mean())   if len(wins)   > 0 else 0, 2),
        "avg_loss_r":      round(float(losses["r"].mean()) if len(losses) > 0 else 0, 2),
        "expectancy_r":    round(expectancy, 3),
        "total_pnl_inr":   round(total_pnl, 0),
        "max_drawdown_inr":round(max_dd, 0),
        "sharpe":          round(sharpe, 2),
    }


def _run_strategy(strategy, all_bars):
    all_trades = []
    ticker_metrics = {}
    for ticker, bars in all_bars.items():
        trades = _simulate(strategy, bars, ticker)
        all_trades.extend(trades)
        ticker_metrics[ticker] = _metrics(trades)
    return all_trades, ticker_metrics


def _fmt_inr(val):
    """Format INR value with L/K suffix."""
    if val >= 0:
        sign = "+"
    else:
        sign = "-"
        val = abs(val)
    if val >= 100_000:
        return f"{sign}₹{val/100_000:.2f}L"
    if val >= 1_000:
        return f"{sign}₹{val/1_000:.1f}K"
    return f"{sign}₹{val:.0f}"


def run_backtest():
    print(f"\n{'=' * 72}")
    print(f"  India Backtest  |  {BACKTEST_DAYS}-day window  |  "
          f"Risk ₹{RISK_PER_TRADE_INR:,.0f}/trade  |  1.5xATR stop, 3xATR target")
    print(f"  Strategies: {', '.join(s.name for s in STRATEGIES)}")
    print("=" * 72)
    print(f"\n  Fetching {BACKTEST_DAYS} days of NSE bars for {len(NSE_WATCHLIST)} tickers + regime proxy...")
    print("  (Uses Yahoo Finance — takes ~60s for the full watchlist)\n")

    all_bars = {}
    for ticker in NSE_WATCHLIST + [REGIME_PROXY]:
        try:
            bars = get_bars(ticker, days=BACKTEST_DAYS)
            if len(bars) >= 60:
                all_bars[ticker] = bars
                marker = "  " if len(bars) >= 220 else " !"
                print(f"   {marker} {ticker:<16} {len(bars):>3} bars")
            else:
                print(f"    ! {ticker:<16} only {len(bars)} bars — skipped")
        except Exception as e:
            print(f"    ! {ticker:<16} ERROR: {e}")

    # exclude regime proxy from strategy scan
    scan_bars = {k: v for k, v in all_bars.items() if k != REGIME_PROXY}

    summary_rows = []
    for strategy in STRATEGIES:
        print(f"\n  Running {strategy.name}...", flush=True)
        all_trades, ticker_m = _run_strategy(strategy, scan_bars)
        port_m = _metrics(all_trades)

        print(f"\n  [{strategy.name}]")
        print(f"  {'Ticker':<14} {'Trades':>6} {'Win%':>6} {'Exp(R)':>9} {'P&L':>14} {'MaxDD':>14}")
        print("  " + "-" * 66)
        for ticker in NSE_WATCHLIST:
            m = ticker_m.get(ticker)
            if m and m["total_trades"] > 0:
                print(
                    f"  {ticker:<14} {m['total_trades']:>6} "
                    f"{m['win_rate_pct']:>5.1f}%  "
                    f"{m['expectancy_r']:>+7.3f}R  "
                    f"{_fmt_inr(m['total_pnl_inr']):>14}  "
                    f"{_fmt_inr(m['max_drawdown_inr']):>14}"
                )
            else:
                print(f"  {ticker:<14}  (no closed trades)")

        if port_m:
            summary_rows.append((strategy.name, port_m))

    # ── Portfolio-level strategy comparison ──────────────────────────────
    print(f"\n{'=' * 72}")
    print("  STRATEGY COMPARISON  (NSE watchlist combined)")
    print("=" * 72)
    print(f"  {'Strategy':<18} {'Trades':>6} {'Win%':>6} {'Exp(R)':>9} "
          f"{'Total P&L':>14} {'MaxDD':>14} {'Sharpe':>7}")
    print("  " + "-" * 72)
    for name, m in summary_rows:
        print(
            f"  {name:<18} {m['total_trades']:>6} "
            f"{m['win_rate_pct']:>5.1f}%  "
            f"{m['expectancy_r']:>+7.3f}R  "
            f"{_fmt_inr(m['total_pnl_inr']):>14}  "
            f"{_fmt_inr(m['max_drawdown_inr']):>14}  "
            f"{m['sharpe']:>6.2f}"
        )

    if summary_rows:
        print()
        best_exp = max(summary_rows, key=lambda x: x[1]["expectancy_r"])
        best_sharpe = max(summary_rows, key=lambda x: x[1]["sharpe"])
        print(f"  Best expectancy: {best_exp[0]} at {best_exp[1]['expectancy_r']:+.3f}R/trade")
        print(f"  Best Sharpe:     {best_sharpe[0]} at {best_sharpe[1]['sharpe']:.2f}")

        # ── Combined portfolio simulation (all strategies, all tickers) ──
        all_combined = []
        for strategy in STRATEGIES:
            trades, _ = _run_strategy(strategy, scan_bars)
            all_combined.extend(trades)

        combo_m = _metrics(all_combined)
        if combo_m:
            print(f"\n  {'─' * 50}")
            print(f"  ALL STRATEGIES COMBINED  ({len(all_combined)} total trades)")
            print(f"  {'─' * 50}")
            print(f"  Total P&L:   {_fmt_inr(combo_m['total_pnl_inr'])}")
            print(f"  Win rate:    {combo_m['win_rate_pct']:.1f}%")
            print(f"  Expectancy:  {combo_m['expectancy_r']:+.3f}R")
            print(f"  Max drawdown:{_fmt_inr(combo_m['max_drawdown_inr'])}")
            print(f"  Sharpe:      {combo_m['sharpe']:.2f}")
            # Estimate annualised return assuming ₹25L account
            account = 25_00_000
            ann_pnl_est = combo_m["total_pnl_inr"] * (365 / BACKTEST_DAYS)
            roi_pct = ann_pnl_est / account * 100
            print(f"\n  Annualised P&L estimate:  {_fmt_inr(ann_pnl_est)}/yr "
                  f"on ₹25L  →  {roi_pct:+.1f}% ROI")

    print("=" * 72 + "\n")


if __name__ == "__main__":
    run_backtest()

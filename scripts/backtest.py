"""
Day 7: Backtesting framework.
Replays the MA+RSI strategy against 1 year of historical daily bars.
Reports win rate, expectancy (R), max drawdown, and Sharpe ratio per ticker
and for the full portfolio.

Run from the project root:
    python scripts/backtest.py
"""

import sys
import os
import numpy as np
import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.alpaca import get_bars
from risk.sizing import compute_stop_target, compute_position_size
from risk.limits import RISK_PER_TRADE_USD
from config.settings import WATCHLIST

SHORT_WINDOW = 10
LONG_WINDOW = 30
RSI_PERIOD = 14
RSI_MIN = 40.0
RSI_MAX = 70.0
ATR_PERIOD = 14
BACKTEST_DAYS = 365


def _build_indicators(bars):
    """
    Compute SMA10, SMA30, RSI14, ATR14 for a full bar list.
    Returns a clean DataFrame with NaN rows dropped.
    """
    df = pd.DataFrame(bars)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    df["sma_short"] = SMAIndicator(close=df["close"], window=SHORT_WINDOW).sma_indicator()
    df["sma_long"] = SMAIndicator(close=df["close"], window=LONG_WINDOW).sma_indicator()
    df["rsi"] = RSIIndicator(close=df["close"], window=RSI_PERIOD).rsi()
    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=ATR_PERIOD
    ).average_true_range()

    return df.dropna().reset_index(drop=True)


def _find_buy_signals(df):
    """
    Identify bar indices where a bullish MA crossover fires with RSI in zone.
    Mirrors the exact logic in strategies/ma_rsi.py.
    """
    signal_indices = []
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        bullish_cross = (
            prev["sma_short"] <= prev["sma_long"]
            and curr["sma_short"] > curr["sma_long"]
        )
        rsi_ok = RSI_MIN <= float(curr["rsi"]) <= RSI_MAX

        if bullish_cross and rsi_ok:
            signal_indices.append(i)

    return signal_indices


def _simulate_trades(df, signal_indices, ticker):
    """
    For each signal, simulate a bracket trade using ATR-based stop and target.
    Entry: close of signal bar (conservative — next open would be more realistic
           but close gives a clean comparison baseline).
    Stop:   entry - 1.5 * ATR  (same as live sizing)
    Target: entry + 3.0 * ATR
    Walk forward bar by bar; if both stop and target are hit in the same bar
    (gap scenario), stop takes priority (most conservative assumption).
    Skips any signal that falls inside a still-open trade window.
    """
    trades = []
    last_exit_i = -1

    for sig_i in signal_indices:
        if sig_i <= last_exit_i:
            continue

        entry_price = float(df.iloc[sig_i]["close"])
        atr = float(df.iloc[sig_i]["atr"])

        if atr <= 0 or np.isnan(atr):
            continue

        stop_price, target_price = compute_stop_target(entry_price, atr, side="buy")
        qty = compute_position_size(entry_price, stop_price)

        if qty == 0:
            continue

        outcome = "open"
        exit_price = float(df.iloc[-1]["close"])
        exit_i = len(df) - 1

        for j in range(sig_i + 1, len(df)):
            bar_low = float(df.iloc[j]["low"])
            bar_high = float(df.iloc[j]["high"])

            if bar_low <= stop_price:
                outcome = "stop"
                exit_price = stop_price
                exit_i = j
                break
            if bar_high >= target_price:
                outcome = "target"
                exit_price = target_price
                exit_i = j
                break

        pnl = (exit_price - entry_price) * qty
        r_multiple = pnl / RISK_PER_TRADE_USD

        trades.append({
            "ticker": ticker,
            "entry_date": df.iloc[sig_i]["ts"],
            "exit_date": df.iloc[exit_i]["ts"],
            "entry": entry_price,
            "exit": exit_price,
            "stop": stop_price,
            "target": target_price,
            "qty": qty,
            "pnl": pnl,
            "r": r_multiple,
            "outcome": outcome,
        })

        last_exit_i = exit_i

    return trades


def _compute_metrics(trades):
    """Compute performance stats from a list of trade dicts (excludes open trades)."""
    closed = [t for t in trades if t["outcome"] != "open"]
    if not closed:
        return None

    df = pd.DataFrame(closed)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    win_rate = len(wins) / len(df) * 100
    avg_win_r = float(wins["r"].mean()) if len(wins) > 0 else 0.0
    avg_loss_r = float(losses["r"].mean()) if len(losses) > 0 else 0.0
    expectancy_r = float(df["r"].mean())
    total_pnl = float(df["pnl"].sum())

    cum_pnl = df["pnl"].cumsum()
    rolling_max = cum_pnl.cummax()
    max_drawdown = float((cum_pnl - rolling_max).min())

    sharpe = 0.0
    r_std = float(df["r"].std())
    if r_std > 0 and len(df) > 1:
        # Annualise using actual trade count over the backtest window
        trades_per_year = len(df) / (BACKTEST_DAYS / 365.0)
        sharpe = (expectancy_r / r_std) * (trades_per_year ** 0.5)

    return {
        "total_trades": len(df),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "expectancy_r": round(expectancy_r, 3),
        "total_pnl_usd": round(total_pnl, 2),
        "max_drawdown_usd": round(max_drawdown, 2),
        "sharpe": round(sharpe, 2),
    }


def run_backtest():
    all_trades = []
    ticker_results = {}

    print(f"\n{'=' * 62}")
    print(f"  MA+RSI Backtest  --  {BACKTEST_DAYS}-day window")
    print(f"  SMA{SHORT_WINDOW}/{LONG_WINDOW}  RSI({RSI_PERIOD}) zone {RSI_MIN:.0f}-{RSI_MAX:.0f}")
    print(f"  Risk ${RISK_PER_TRADE_USD}/trade  |  stop 1.5xATR  |  target 3.0xATR")
    print(f"{'=' * 62}\n")

    for ticker in WATCHLIST:
        print(f"  {ticker:<6} fetching...", end=" ", flush=True)
        try:
            bars = get_bars(ticker, days=BACKTEST_DAYS)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        min_bars = LONG_WINDOW + ATR_PERIOD + 5
        if len(bars) < min_bars:
            print(f"skipped (only {len(bars)} bars, need {min_bars})")
            continue

        df = _build_indicators(bars)
        signals = _find_buy_signals(df)
        trades = _simulate_trades(df, signals, ticker)

        closed = [t for t in trades if t["outcome"] != "open"]
        wins = sum(1 for t in closed if t["outcome"] == "target")
        print(
            f"{len(bars)} bars  {len(signals)} signals  "
            f"{len(closed)} trades  {wins}W/{len(closed)-wins}L"
        )

        all_trades.extend(trades)
        ticker_results[ticker] = trades

    # -- Per-ticker table --------------------------------------------------
    print(f"\n{'-' * 62}")
    print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'Exp(R)':>8} {'P&L $':>10} {'MaxDD $':>10}")
    print(f"{'-' * 62}")
    for ticker, trades in ticker_results.items():
        m = _compute_metrics(trades)
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

    # -- Portfolio summary --------------------------------------------------
    print(f"\n{'=' * 62}")
    print("  PORTFOLIO SUMMARY")
    print("=" * 62)
    m = _compute_metrics(all_trades)
    if m:
        print(f"  Total trades      {m['total_trades']}")
        print(f"  Win rate          {m['win_rate_pct']}%")
        print(f"  Avg win          +{m['avg_win_r']}R")
        print(f"  Avg loss          {m['avg_loss_r']}R")
        print(f"  Expectancy       {m['expectancy_r']:+.3f}R per trade")
        print(f"  Total P&L        ${m['total_pnl_usd']:+,.2f}")
        print(f"  Max drawdown     ${m['max_drawdown_usd']:,.2f}")
        print(f"  Sharpe ratio      {m['sharpe']:.2f}")
        print()

        if m["expectancy_r"] > 0:
            print("  VERDICT: Positive expectancy -- strategy has edge in this window.")
        else:
            print("  VERDICT: Negative expectancy — review parameters before live use.")
    else:
        print("  No closed trades found. The window may be too short or signals too rare.")

    print("=" * 62 + "\n")


if __name__ == "__main__":
    run_backtest()

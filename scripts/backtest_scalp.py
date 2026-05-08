"""
VWAP Scalp Strategy backtest (15-min bars).

Mirrors the exact conditions in strategies/intraday_scalp.py:
  - VWAP resets per session (daily)
  - Entry window: 10:00am–3:00pm ET only
  - Stop  = 0.5% below entry  (tight scalp risk)
  - Target = 1.0% above entry  (2:1 R/R)
  - Force-close at 3:45pm ET if neither stop nor target filled

Inline indicator computation (not calling generate_signals() per-bar) so the
full 90-session simulation finishes in seconds rather than minutes.

Run from the project root:
    python scripts/backtest_scalp.py
"""

import sys
import os
from datetime import time

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.alpaca import get_bars
from risk.limits import RISK_PER_TRADE_USD
from config.settings import WATCHLIST

# Tickers where VWAP scalp has Sharpe > 1.0 on 90-day backtest.
# Running on the full WATCHLIST drags down the portfolio — restrict to winners.
SCALP_UNIVERSE = ["SOUN", "NFLX", "UNH", "CRWD", "GOOGL", "V", "MA"]

BACKTEST_DAYS  = 90
STOP_PCT       = 0.005   # 0.5% stop below entry
TARGET_PCT     = 0.005   # 0.5% target (1:1 R/R — 1% was too far in a 2-hr window)
VWAP_TOUCH_PCT = 0.006   # within 0.6% of VWAP (was 0.3% — too tight, too few signals)
RSI_MAX        = 50.0    # neutral dip (was 45 — too restrictive)
RSI_WINDOW     = 9       # 9-bar warmup = entries from 11:45am (was 14 = entries from 1pm)
VOL_MULT       = 0.0     # IEX covers ~2% of real volume — volume filter is unreliable, removed

_ET          = ZoneInfo("America/New_York")
_ENTRY_START = time(10, 0)   # no entries before 10:00am ET
_ENTRY_END   = time(15, 0)   # no entries at or after 3:00pm ET
_FORCE_CLOSE = time(15, 45)  # force-close all open positions at 3:45pm ET


# ──────────────────────────────────────────────────────────────────────────────
# Simulation
# ──────────────────────────────────────────────────────────────────────────────

def _record(trades, ticker, entry, exit_px, stop, target, qty, date, bar_time, outcome):
    pnl = (exit_px - entry) * qty
    trades.append({
        "ticker":  ticker,
        "date":    str(date),
        "time":    str(bar_time),
        "entry":   round(entry, 2),
        "exit":    round(exit_px, 2),
        "stop":    round(stop, 2),
        "target":  round(target, 2),
        "qty":     qty,
        "pnl":     round(pnl, 2),
        "r":       round(pnl / RISK_PER_TRADE_USD, 3),
        "outcome": outcome,
    })


def _simulate_scalp(bars_15m, ticker):
    """
    Walk through 15-min bars session by session.
    Pre-computes VWAP, RSI(14), and 10-bar vol avg once per session (fast).
    One entry per session — skips remainder of session after a trade is placed.
    """
    df_all = pd.DataFrame(bars_15m)
    df_all["ts_et"]  = pd.to_datetime(df_all["ts"], utc=True).dt.tz_convert(_ET)
    df_all["date_et"] = df_all["ts_et"].dt.date
    df_all["time_et"] = df_all["ts_et"].dt.time

    for col in ("open", "high", "low", "close", "volume"):
        df_all[col] = df_all[col].astype(float)

    trades = []

    for session_date in sorted(df_all["date_et"].unique()):
        sd = df_all[df_all["date_et"] == session_date].reset_index(drop=True).copy()
        if len(sd) < RSI_WINDOW:
            continue

        # Compute session indicators once
        sd["tp"]       = (sd["high"] + sd["low"] + sd["close"]) / 3
        sd["vwap"]     = (sd["tp"] * sd["volume"]).cumsum() / sd["volume"].cumsum()
        sd["rsi"]      = RSIIndicator(close=sd["close"], window=RSI_WINDOW).rsi()
        sd["vol_avg"]  = sd["volume"].rolling(10).mean()

        in_trade  = False
        entry_price = stop_price = target_price = qty = None

        for i in range(len(sd)):
            row = sd.iloc[i]
            bar_time = row["time_et"]

            # ── Exit: stop, target, or force-close ──────────────────────────
            if in_trade:
                lo, hi = float(row["low"]), float(row["high"])
                if lo <= stop_price:
                    _record(trades, ticker, entry_price, stop_price,
                            stop_price, target_price, qty, session_date, bar_time, "stop")
                    in_trade = False
                    continue
                if hi >= target_price:
                    _record(trades, ticker, entry_price, target_price,
                            stop_price, target_price, qty, session_date, bar_time, "target")
                    in_trade = False
                    continue
                if bar_time >= _FORCE_CLOSE:
                    exit_px = float(row["close"])
                    _record(trades, ticker, entry_price, exit_px,
                            stop_price, target_price, qty, session_date, bar_time, "force_close")
                    in_trade = False
                    continue
                continue  # still holding

            # ── Entry: all 4 conditions must be met ─────────────────────────
            if bar_time < _ENTRY_START or bar_time >= _ENTRY_END:
                continue
            if pd.isna(row["rsi"]) or pd.isna(row["vol_avg"]):
                continue

            close   = float(row["close"])
            vwap    = float(row["vwap"])
            rsi     = float(row["rsi"])
            vol_now = float(row["volume"])
            vol_avg = float(row["vol_avg"])

            # 1. Price above VWAP
            if close <= vwap:
                continue
            # 2. Pulled back to within VWAP_TOUCH_PCT
            pct_above = (close - vwap) / vwap
            if pct_above > VWAP_TOUCH_PCT:
                continue
            # 3. RSI dip
            if rsi >= RSI_MAX:
                continue
            # 4. Volume participation (VOL_MULT=0 disables — IEX data is unreliable for this)
            if VOL_MULT > 0 and vol_avg > 0 and vol_now < VOL_MULT * vol_avg:
                continue

            # All conditions met — enter the trade
            entry_price  = close
            stop_price   = round(entry_price * (1 - STOP_PCT),  2)
            target_price = round(entry_price * (1 + TARGET_PCT), 2)
            qty          = max(1, int(RISK_PER_TRADE_USD / (entry_price * STOP_PCT)))
            in_trade     = True

        # End of session: close anything still open
        if in_trade:
            last = sd.iloc[-1]
            exit_px = float(last["close"])
            _record(trades, ticker, entry_price, exit_px,
                    stop_price, target_price, qty, session_date, last["time_et"], "eod_close")

    return trades


# ──────────────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────────────

def _metrics(trades):
    if not trades:
        return None
    df = pd.DataFrame(trades)
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    win_rate     = len(wins) / len(df) * 100
    expectancy_r = float(df["r"].mean())
    total_pnl    = float(df["pnl"].sum())
    cum_pnl      = df["pnl"].cumsum()
    max_dd       = float((cum_pnl - cum_pnl.cummax()).min())

    sharpe = 0.0
    r_std  = float(df["r"].std())
    if r_std > 0 and len(df) > 1:
        trades_per_year = len(df) / (BACKTEST_DAYS / 365.0)
        sharpe = (expectancy_r / r_std) * (trades_per_year ** 0.5)

    return {
        "total_trades":    len(df),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate_pct":    round(win_rate, 1),
        "avg_win_r":       round(float(wins["r"].mean())   if len(wins)   > 0 else 0, 3),
        "avg_loss_r":      round(float(losses["r"].mean()) if len(losses) > 0 else 0, 3),
        "expectancy_r":    round(expectancy_r, 3),
        "total_pnl_usd":   round(total_pnl, 2),
        "max_drawdown_usd": round(max_dd, 2),
        "sharpe":          round(sharpe, 2),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def run_scalp_backtest():
    print(f"\n{'=' * 70}")
    print(f"  VWAP Scalp Backtest  |  {BACKTEST_DAYS}-day window  |  15-min IEX bars")
    print(f"  Entry: 10am–3pm ET  |  Stop {STOP_PCT*100:.1f}%  |  "
          f"Target {TARGET_PCT*100:.1f}%  |  1:1 R/R  |  Risk ${RISK_PER_TRADE_USD}/trade")
    print(f"  Force-close: 3:45pm ET")
    print("=" * 70)

    print(f"\n  Fetching {BACKTEST_DAYS} days of 15-min bars for {len(WATCHLIST)} tickers...")
    all_bars = {}
    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=BACKTEST_DAYS, timeframe="15min")
            if len(bars) >= 100:
                df_tmp = pd.to_datetime([b["ts"] for b in bars], utc=True)
                sessions = len(set(t.date() for t in df_tmp))
                all_bars[ticker] = bars
                print(f"    {ticker:<6} {len(bars):>5} bars  ({sessions} sessions)")
            else:
                print(f"    {ticker:<6} skipped ({len(bars)} bars — need ≥ 100)")
        except Exception as e:
            print(f"    {ticker:<6} ERROR: {e}")

    if not all_bars:
        print("\n  No bars fetched. Check ALPACA_KEY / ALPACA_SECRET in .env")
        return

    print(f"\n  Simulating...\n")
    print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'Exp(R)':>9} {'P&L $':>10} {'Sharpe':>7}")
    print("  " + "-" * 52)

    all_trades = []
    ticker_metrics = {}

    for ticker, bars in all_bars.items():
        trades = _simulate_scalp(bars, ticker)
        all_trades.extend(trades)
        m = _metrics(trades)
        ticker_metrics[ticker] = m
        if m and m["total_trades"] > 0:
            print(
                f"  {ticker:<8} {m['total_trades']:>6} "
                f"{m['win_rate_pct']:>5.1f}%  "
                f"{m['expectancy_r']:>+7.3f}R  "
                f"${m['total_pnl_usd']:>+8.2f}  "
                f"{m['sharpe']:>6.2f}"
            )
        else:
            print(f"  {ticker:<8}  (no trades)")

    port_m = _metrics(all_trades)

    print(f"\n{'=' * 70}")
    print("  PORTFOLIO SUMMARY")
    print("=" * 70)

    if not port_m:
        print("  No trades generated. Strategy conditions may be too tight for IEX data.")
        print("  Possible causes:")
        print("    - IEX volume data is sparse (~2-3% of market) → vol filter fires too often")
        print("    - RSI(14) needs 14 session bars → entries only possible after ~1:45pm ET")
        print("    - VWAP touch window of 0.3% may be too tight")
        print("=" * 70 + "\n")
        return

    print(f"  Total trades:      {port_m['total_trades']}")
    print(f"  Win rate:          {port_m['win_rate_pct']:.1f}%  "
          f"({port_m['wins']}W / {port_m['losses']}L)")
    print(f"  Avg win:           {port_m['avg_win_r']:+.3f}R  "
          f"(~ ${port_m['avg_win_r'] * RISK_PER_TRADE_USD:+.0f})")
    print(f"  Avg loss:          {port_m['avg_loss_r']:+.3f}R  "
          f"(~ ${port_m['avg_loss_r'] * RISK_PER_TRADE_USD:+.0f})")
    print(f"  Expectancy:        {port_m['expectancy_r']:+.3f}R/trade")
    print(f"  Total P&L:         ${port_m['total_pnl_usd']:+,.2f}")
    print(f"  Max drawdown:      ${port_m['max_drawdown_usd']:,.2f}")
    print(f"  Sharpe:            {port_m['sharpe']:.2f}")

    df_all_trades = pd.DataFrame(all_trades)

    # Outcome breakdown
    print(f"\n  Outcome breakdown:")
    for outcome, count in df_all_trades["outcome"].value_counts().items():
        print(f"    {outcome:<15} {count:>4}  ({count / len(df_all_trades) * 100:.0f}%)")

    # Sample size warning
    if port_m["total_trades"] < 30:
        print(f"\n  WARNING: Only {port_m['total_trades']} trades — insufficient for statistical "
              "confidence.")
        print("  Consider lowering VWAP_TOUCH_PCT (e.g. 0.006) or RSI_MAX (e.g. 50) to "
              "generate more signals.")

    # Top/bottom tickers
    ranked = sorted(
        [(t, m) for t, m in ticker_metrics.items() if m and m["total_trades"] > 0],
        key=lambda x: x[1]["expectancy_r"],
        reverse=True,
    )
    if ranked:
        print(f"\n  Top performers by expectancy:")
        for name, m in ranked[:5]:
            print(f"    {name:<6}  {m['expectancy_r']:+.3f}R  "
                  f"{m['win_rate_pct']:.0f}%  "
                  f"${m['total_pnl_usd']:+,.0f}  "
                  f"Sharpe {m['sharpe']:.2f}")
        if len(ranked) > 5:
            print(f"\n  Worst performers:")
            for name, m in ranked[-3:]:
                print(f"    {name:<6}  {m['expectancy_r']:+.3f}R  "
                      f"{m['win_rate_pct']:.0f}%  "
                      f"${m['total_pnl_usd']:+,.0f}  "
                      f"Sharpe {m['sharpe']:.2f}")

    # ── Filtered universe: only SCALP_UNIVERSE tickers ──────────────────
    scalp_trades = [t for t in all_trades if t["ticker"] in SCALP_UNIVERSE]
    scalp_m = _metrics(scalp_trades)
    if scalp_m and scalp_m["total_trades"] > 0:
        print(f"\n{'=' * 70}")
        print(f"  SCALP UNIVERSE ONLY  ({', '.join(SCALP_UNIVERSE)})")
        print("=" * 70)
        print(f"  Total trades:      {scalp_m['total_trades']}")
        print(f"  Win rate:          {scalp_m['win_rate_pct']:.1f}%  "
              f"({scalp_m['wins']}W / {scalp_m['losses']}L)")
        print(f"  Avg win:           {scalp_m['avg_win_r']:+.3f}R")
        print(f"  Avg loss:          {scalp_m['avg_loss_r']:+.3f}R")
        print(f"  Expectancy:        {scalp_m['expectancy_r']:+.3f}R/trade")
        print(f"  Total P&L:         ${scalp_m['total_pnl_usd']:+,.2f}")
        print(f"  Max drawdown:      ${scalp_m['max_drawdown_usd']:,.2f}")
        print(f"  Sharpe:            {scalp_m['sharpe']:.2f}")
        port_m = scalp_m  # use filtered metrics for the deploy verdict

    # Verdict
    print(f"\n{'=' * 70}")
    deploy = port_m["sharpe"] >= 1.0 and port_m["expectancy_r"] > 0
    verdict = "DEPLOY" if deploy else "DO NOT DEPLOY"
    print(f"  Verdict: {verdict}  "
          f"(threshold: Sharpe >= 1.0 AND Expectancy > 0)")
    if not deploy:
        if port_m["expectancy_r"] <= 0:
            print("  ->Negative expectancy: losing strategy, do not go live.")
        elif port_m["sharpe"] < 1.0:
            print(f"  ->Sharpe {port_m['sharpe']:.2f} is below 1.0 threshold. "
                  "Edge exists but risk-adjusted return not sufficient.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_scalp_backtest()

"""
Full-pipeline simulation against real historical data.

Unlike the backtest (which tests each strategy in isolation), this script
runs the ENTIRE intraday pipeline — regime gate, portfolio manager, all
strategy signals, position sizing — against past market data, day by day.

The LLM filter is bypassed (approved=True) so you can observe strategy
behavior without needing an API key.

Run:
    python -m scripts.simulate                   # last 90 trading days
    python -m scripts.simulate --days 180        # custom window
    python -m scripts.simulate --days 60 --verbose  # show skipped signals too
"""

import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from brokers.alpaca import get_bars
from config.settings import WATCHLIST
from risk.sizing import compute_atr, compute_stop_target, compute_position_size
from risk.limits import (
    RISK_PER_TRADE_USD, MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_USD,
    MAX_TRADES_PER_DAY,
)
from routines.intraday import get_market_regime, STRATEGIES, TREND_ONLY_STRATEGIES
from routines.portfolio import filter_buy_signals
from strategies.base import Signal


# ── Simulation state ──────────────────────────────────────────────────────────

@dataclass
class SimPosition:
    ticker: str
    entry_price: float
    stop_price: float
    target_price: float
    qty: int
    strategy: str
    entry_date: str

    @property
    def r_per_share(self):
        return abs(self.entry_price - self.stop_price)


@dataclass
class SimTrade:
    ticker: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    qty: int
    exit_reason: str  # "target", "stop", "forced_eod"

    @property
    def pnl(self):
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def r(self):
        risk = abs(self.entry_price - self.stop_price)
        return self.pnl / (risk * self.qty) if risk > 0 else 0.0

    @property
    def stop_price(self):
        return self._stop

    @stop_price.setter
    def stop_price(self, v):
        self._stop = v


@dataclass
class DaySummary:
    date: str
    regime: str
    active_strategies: list
    entries: list = field(default_factory=list)
    exits: list = field(default_factory=list)
    daily_pnl: float = 0.0
    skipped: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_df(bars):
    df = pd.DataFrame(bars)
    df["close"] = df["close"].astype(float)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    df["open"]  = df["open"].astype(float)
    return df


def _get_date(bar):
    """Extract YYYY-MM-DD from a bar dict."""
    ts = bar.get("ts") or bar.get("timestamp") or bar.get("t") or ""
    return str(ts)[:10]


def _simulate_bracket_fill(pos: SimPosition, future_bars: list):
    """
    Check whether stop or target was hit in the next N bars.
    Returns (exit_price, exit_date, exit_reason) or None if still open.
    Assumes each bar represents one day; checks high/low.
    """
    for bar in future_bars:
        low  = float(bar["low"])
        high = float(bar["high"])
        date = _get_date(bar)

        if low <= pos.stop_price:
            return pos.stop_price, date, "stop"
        if high >= pos.target_price:
            return pos.target_price, date, "target"

    return None  # still open at end of window


# ── Main simulation ───────────────────────────────────────────────────────────

def run_simulation(days: int = 90, verbose: bool = False):
    print(f"\nFull-pipeline simulation — last {days} calendar days")
    print(f"Watchlist: {WATCHLIST}")
    print(f"Max positions: {MAX_OPEN_POSITIONS}  |  Risk/trade: ${RISK_PER_TRADE_USD}  |  Kill switch: -${MAX_DAILY_LOSS_USD}/day")
    print("=" * 72)

    # Fetch bars — extra buffer so early days have enough history for indicators
    fetch_days = days + 350
    print(f"\nFetching {fetch_days} days of bars for {len(WATCHLIST)} tickers...")
    all_bars = {}
    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=fetch_days)
            if len(bars) >= 60:
                all_bars[ticker] = bars
                print(f"  {ticker}: {len(bars)} bars")
            else:
                print(f"  {ticker}: only {len(bars)} bars — skipping")
        except Exception as e:
            print(f"  {ticker}: fetch failed — {e}")

    if not all_bars:
        print("No data fetched. Check ALPACA_KEY in .env")
        return

    # Find trading dates in the simulation window using SPY as calendar
    spy_bars = all_bars.get("SPY", list(all_bars.values())[0])
    # Use the last `days` calendar days worth of bars
    # Approximate: last N bars roughly covers N trading days
    trading_bars_per_year = 252
    approx_bars = int(days * trading_bars_per_year / 365)
    sim_bars = spy_bars[-approx_bars:]
    trading_dates = [_get_date(b) for b in sim_bars]
    trading_dates = sorted(set(d for d in trading_dates if d))

    print(f"\nSimulating {len(trading_dates)} trading days "
          f"({trading_dates[0]} to {trading_dates[-1]})")
    print("=" * 72)

    # Simulation state
    open_positions: dict[str, SimPosition] = {}   # ticker -> SimPosition
    closed_trades: list = []
    day_summaries: list[DaySummary] = []
    cumulative_pnl = 0.0

    for day_idx, date in enumerate(trading_dates):
        # Build the "current bars" window — everything up to and including today
        def bars_through(ticker, up_to_date):
            return [b for b in all_bars.get(ticker, [])
                    if _get_date(b) <= up_to_date]

        spy_window = bars_through("SPY", date)

        # ── Check bracket fills on open positions ──────────────────────────
        filled_today = []
        for ticker, pos in list(open_positions.items()):
            future = [b for b in all_bars.get(ticker, [])
                      if _get_date(b) > pos.entry_date and _get_date(b) <= date]
            if not future:
                continue
            result = _simulate_bracket_fill(pos, future)
            if result:
                exit_price, exit_date, reason = result
                pnl = (exit_price - pos.entry_price) * pos.qty
                closed_trades.append({
                    "ticker": ticker,
                    "strategy": pos.strategy,
                    "entry_date": pos.entry_date,
                    "exit_date": exit_date,
                    "entry": pos.entry_price,
                    "exit": exit_price,
                    "qty": pos.qty,
                    "reason": reason,
                    "pnl": round(pnl, 2),
                    "r": round(pnl / (pos.r_per_share * pos.qty), 3)
                         if pos.r_per_share > 0 else 0.0,
                })
                cumulative_pnl += pnl
                filled_today.append(ticker)

        for t in filled_today:
            del open_positions[t]

        open_tickers = set(open_positions.keys())

        # ── Regime check ───────────────────────────────────────────────────
        regime = get_market_regime(spy_window)
        active_strats = [s for s in STRATEGIES
                         if regime == "uptrend" or s.name not in TREND_ONLY_STRATEGIES]

        # ── Generate signals ───────────────────────────────────────────────
        buy_candidates = {}
        daily_trades = 0
        daily_pnl_today = sum(
            t["pnl"] for t in closed_trades
            if t["exit_date"] == date
        )

        # Kill switch: if cumulative daily loss too large, skip
        if daily_pnl_today <= -MAX_DAILY_LOSS_USD:
            summary = DaySummary(date=date, regime=regime,
                                 active_strategies=[s.name for s in active_strats],
                                 daily_pnl=daily_pnl_today, skipped=-1)
            day_summaries.append(summary)
            continue

        skipped_this_day = 0

        for ticker in WATCHLIST:
            if ticker not in all_bars:
                continue
            window = bars_through(ticker, date)
            if len(window) < 35:
                continue
            for strat in active_strats:
                try:
                    signals = strat.generate_signals(ticker, window)
                    for s in signals:
                        if s.action == "buy":
                            buy_candidates.setdefault(ticker, []).append(
                                (strat.name, s)
                            )
                except Exception:
                    pass

        to_buy = filter_buy_signals(buy_candidates, open_tickers)

        # ── Execute buys (simulated) ───────────────────────────────────────
        entries_today = []
        for strat_name, sig in to_buy:
            if daily_trades >= MAX_TRADES_PER_DAY:
                skipped_this_day += 1
                continue
            if len(open_positions) >= MAX_OPEN_POSITIONS:
                skipped_this_day += 1
                continue

            ticker = sig.ticker
            window = bars_through(ticker, date)
            atr = compute_atr(window)
            if not atr:
                skipped_this_day += 1
                continue

            entry_price = float(window[-1]["close"])
            stop_price, target_price = compute_stop_target(entry_price, atr, "buy")
            qty = compute_position_size(entry_price, stop_price)
            if qty == 0:
                skipped_this_day += 1
                continue

            open_positions[ticker] = SimPosition(
                ticker=ticker,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                qty=qty,
                strategy=strat_name,
                entry_date=date,
            )
            entries_today.append({
                "ticker": ticker, "strategy": strat_name,
                "entry": entry_price, "stop": stop_price,
                "target": target_price, "qty": qty,
            })
            daily_trades += 1

        summary = DaySummary(
            date=date, regime=regime,
            active_strategies=[s.name for s in active_strats],
            entries=entries_today,
            exits=[t for t in closed_trades if t["exit_date"] == date],
            daily_pnl=daily_pnl_today,
            skipped=skipped_this_day,
        )
        day_summaries.append(summary)

    # ── Force-close any still-open positions at last bar ─────────────────────
    for ticker, pos in open_positions.items():
        last_bar = bars_through(ticker, trading_dates[-1])
        if not last_bar:
            continue
        exit_price = float(last_bar[-1]["close"])
        pnl = (exit_price - pos.entry_price) * pos.qty
        closed_trades.append({
            "ticker": ticker, "strategy": pos.strategy,
            "entry_date": pos.entry_date,
            "exit_date": trading_dates[-1],
            "entry": pos.entry_price, "exit": exit_price,
            "qty": pos.qty, "reason": "still_open",
            "pnl": round(pnl, 2),
            "r": round(pnl / (pos.r_per_share * pos.qty), 3)
                 if pos.r_per_share > 0 else 0.0,
        })
        cumulative_pnl += pnl

    # ── Print results ─────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  DAY-BY-DAY LOG")
    print("=" * 72)

    for s in day_summaries:
        if s.skipped == -1:
            print(f"  {s.date}  [{s.regime:10}]  KILL SWITCH ACTIVE — no trading")
            continue

        has_activity = s.entries or s.exits
        if not has_activity and not verbose:
            continue

        regime_tag = f"[{s.regime}]"
        strat_tag  = ",".join(s.active_strategies)
        print(f"\n  {s.date}  {regime_tag:12}  strategies: {strat_tag}")

        for e in s.entries:
            print(f"    BUY   {e['ticker']:6}  {e['strategy']:16}  "
                  f"qty={e['qty']}  entry=${e['entry']:.2f}  "
                  f"stop=${e['stop']:.2f}  target=${e['target']:.2f}")

        for x in s.exits:
            pnl_tag = f"+${x['pnl']:.2f}" if x['pnl'] >= 0 else f"-${abs(x['pnl']):.2f}"
            print(f"    EXIT  {x['ticker']:6}  {x['strategy']:16}  "
                  f"{x['reason']:10}  {pnl_tag}  ({x['r']:+.2f}R)  "
                  f"entered {x['entry_date']}")

        if s.skipped > 0:
            print(f"    (skipped {s.skipped} signals — position cap / no ATR)")

    # ── Summary stats ─────────────────────────────────────────────────────────
    if not closed_trades:
        print("\n  No trades were completed in this window.")
        return

    wins   = [t for t in closed_trades if t["pnl"] > 0]
    losses = [t for t in closed_trades if t["pnl"] <= 0]
    total  = len(closed_trades)
    win_rate = len(wins) / total * 100 if total else 0
    avg_win  = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    total_pnl = sum(t["pnl"] for t in closed_trades)
    avg_r   = sum(t["r"] for t in closed_trades) / total

    targets = [t for t in closed_trades if t["reason"] == "target"]
    stops   = [t for t in closed_trades if t["reason"] == "stop"]

    correction_days = sum(1 for s in day_summaries if s.regime == "correction")
    uptrend_days    = sum(1 for s in day_summaries if s.regime == "uptrend")

    print("\n" + "=" * 72)
    print("  SIMULATION SUMMARY")
    print("=" * 72)
    print(f"  Period:         {trading_dates[0]}  to  {trading_dates[-1]}")
    print(f"  Trading days:   {len(trading_dates)}  "
          f"(uptrend {uptrend_days}d / correction {correction_days}d)")
    print(f"  Total trades:   {total}")
    print(f"  Win rate:       {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Target hits:    {len(targets)}   Stop hits: {len(stops)}")
    print(f"  Avg win:        +${avg_win:.2f}   Avg loss: -${abs(avg_loss):.2f}")
    print(f"  Avg R:          {avg_r:+.3f}R")
    print(f"  Total P&L:      ${total_pnl:+.2f}")
    print()

    print("  By strategy:")
    strat_names = sorted({t["strategy"] for t in closed_trades})
    for sname in strat_names:
        st = [t for t in closed_trades if t["strategy"] == sname]
        sw = [t for t in st if t["pnl"] > 0]
        sr = sum(t["r"] for t in st) / len(st) if st else 0
        sp = sum(t["pnl"] for t in st)
        print(f"    {sname:20}  {len(st):3} trades  "
              f"{len(sw)/len(st)*100:.0f}% win  "
              f"{sr:+.3f}R avg  ${sp:+.2f}")
    print()

    by_ticker = {}
    for t in closed_trades:
        by_ticker.setdefault(t["ticker"], []).append(t)
    print("  By ticker:")
    for ticker in sorted(by_ticker):
        tt = by_ticker[ticker]
        tp = sum(t["pnl"] for t in tt)
        print(f"    {ticker:6}  {len(tt)} trades  ${tp:+.2f}")

    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full-pipeline simulation")
    parser.add_argument("--days", type=int, default=90,
                        help="Calendar days to simulate (default: 90)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all days, including days with no activity")
    args = parser.parse_args()
    run_simulation(days=args.days, verbose=args.verbose)

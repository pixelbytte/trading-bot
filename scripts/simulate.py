"""
Full-pipeline simulation against real historical data.

Unlike the backtest (which tests each strategy in isolation), this script
runs the ENTIRE intraday pipeline — regime gate, portfolio manager, all
strategy signals, position sizing — against past market data, day by day.

The LLM filter is bypassed (approved=True) so you can observe strategy
behavior without needing an API key.

Modes:
  --trailing  : trailing stops instead of fixed 2R target.
                Trail at running_high - 2R once +1R is hit.
                Tighten to running_high - 1.5R once +3R is hit (Minervini).
  --compound  : 1% of current equity per trade instead of fixed $50.
  --pyramid   : pyramid into winners (Livermore / Minervini SEPA).
                At +1R: add 0.5x size with stop=breakeven.
                At +2R: add 0.25x size with stop=+1R.
                Total exposure grows 1.0x -> 1.5x -> 1.75x on confirmed winners.

Run:
    python -m scripts.simulate                                       # baseline
    python -m scripts.simulate --trailing --compound                 # T1+T2
    python -m scripts.simulate --trailing --compound --pyramid       # max ROI
    python -m scripts.simulate --days 500 --trailing --pyramid       # 2yr window
"""

import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from brokers.alpaca import get_bars
from config.settings import WATCHLIST, ACCOUNT_SIZE_USD
from risk.sizing import compute_atr, compute_stop_target, compute_position_size, dynamic_risk_usd
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
    exit_reason: str  # "target", "stop", "trail", "forced_eod"

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
    Fixed 2R target (original mode).
    Returns (exit_price, exit_date, exit_reason, qty_mult, avg_entry) or None.
    qty_mult is always 1.0 (no pyramiding in fixed-bracket mode).
    """
    for bar in future_bars:
        low  = float(bar["low"])
        high = float(bar["high"])
        date = _get_date(bar)

        if low <= pos.stop_price:
            return pos.stop_price, date, "stop", 1.0, pos.entry_price
        if high >= pos.target_price:
            return pos.target_price, date, "target", 1.0, pos.entry_price

    return None


def _simulate_trailing_stop_fill(pos: SimPosition, future_bars: list,
                                  pyramid: bool = False):
    """
    Trailing stop mode (Minervini-style) with optional pyramiding.

    Base trailing logic:
    - Phase 1: hard stop until running_high reaches +1R
    - Phase 2: once +1R, trail = max(current, running_high - 2R)
    - Phase 3 (Minervini "tighten"): once +3R, trail = max(current, running_high - 1.5R)
      → big winners lock in more of their gains

    Pyramiding (Livermore/Darvas, --pyramid flag):
    - At +1R: add a 0.5x tranche, stop=entry (breakeven). Total = 1.5x.
    - At +2R: add a 0.25x tranche, stop=entry+1R. Total = 1.75x.
    - Exit ALL tranches together when trailing stop hits.

    Returns (exit_price, exit_date, exit_reason, pnl_multiplier) or None.
    pnl_multiplier expresses total tranche size (1.0 = no pyramid, 1.75 = full).
    """
    R = pos.r_per_share
    if R <= 0:
        result = _simulate_bracket_fill(pos, future_bars)
        if result is None:
            return None
        ep, ed, er = result
        return ep, ed, er, 1.0

    trailing_stop = pos.stop_price
    running_high = pos.entry_price
    trailing_active = False

    # Pyramid tranche state (each tranche has its own entry & weight)
    tranches = [(pos.entry_price, 1.0)]   # (entry_price, weight)
    pyr1_added = False  # +1R add
    pyr2_added = False  # +2R add
    tighten_active = False

    for bar in future_bars:
        low  = float(bar["low"])
        high = float(bar["high"])
        date = _get_date(bar)

        running_high = max(running_high, high)

        # Trailing activation thresholds
        if running_high >= pos.entry_price + R:
            trailing_active = True
        if running_high >= pos.entry_price + 3 * R:
            tighten_active = True

        # Pyramiding: add tranches as new R-levels are crossed
        if pyramid:
            if not pyr1_added and running_high >= pos.entry_price + R:
                tranches.append((pos.entry_price + R, 0.5))  # 0.5x at +1R
                pyr1_added = True
            if not pyr2_added and running_high >= pos.entry_price + 2 * R:
                tranches.append((pos.entry_price + 2 * R, 0.25))  # 0.25x at +2R
                pyr2_added = True

        if trailing_active:
            trail_mult = 1.5 if tighten_active else 2.0
            new_trail = running_high - trail_mult * R
            trailing_stop = max(trailing_stop, new_trail)

        if low <= trailing_stop:
            exit_price = round(trailing_stop, 2)
            reason = "trail" if trailing_active else "stop"
            total_w = sum(w for _, w in tranches)
            avg_entry = sum(w * tp for tp, w in tranches) / total_w
            return exit_price, date, reason, total_w, avg_entry

    return None


# ── Leveraged ETF sleeve (TQQQ/UPRO proxy) ────────────────────────────────────

def _simulate_leverage_sleeve(spy_bars: list, trading_dates: list,
                               sleeve_capital: float,
                               etf_bars: list = None,
                               etf_symbol: str = "TQQQ",
                               fallback_leverage: float = 3.0) -> dict:
    """
    Simulate a leveraged-ETF sleeve gated by SPY's 200-day SMA regime.

    Logic:
      - Only LONG when SPY closed >= SMA200 the previous day (no look-ahead).
      - When etf_bars provided: use REAL ETF daily returns (decay/tracking baked in).
      - When etf_bars is None: synthesize using fallback_leverage * spy_daily_return.
      - Compounds daily.
      - Sits in cash (0% return) during corrections.

    Research basis: Cheng & Madhavan (2009), "Dynamics of leveraged ETFs."
    With a regime filter on a 3x ETF, you avoid the worst of the daily-rebalance
    decay, which is highest in choppy, sideways markets — exactly what the
    SMA200 gate locks you out of.

    Returns dict with ending_value, total_pnl, days_long, days_cash, max_drawdown,
    and source ("real" if etf_bars used, "synthetic" otherwise).
    """
    if not spy_bars or len(spy_bars) < 205 or not trading_dates:
        return {"ending_value": sleeve_capital, "total_pnl": 0.0,
                "days_long": 0, "days_cash": 0, "max_drawdown": 0.0,
                "source": "none", "symbol": etf_symbol}

    # SPY history for the regime gate
    spy_by_date = {_get_date(b): float(b["close"]) for b in spy_bars}
    sorted_dates = sorted(spy_by_date.keys())
    closes_series = pd.Series([spy_by_date[d] for d in sorted_dates])
    sma200 = closes_series.rolling(200).mean()
    sma200_by_date = {sorted_dates[i]: (float(sma200.iloc[i]) if not pd.isna(sma200.iloc[i]) else None)
                      for i in range(len(sorted_dates))}

    # ETF return source: real bars preferred, fall back to SPY*leverage
    use_real = etf_bars and len(etf_bars) >= 60
    etf_by_date = ({_get_date(b): float(b["close"]) for b in etf_bars}
                   if use_real else {})

    # Per-ETF SMA50 for a tighter regime filter on the leveraged instrument itself.
    # NDX has selloffs SPY misses (tech-specific corrections), and 3x decay
    # eats sleeve value fast in chop. Faber (2007) "Quantitative Approach to
    # Tactical Asset Allocation" — use the held instrument's own trend filter.
    etf_sma50_by_date = {}
    if use_real:
        sorted_etf_dates = sorted(etf_by_date.keys())
        etf_closes = pd.Series([etf_by_date[d] for d in sorted_etf_dates])
        etf_sma50 = etf_closes.rolling(50).mean()
        etf_sma50_by_date = {sorted_etf_dates[i]: (float(etf_sma50.iloc[i])
                                                    if not pd.isna(etf_sma50.iloc[i]) else None)
                             for i in range(len(sorted_etf_dates))}

    sleeve_value = sleeve_capital
    peak = sleeve_capital
    max_dd = 0.0
    days_long = 0
    days_cash = 0

    prev_date = None
    for date in trading_dates:
        if date not in spy_by_date:
            continue
        if prev_date is None:
            prev_date = date
            continue

        # Gate 1: SPY > SMA200 (broad market uptrend)
        prev_sma = sma200_by_date.get(prev_date)
        spy_ok = prev_sma is not None and spy_by_date[prev_date] >= prev_sma

        # Gate 2: ETF > its own SMA50 (instrument-specific health)
        etf_ok = True
        if use_real:
            etf_sma = etf_sma50_by_date.get(prev_date)
            etf_ok = (etf_sma is not None
                      and prev_date in etf_by_date
                      and etf_by_date[prev_date] >= etf_sma)

        in_uptrend = spy_ok and etf_ok

        if in_uptrend:
            if use_real and prev_date in etf_by_date and date in etf_by_date:
                # Real ETF return — already 3x with decay baked in
                etf_ret = (etf_by_date[date] / etf_by_date[prev_date]) - 1
            else:
                # Synthetic 3x SPY (no decay modeled)
                spy_ret = (spy_by_date[date] / spy_by_date[prev_date]) - 1
                etf_ret = fallback_leverage * spy_ret
            sleeve_value *= (1 + etf_ret)
            days_long += 1
        else:
            days_cash += 1

        peak = max(peak, sleeve_value)
        dd = (peak - sleeve_value) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        prev_date = date

    return {
        "ending_value": round(sleeve_value, 2),
        "total_pnl": round(sleeve_value - sleeve_capital, 2),
        "days_long": days_long,
        "days_cash": days_cash,
        "max_drawdown": round(max_dd * 100, 1),
        "source": "real" if use_real else "synthetic",
        "symbol": etf_symbol,
    }


# ── Main simulation ───────────────────────────────────────────────────────────

def run_simulation(days: int = 90, verbose: bool = False,
                   trailing: bool = False, compound: bool = False,
                   pyramid: bool = False, leverage: bool = False,
                   leverage_pct: float = 0.30):
    mode_tag = []
    if trailing:
        mode_tag.append("trailing-stops")
    if compound:
        mode_tag.append("compounding")
    if pyramid:
        mode_tag.append("pyramid(+1R/+2R)")
    if leverage:
        mode_tag.append(f"3x-sleeve({int(leverage_pct*100)}%)")
    mode_str = " + ".join(mode_tag) if mode_tag else "baseline (fixed 2R + fixed $50)"

    print(f"\nFull-pipeline simulation -- last {days} calendar days")
    print(f"Mode: {mode_str}")
    print(f"Watchlist: {len(WATCHLIST)} tickers")
    print(f"Max positions: {MAX_OPEN_POSITIONS}  |  "
          f"Risk/trade: {'1% of equity' if compound else f'${RISK_PER_TRADE_USD}'}  |  "
          f"Kill switch: -${MAX_DAILY_LOSS_USD}/day")
    print("=" * 72)

    # Fetch bars
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
                print(f"  {ticker}: only {len(bars)} bars -- skipping")
        except Exception as e:
            print(f"  {ticker}: fetch failed -- {e}")

    if not all_bars:
        print("No data fetched. Check ALPACA_KEY in .env")
        return

    # Fetch TQQQ separately for the leverage sleeve (not in WATCHLIST since
    # strategies don't trade it — buy-and-hold via regime gate only)
    tqqq_bars = []
    if leverage:
        try:
            tqqq_bars = get_bars("TQQQ", days=fetch_days)
            print(f"  TQQQ: {len(tqqq_bars)} bars (real returns for sleeve)")
        except Exception as e:
            print(f"  TQQQ: fetch failed ({e}) — falling back to 3x SPY synthetic")

    spy_bars = all_bars.get("SPY", list(all_bars.values())[0])
    trading_bars_per_year = 252
    approx_bars = int(days * trading_bars_per_year / 365)
    sim_bars = spy_bars[-approx_bars:]
    trading_dates = [_get_date(b) for b in sim_bars]
    trading_dates = sorted(set(d for d in trading_dates if d))

    print(f"\nSimulating {len(trading_dates)} trading days "
          f"({trading_dates[0]} to {trading_dates[-1]})")
    print("=" * 72)

    # Simulation state
    open_positions: dict[str, SimPosition] = {}
    closed_trades: list = []
    day_summaries: list[DaySummary] = []
    cumulative_pnl = 0.0
    current_equity = float(ACCOUNT_SIZE_USD)

    if trailing:
        fill_fn = lambda pos, bars: _simulate_trailing_stop_fill(pos, bars, pyramid=pyramid)
    else:
        fill_fn = _simulate_bracket_fill

    for day_idx, date in enumerate(trading_dates):
        def bars_through(ticker, up_to_date):
            return [b for b in all_bars.get(ticker, [])
                    if _get_date(b) <= up_to_date]

        spy_window = bars_through("SPY", date)

        # Check bracket/trailing fills on open positions
        filled_today = []
        for ticker, pos in list(open_positions.items()):
            future = [b for b in all_bars.get(ticker, [])
                      if _get_date(b) > pos.entry_date and _get_date(b) <= date]
            if not future:
                continue
            result = fill_fn(pos, future)
            if result:
                exit_price, exit_date, reason, qty_mult, avg_entry = result
                # Pyramiding: tranches at +1R / +2R increase total exposure.
                # Total qty = base_qty * qty_mult. Realized pnl uses avg_entry.
                total_qty = pos.qty * qty_mult
                pnl = (exit_price - avg_entry) * total_qty
                r_val = round(pnl / (pos.r_per_share * pos.qty), 3) if pos.r_per_share > 0 else 0.0
                closed_trades.append({
                    "ticker": ticker,
                    "strategy": pos.strategy,
                    "entry_date": pos.entry_date,
                    "exit_date": exit_date,
                    "entry": pos.entry_price,
                    "exit": exit_price,
                    "qty": pos.qty,
                    "qty_mult": qty_mult,
                    "reason": reason,
                    "pnl": round(pnl, 2),
                    "r": r_val,
                })
                cumulative_pnl += pnl
                current_equity += pnl
                filled_today.append(ticker)

        for t in filled_today:
            del open_positions[t]

        open_tickers = set(open_positions.keys())

        # Regime check
        regime = get_market_regime(spy_window)
        active_strats = [s for s in STRATEGIES
                         if regime == "uptrend" or s.name not in TREND_ONLY_STRATEGIES]

        daily_pnl_today = sum(
            t["pnl"] for t in closed_trades
            if t["exit_date"] == date
        )

        if daily_pnl_today <= -MAX_DAILY_LOSS_USD:
            summary = DaySummary(date=date, regime=regime,
                                 active_strategies=[s.name for s in active_strats],
                                 daily_pnl=daily_pnl_today, skipped=-1)
            day_summaries.append(summary)
            continue

        # Generate signals
        buy_candidates = {}
        daily_trades = 0
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

        # Relative Strength filter: only buy tickers outperforming SPY over 6 months.
        # 6-month window is less sensitive to short-term corrections than 3-month.
        # Leaders beat the market before you buy them, not after.
        if len(spy_window) >= 126:
            spy_6m = float(spy_window[-1]["close"]) / float(spy_window[-126]["close"]) - 1
            rs_filtered = {}
            for ticker, candidates in buy_candidates.items():
                tw = bars_through(ticker, date)
                if len(tw) >= 126:
                    tick_6m = float(tw[-1]["close"]) / float(tw[-126]["close"]) - 1
                    if tick_6m >= spy_6m - 0.05:  # allow 5% tolerance for early-stage leaders
                        rs_filtered[ticker] = candidates
                else:
                    rs_filtered[ticker] = candidates  # not enough data: fail open
            buy_candidates = rs_filtered

        to_buy = filter_buy_signals(buy_candidates, open_tickers)

        # Execute buys (simulated)
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

            if trailing:
                # Wide target (10R) — trailing stop handles the exit
                stop_price, target_price = compute_stop_target(
                    entry_price, atr, "buy", target_mult=10.0
                )
            else:
                stop_price, target_price = compute_stop_target(entry_price, atr, "buy")

            # Position sizing: fixed or proportional
            risk = dynamic_risk_usd(current_equity) if compound else None
            qty = compute_position_size(entry_price, stop_price, risk_override=risk)
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

    # Force-close open positions at last bar
    for ticker, pos in open_positions.items():
        last_bar = [b for b in all_bars.get(ticker, [])
                    if _get_date(b) <= trading_dates[-1]]
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
        current_equity += pnl

    # Print day-by-day log
    print("\n" + "=" * 72)
    print("  DAY-BY-DAY LOG")
    print("=" * 72)

    for s in day_summaries:
        if s.skipped == -1:
            print(f"  {s.date}  [{s.regime:10}]  KILL SWITCH ACTIVE -- no trading")
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
            print(f"    (skipped {s.skipped} signals -- position cap / no ATR)")

    # Summary stats
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
    trails  = [t for t in closed_trades if t["reason"] == "trail"]

    correction_days = sum(1 for s in day_summaries if s.regime == "correction")
    uptrend_days    = sum(1 for s in day_summaries if s.regime == "uptrend")

    period_months = len(trading_dates) / 21  # ~21 trading days per month
    base_account = float(ACCOUNT_SIZE_USD)

    # Optional 3x leveraged sleeve overlay (TQQQ/UPRO proxy)
    sleeve_result = None
    sleeve_pnl = 0.0
    if leverage:
        sleeve_capital = base_account * leverage_pct
        sleeve_result = _simulate_leverage_sleeve(
            spy_bars=all_bars.get("SPY", []),
            trading_dates=trading_dates,
            sleeve_capital=sleeve_capital,
            etf_bars=tqqq_bars,
            etf_symbol="TQQQ",
            fallback_leverage=3.0,
        )
        sleeve_pnl = sleeve_result["total_pnl"]

    combined_pnl = total_pnl + sleeve_pnl
    roi_pct = combined_pnl / base_account * 100
    annual_roi = roi_pct / period_months * 12

    print("\n" + "=" * 72)
    print("  SIMULATION SUMMARY")
    print("=" * 72)
    print(f"  Mode:           {mode_str}")
    print(f"  Period:         {trading_dates[0]}  to  {trading_dates[-1]}")
    print(f"  Trading days:   {len(trading_dates)}  "
          f"(uptrend {uptrend_days}d / correction {correction_days}d)")
    print(f"  Total trades:   {total}")
    print(f"  Win rate:       {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Exits:          target={len(targets)}  stop={len(stops)}  trail={len(trails)}")
    print(f"  Avg win:        +${avg_win:.2f}   Avg loss: -${abs(avg_loss):.2f}")
    print(f"  Win/loss ratio: {avg_win/abs(avg_loss):.2f}x")
    print(f"  Avg R:          {avg_r:+.3f}R")
    print(f"  Strategy P&L:   ${total_pnl:+.2f}  on ${base_account:,.0f} base")
    if sleeve_result is not None:
        src = sleeve_result.get("source", "synthetic")
        sym = sleeve_result.get("symbol", "TQQQ")
        label = f"real {sym}" if src == "real" else "3x SPY synthetic"
        print(f"  Leverage P&L:   ${sleeve_pnl:+.2f}  "
              f"({label}, {int(leverage_pct*100)}% allocation, "
              f"max DD {sleeve_result['max_drawdown']:.1f}%, "
              f"{sleeve_result['days_long']}d long / {sleeve_result['days_cash']}d cash)")
        print(f"  COMBINED P&L:   ${combined_pnl:+.2f}")
    print(f"  ROI:            {roi_pct:+.1f}% over {period_months:.0f} months")
    print(f"  Annualized:     {annual_roi:+.1f}%/yr")
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
    print("  By ticker (top 10 by P&L):")
    sorted_tickers = sorted(by_ticker.items(),
                            key=lambda x: sum(t["pnl"] for t in x[1]), reverse=True)
    for ticker, tt in sorted_tickers[:10]:
        tp = sum(t["pnl"] for t in tt)
        print(f"    {ticker:6}  {len(tt)} trades  ${tp:+.2f}")

    # Investment scaling table
    print()
    print("=" * 72)
    print("  INVESTMENT PROJECTIONS  (same strategy, scaled account)")
    print("=" * 72)
    print(f"  {'Invested':>12}  {'Projected P&L':>15}  {'Final value':>13}  {'ROI':>7}  {'Annual':>7}")
    print(f"  {'-'*60}")
    for inv in [5_000, 10_000, 25_000, 50_000, 100_000]:
        scale = inv / base_account
        scaled_pnl = combined_pnl * scale
        final = inv + scaled_pnl
        r = scaled_pnl / inv * 100
        ann = r / period_months * 12
        print(f"  ${inv:>11,.0f}  ${scaled_pnl:>+14,.2f}  ${final:>12,.2f}  {r:>+6.1f}%  {ann:>+6.1f}%")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full-pipeline simulation")
    parser.add_argument("--days", type=int, default=90,
                        help="Calendar days to simulate (default: 90)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all days, including days with no activity")
    parser.add_argument("--trailing", action="store_true",
                        help="Use trailing stops instead of fixed 2R target")
    parser.add_argument("--compound", action="store_true",
                        help="Use 1pct-of-equity risk instead of fixed $50")
    parser.add_argument("--pyramid", action="store_true",
                        help="Add 0.5x size at +1R, 0.25x at +2R (Livermore/Minervini)")
    parser.add_argument("--leverage", action="store_true",
                        help="Add 3x SPY leveraged sleeve (TQQQ/UPRO proxy), regime-gated")
    parser.add_argument("--leverage-pct", type=float, default=0.30,
                        help="Fraction of equity in the leveraged sleeve (default: 0.30)")
    args = parser.parse_args()
    run_simulation(days=args.days, verbose=args.verbose,
                   trailing=args.trailing, compound=args.compound,
                   pyramid=args.pyramid,
                   leverage=args.leverage, leverage_pct=args.leverage_pct)

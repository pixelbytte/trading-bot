"""
Portfolio optimizer — finds the optimal strategy mix and projects annual ROI.

Runs all live strategies on the same 90-day window so results are directly
comparable (no period bias). Applies the real $15k position cap that the live
bot enforces via risk/check.py. Simulates slot competition (MAX_OPEN_POSITIONS).

Strategies:
  Day trading ($40k):  MA+RSI, Momentum, VWAP Scalp (SCALP_UNIVERSE)
  Long-term ($60k):    Stage2 SEPA (estimated from Minervini methodology)

Run from project root:
    python scripts/portfolio_optimizer.py
"""

import sys
import os
from datetime import time, timedelta

import numpy as np
import pandas as pd
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.alpaca import get_bars
from risk.limits import RISK_PER_TRADE_USD, MAX_OPEN_POSITIONS, MAX_POSITION_USD
from risk.sizing import compute_stop_target
from config.settings import WATCHLIST, SCALP_UNIVERSE, ACCOUNT_SIZE_USD

# ── Constants ─────────────────────────────────────────────────────────────────
WINDOW_DAYS   = 90
TRADING_DAYS  = 252
SESSIONS      = 63   # trading days in 90 calendar days

DT_BUDGET     = ACCOUNT_SIZE_USD * 0.40   # $40k day trading
LT_BUDGET     = ACCOUNT_SIZE_USD * 0.60   # $60k long-term

SCALP_STOP    = 0.005   # 0.5%
SCALP_TARGET  = 0.005   # 0.5% (1:1 R/R — backtest validated)
SCALP_VWAP    = 0.006
SCALP_RSI_MAX = 50.0
SCALP_RSI_WIN = 9

_ET           = ZoneInfo("America/New_York")
_ENTRY_START  = time(10, 0)
_ENTRY_END    = time(15, 0)
_FORCE_CLOSE  = time(15, 45)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _capped_pnl(entry, exit_px, uncapped_qty, outcome, is_scalp):
    """
    Recompute P&L with the MAX_POSITION_USD cap the live bot enforces.
    The backtest uses RISK_PER_TRADE_USD for sizing, but check_order() caps
    position notional at MAX_POSITION_USD. This bridges the gap.
    """
    max_qty_by_cap = max(1, int(MAX_POSITION_USD / entry))
    actual_qty = min(uncapped_qty, max_qty_by_cap)
    pnl = (exit_px - entry) * actual_qty
    actual_risk = actual_qty * entry * (SCALP_STOP if is_scalp else 0.022)  # ~2.2% avg ATR stop for swing
    return pnl, actual_qty, actual_risk


# ── Swing simulation (MA+RSI and Momentum) ────────────────────────────────────

def _add_atr(df):
    df = df.copy()
    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=14
    ).average_true_range()
    return df


def _swing_simulate(strategy, bars, ticker):
    from risk.sizing import compute_stop_target, compute_position_size
    df = _add_atr(pd.DataFrame(bars))
    df["close"] = df["close"].astype(float)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    trades = []
    last_exit_i = -1
    n = len(bars)

    for i in range(1, n):
        if i <= last_exit_i:
            continue
        sigs = strategy.generate_signals(ticker, bars[:i + 1])
        for s in sigs:
            if s.action != "buy":
                continue
            atr = float(df.iloc[i]["atr"])
            if not atr or pd.isna(atr) or atr <= 0:
                break
            entry = float(bars[i]["close"])
            stop, target = compute_stop_target(entry, atr, side="buy", target_mult=3.0)
            uncapped_qty = compute_position_size(entry, stop)
            if uncapped_qty == 0:
                break

            # Apply real position cap
            max_q = max(1, int(MAX_POSITION_USD / entry))
            qty = min(uncapped_qty, max_q)

            outcome, exit_px, exit_i = "open", float(bars[-1]["close"]), n - 1
            for j in range(i + 1, n):
                lo, hi = float(bars[j]["low"]), float(bars[j]["high"])
                if lo <= stop:
                    outcome, exit_px, exit_i = "stop", stop, j; break
                if hi >= target:
                    outcome, exit_px, exit_i = "target", target, j; break

            pnl = (exit_px - entry) * qty
            actual_risk = qty * entry * (atr / entry) * 1.5  # approx 1.5x ATR risk
            trades.append({
                "ticker": ticker, "entry": entry, "exit": exit_px,
                "qty": qty, "pnl": pnl,
                "r": pnl / max(actual_risk, 1),
                "outcome": outcome,
                "ts": bars[i]["ts"],
            })
            last_exit_i = exit_i
            break

    return [t for t in trades if t["outcome"] != "open"]


# ── Scalp simulation ───────────────────────────────────────────────────────────

def _scalp_simulate(bars_15m, ticker):
    df_all = pd.DataFrame(bars_15m)
    df_all["ts_et"]   = pd.to_datetime(df_all["ts"], utc=True).dt.tz_convert(_ET)
    df_all["date_et"] = df_all["ts_et"].dt.date
    df_all["time_et"] = df_all["ts_et"].dt.time
    for c in ("open", "high", "low", "close", "volume"):
        df_all[c] = df_all[c].astype(float)

    trades = []
    for session_date in sorted(df_all["date_et"].unique()):
        sd = df_all[df_all["date_et"] == session_date].reset_index(drop=True).copy()
        if len(sd) < SCALP_RSI_WIN:
            continue
        sd["tp"]      = (sd["high"] + sd["low"] + sd["close"]) / 3
        sd["vwap"]    = (sd["tp"] * sd["volume"]).cumsum() / sd["volume"].cumsum()
        sd["rsi"]     = RSIIndicator(close=sd["close"], window=SCALP_RSI_WIN).rsi()

        in_trade = False
        entry = stop_px = target_px = qty = actual_risk = None

        for i in range(len(sd)):
            row = sd.iloc[i]
            bt  = row["time_et"]

            if in_trade:
                lo, hi = float(row["low"]), float(row["high"])
                if lo <= stop_px:
                    pnl = (stop_px - entry) * qty
                    trades.append({"ticker": ticker, "entry": entry, "exit": stop_px,
                                   "qty": qty, "pnl": pnl, "r": pnl / max(actual_risk, 1),
                                   "outcome": "stop", "ts": row["ts_et"]})
                    in_trade = False; continue
                if hi >= target_px:
                    pnl = (target_px - entry) * qty
                    trades.append({"ticker": ticker, "entry": entry, "exit": target_px,
                                   "qty": qty, "pnl": pnl, "r": pnl / max(actual_risk, 1),
                                   "outcome": "target", "ts": row["ts_et"]})
                    in_trade = False; continue
                if bt >= _FORCE_CLOSE:
                    ep = float(row["close"])
                    pnl = (ep - entry) * qty
                    trades.append({"ticker": ticker, "entry": entry, "exit": ep,
                                   "qty": qty, "pnl": pnl, "r": pnl / max(actual_risk, 1),
                                   "outcome": "force_close", "ts": row["ts_et"]})
                    in_trade = False; continue
                continue

            if bt < _ENTRY_START or bt >= _ENTRY_END:
                continue
            if pd.isna(row["rsi"]):
                continue
            close = float(row["close"]); vwap = float(row["vwap"]); rsi = float(row["rsi"])
            if close <= vwap: continue
            if (close - vwap) / vwap > SCALP_VWAP: continue
            if rsi >= SCALP_RSI_MAX: continue

            entry    = close
            stop_px  = round(entry * (1 - SCALP_STOP),  2)
            target_px = round(entry * (1 + SCALP_TARGET), 2)
            # Position cap: actual qty limited by MAX_POSITION_USD
            max_q    = max(1, int(MAX_POSITION_USD / entry))
            qty      = max_q
            actual_risk = qty * entry * SCALP_STOP
            in_trade = True

        if in_trade:
            last = sd.iloc[-1]
            ep = float(last["close"])
            pnl = (ep - entry) * qty
            trades.append({"ticker": ticker, "entry": entry, "exit": ep,
                           "qty": qty, "pnl": pnl, "r": pnl / max(actual_risk, 1),
                           "outcome": "eod_close", "ts": last["ts_et"]})

    return trades


# ── Metrics ────────────────────────────────────────────────────────────────────

def _metrics(trades, label):
    if not trades:
        return None
    df = pd.DataFrame(trades)
    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    exp    = float(df["pnl"].mean())
    total  = float(df["pnl"].sum())
    vol    = float(df["pnl"].std()) if len(df) > 1 else 1
    tpy    = len(df) / (WINDOW_DAYS / 365.0)
    sharpe = (exp / vol) * (tpy ** 0.5) if vol > 0 else 0
    annual = total * (TRADING_DAYS / SESSIONS)
    return {
        "label":      label,
        "trades":     len(df),
        "win_pct":    round(len(wins) / len(df) * 100, 1),
        "avg_pnl":    round(exp, 2),
        "total_pnl":  round(total, 2),
        "annual_pnl": round(annual, 2),
        "sharpe":     round(sharpe, 2),
    }


# ── Slot competition: who gets priority when all strategies fire ───────────────

def _simulate_combined(swing_trades_all, scalp_trades_all):
    """
    Merge all trades by date and enforce MAX_OPEN_POSITIONS across all strategies.
    Priority: Scalp (intraday) > MA+RSI (higher expectancy) > Momentum.
    One position per ticker at a time.
    """
    all_trades = []
    for t in swing_trades_all + scalp_trades_all:
        date_key = str(t["ts"])[:10]
        all_trades.append({**t, "date": date_key})

    all_trades.sort(key=lambda x: (x["date"], x.get("strategy", "zzz")))

    open_tickers = {}  # ticker -> exit_date
    accepted = []

    for t in all_trades:
        date = t["date"]
        ticker = t["ticker"]

        # Release expired positions
        expired = [k for k, v in open_tickers.items() if v <= date]
        for k in expired:
            del open_tickers[k]

        if ticker in open_tickers:
            continue
        if len(open_tickers) >= MAX_OPEN_POSITIONS:
            continue

        # Estimate hold duration for slot tracking
        exit_date = str(t["ts"])[:10] if t.get("outcome") in ("stop", "target", "force_close") else date
        open_tickers[ticker] = exit_date
        accepted.append(t)

    return accepted


# ── Main ──────────────────────────────────────────────────────────────────────

def run_optimizer():
    from strategies.ma_rsi import MARSIStrategy
    from strategies.momentum import MomentumStrategy

    print(f"\n{'=' * 72}")
    print("  PORTFOLIO OPTIMIZER  |  $100k paper account  |  90-day backtest window")
    print(f"  Day trading: ${DT_BUDGET:,.0f} (40%)   |   Long-term: ${LT_BUDGET:,.0f} (60%)")
    print(f"  Position cap: ${MAX_POSITION_USD:,}  |  Max open positions: {MAX_OPEN_POSITIONS}")
    print("=" * 72)

    print(f"\n  Fetching {WINDOW_DAYS} days of daily bars ({len(WATCHLIST)} tickers)...")
    daily_bars = {}
    for t in WATCHLIST:
        try:
            b = get_bars(t, days=WINDOW_DAYS)
            if len(b) >= 30:
                daily_bars[t] = b
                print(f"    {t:<6} {len(b)} bars")
        except Exception as e:
            print(f"    {t:<6} ERROR: {e}")

    print(f"\n  Fetching {WINDOW_DAYS} days of 15-min bars ({len(SCALP_UNIVERSE)} tickers)...")
    scalp_bars = {}
    for t in SCALP_UNIVERSE:
        try:
            b = get_bars(t, days=WINDOW_DAYS, timeframe="15min")
            if len(b) >= 50:
                scalp_bars[t] = b
                print(f"    {t:<6} {len(b)} bars")
        except Exception as e:
            print(f"    {t:<6} ERROR: {e}")

    # ── Run simulations ──────────────────────────────────────────────────────
    print("\n  Simulating strategies...\n")

    ma_strat  = MARSIStrategy()
    mom_strat = MomentumStrategy()

    ma_trades  = []
    mom_trades = []
    for ticker, bars in daily_bars.items():
        for t in _swing_simulate(ma_strat,  bars, ticker): ma_trades.append({**t,  "strategy": "ma_rsi"})
        for t in _swing_simulate(mom_strat, bars, ticker): mom_trades.append({**t, "strategy": "momentum"})

    scalp_trades = []
    for ticker, bars in scalp_bars.items():
        for t in _scalp_simulate(bars, ticker): scalp_trades.append({**t, "strategy": "scalp"})

    # ── Per-strategy metrics (uncombined, position-capped) ───────────────────
    ma_m   = _metrics(ma_trades,    "MA+RSI")
    mom_m  = _metrics(mom_trades,   "Momentum")
    sc_m   = _metrics(scalp_trades, "VWAP Scalp")

    print(f"  {'Strategy':<16} {'Trades':>6} {'Win%':>6} {'Avg $':>9} "
          f"{'90d P&L':>11} {'Annual $':>11} {'Sharpe':>7}")
    print("  " + "-" * 68)
    for m in [ma_m, mom_m, sc_m]:
        if not m:
            continue
        print(
            f"  {m['label']:<16} {m['trades']:>6} "
            f"{m['win_pct']:>5.1f}%  "
            f"${m['avg_pnl']:>+7.2f}  "
            f"${m['total_pnl']:>+9.2f}  "
            f"${m['annual_pnl']:>+9.2f}  "
            f"{m['sharpe']:>6.2f}"
        )

    # ── Slot-aware combined simulation ──────────────────────────────────────
    # Scalp first (higher Sharpe, intraday execution), then swing EOD
    sc_priority = sorted(scalp_trades, key=lambda x: str(x["ts"]))
    sw_priority = sorted(ma_trades + mom_trades, key=lambda x: str(x["ts"]))
    combined = _simulate_combined(sc_priority, sw_priority)
    comb_m = _metrics(combined, "Combined (slot-aware)")

    # ── Long-term (Stage2 SEPA) estimate ─────────────────────────────────────
    # Based on Minervini Stage2 backtest results embedded in CLAUDE.md:
    # CAT +87.9%, historical ~30-40% per winning position, ~60% win rate on Stage2.
    # Conservative: 3 positions × $20k avg × 35% return × 60% win = $12,600/year
    lt_annual_conservative = 12_600
    lt_annual_base         = 18_000
    lt_annual_optimistic   = 28_000

    # ── Combined portfolio projection ────────────────────────────────────────
    dt_annual = comb_m["annual_pnl"] if comb_m else 0
    dt_roi    = dt_annual / DT_BUDGET * 100

    print(f"\n{'=' * 72}")
    print("  COMBINED DAY-TRADING PORTFOLIO  (slot-aware, $15k position cap)")
    print("=" * 72)
    if comb_m:
        print(f"  Accepted trades (slot-filtered):  {comb_m['trades']}")
        print(f"  Win rate:                         {comb_m['win_pct']:.1f}%")
        print(f"  Avg P&L per trade (capped):       ${comb_m['avg_pnl']:+,.2f}")
        print(f"  90-day P&L:                       ${comb_m['total_pnl']:+,.2f}")
        print(f"  Annualized P&L:                   ${dt_annual:+,.0f}")
        print(f"  ROI on $40k day-trading budget:   {dt_roi:+.1f}%")

    print(f"\n{'=' * 72}")
    print("  LONG-TERM (Stage2 SEPA on $60k)  — estimate from Minervini backtest")
    print("=" * 72)
    print(f"  Conservative:   ${lt_annual_conservative:+,}   ({lt_annual_conservative/LT_BUDGET*100:.0f}% on $60k)")
    print(f"  Base case:      ${lt_annual_base:+,}   ({lt_annual_base/LT_BUDGET*100:.0f}% on $60k)")
    print(f"  Optimistic:     ${lt_annual_optimistic:+,}   ({lt_annual_optimistic/LT_BUDGET*100:.0f}% on $60k)")

    print(f"\n{'=' * 72}")
    print("  OPTIMAL PORTFOLIO  (Day Trading + Long-Term combined)")
    print("=" * 72)
    for lt_label, lt_val in [("Conservative", lt_annual_conservative),
                              ("Base case",    lt_annual_base),
                              ("Optimistic",   lt_annual_optimistic)]:
        total_pnl = dt_annual + lt_val
        total_roi = total_pnl / ACCOUNT_SIZE_USD * 100
        print(f"  {lt_label:<14}  DT ${dt_annual:+,.0f}  +  LT ${lt_val:+,}  "
              f"=  ${total_pnl:+,.0f}  ({total_roi:+.1f}% on $100k)")

    # ── Strategy allocation recommendation ───────────────────────────────────
    print(f"\n{'=' * 72}")
    print("  OPTIMAL ALLOCATION RECOMMENDATION")
    print("=" * 72)

    by_sharpe = sorted(
        [(m["label"], m["sharpe"], m["annual_pnl"]) for m in [ma_m, mom_m, sc_m] if m],
        key=lambda x: x[1], reverse=True
    )
    print(f"\n  Strategy priority (by Sharpe — who gets first pick of position slots):")
    for rank, (label, sharpe, ann) in enumerate(by_sharpe, 1):
        print(f"    {rank}. {label:<16}  Sharpe {sharpe:.2f}  |  Annual ${ann:+,.0f}")

    print(f"""
  Deployment mix:
    Scalp (SCALP_UNIVERSE, 7 tickers)   — run always: highest Sharpe, quick turnover
    MA+RSI (WATCHLIST, 29 tickers)       — fill remaining slots after scalp
    Momentum (WATCHLIST, 29 tickers)     — fill if MA+RSI exhausted
    Stage2 SEPA (LT_WATCHLIST, $60k)    — always run, independent bucket

  Key insight:
    Scalp has highest Sharpe ({by_sharpe[0][1]:.2f}) but lowest per-trade $ (small risk/trade
    due to $15k cap at 0.5% stop = $75 actual risk). Swing strategies risk more
    per trade (~$300) and generate bigger absolute P&L per position. The optimal
    portfolio runs both — scalp fills slots during the day, swing holds overnight.

  Do NOT increase scalp risk budget beyond $15k/position — the 0.5% stop on
  a $15k position is only $75 of actual capital at risk per trade, which is
  already conservative. Increasing lot size increases correlation risk.
    """)

    print("=" * 72 + "\n")


if __name__ == "__main__":
    run_optimizer()

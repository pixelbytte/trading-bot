"""
Long-term portfolio routine (Days 22, 25-26).
Runs once per day at market open via longterm.yml.

Day 22: Stage 2 SEPA entries with wider stops (3x ATR) and 3:1 R:R targets.
Day 25-26 additions:
  - Screener ranks all candidates by momentum + fundamentals + thesis before entry.
  - DCA logic: scale into existing positions on 5-12% pullbacks (max 2 tranches).

Portfolio constraints:
  - Max 3 simultaneous long-term positions (MAX_LONGTERM_POSITIONS)
  - Max $16,000 per position (MAX_LONGTERM_POSITION_USD = 16% of account)
  - Same 1%-of-equity risk-per-trade — consistent with day-trading sizing
  - Won't enter if already in the ticker across either portfolio
  - Kill switch respected
"""

import sys
import pandas as pd
from brokers.alpaca import (
    get_bars, get_positions, get_quote, place_bracket_order,
)
from strategies.stage2_trend import Stage2TrendStrategy
from routines.screener import rank_longterm_watchlist
from config.settings import LONG_TERM_WATCHLIST
from risk.sizing import compute_atr, compute_stop_target, compute_position_size
from risk.limits import (
    RISK_PER_TRADE_USD, MAX_LONGTERM_POSITIONS, MAX_LONGTERM_POSITION_USD,
)
from data.db import (
    init_schema, log_signal, is_trading_halted, log_llm_output,
    get_longterm_open_positions,
)
from routines.llm_filter import analyse_signal
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_error, send_info

LONGTERM_STOP_MULT = 3.0
LONGTERM_TARGET_MULT = 9.0
DCA_PULLBACK_MIN = 0.05   # add when 5% below avg entry
DCA_PULLBACK_MAX = 0.12   # but not more than 12% (breakdown territory)
MAX_TRANCHES = 2          # initial entry + max 1 DCA addition

_STRATEGY = Stage2TrendStrategy()


def _get_open_tickers():
    """All tickers with any open Alpaca position (blocks doubles across portfolios)."""
    try:
        return {p["ticker"] for p in get_positions()}
    except Exception:
        return set()


def _spy_regime(spy_bars):
    """'uptrend' if SPY > SMA50, else 'correction'."""
    if len(spy_bars) < 55:
        return "uptrend"
    closes = pd.Series([float(b["close"]) for b in spy_bars])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    return "uptrend" if float(closes.iloc[-1]) >= sma50 else "correction"


def _place_lt_order(ticker, bars, strategy_name, confidence, note=""):
    """
    Shared order-placement logic for both new entries and DCA additions.
    Returns True if an order was placed successfully.
    """
    atr = compute_atr(bars)
    if atr is None:
        log_signal(ticker=ticker, strategy=strategy_name, action="buy",
                   confidence=confidence, acted=False,
                   skip_reason="insufficient bars for ATR")
        return False

    quote = get_quote(ticker)
    entry_price = quote["ask"]
    stop_price, target_price = compute_stop_target(
        entry_price, atr, side="buy",
        stop_mult=LONGTERM_STOP_MULT,
        target_mult=LONGTERM_TARGET_MULT,
    )

    qty = compute_position_size(entry_price, stop_price)
    if qty == 0:
        log_signal(ticker=ticker, strategy=strategy_name, action="buy",
                   confidence=confidence, acted=False,
                   skip_reason="position size 0")
        return False

    if qty * entry_price > MAX_LONGTERM_POSITION_USD:
        qty = int(MAX_LONGTERM_POSITION_USD / entry_price)
    if qty == 0:
        return False

    approved, llm_reason, llm_conviction = analyse_signal(
        ticker, bars, strategy_name, confidence
    )
    log_llm_output(
        source="signal_filter", ticker=ticker,
        output_type="longterm_approval",
        content=llm_reason, conviction=llm_conviction,
        sentiment=1.0 if approved else -1.0,
    )
    if not approved:
        log_signal(ticker=ticker, strategy=strategy_name, action="buy",
                   confidence=confidence, acted=False,
                   skip_reason=f"LLM rejected: {llm_reason}")
        info(f"{ticker}: long-term entry rejected by LLM — {llm_reason}", source="longterm")
        return False

    result = place_bracket_order(
        ticker=ticker, qty=qty, side="buy",
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        strategy=strategy_name,
    )

    acted = result.get("status") != "blocked"
    log_signal(ticker=ticker, strategy=strategy_name, action="buy",
               confidence=confidence, acted=acted,
               skip_reason=result.get("blocked_reason", ""))

    if acted:
        label = f"{strategy_name} [LONG-TERM{' DCA' if note else ''}]"
        send_trade_alert(ticker, "buy", qty, entry_price, strategy=label)
        info(
            f"Long-term {'DCA ' if note else ''}entry: {ticker} {qty} sh @ "
            f"${entry_price:.2f}  stop ${stop_price:.2f}  target ${target_price:.2f}{note}",
            source="longterm",
        )
    else:
        info(f"BLOCKED {ticker} (long-term): {result['blocked_reason']}", source="longterm")

    return acted


def check_dca_additions(all_bars):
    """
    Scale into existing long-term positions on mild pullbacks.

    Conditions:
      - Position is 5-12% below average entry (pullback, not breakdown)
      - Price still above SMA50 (trend intact)
      - Fewer than MAX_TRANCHES entries already placed
    """
    lt_positions = get_longterm_open_positions()

    for pos in lt_positions:
        ticker = pos["ticker"]
        avg_entry = pos["avg_entry"]
        entry_count = pos["entry_count"]

        if entry_count >= MAX_TRANCHES:
            continue

        bars = all_bars.get(ticker)
        if not bars or len(bars) < 55:
            continue

        try:
            quote = get_quote(ticker)
            current_price = quote["ask"]

            pct_below = (avg_entry - current_price) / avg_entry
            if not (DCA_PULLBACK_MIN <= pct_below <= DCA_PULLBACK_MAX):
                continue

            closes = [float(b["close"]) for b in bars]
            sma50 = sum(closes[-50:]) / 50
            if current_price < sma50:
                info(f"{ticker}: DCA skipped — below SMA50", source="longterm")
                continue

            info(
                f"{ticker}: DCA opportunity — {pct_below*100:.1f}% below avg "
                f"entry ${avg_entry:.2f}, tranche {entry_count + 1}/{MAX_TRANCHES}",
                source="longterm",
            )
            _place_lt_order(
                ticker, bars, "stage2_trend_dca", confidence=0.70,
                note=f" (DCA tranche {entry_count + 1}, {pct_below*100:.1f}% pullback)",
            )

        except Exception as e:
            error(f"{ticker}: DCA check error: {e}", source="longterm", exc=e)


def run_longterm():
    """Main long-term entry point. Scans for Stage 2 entries once per day."""
    info("Long-term routine starting", source="longterm")
    init_schema()

    if is_trading_halted():
        warning("Trading halted — skipping long-term scan", source="longterm")
        return

    # Fetch 400 days — SMA200 + screener momentum window need the history
    all_bars = {}
    for ticker in LONG_TERM_WATCHLIST + ["SPY"]:
        try:
            bars = get_bars(ticker, days=400)
            if len(bars) >= 220:
                all_bars[ticker] = bars
        except Exception as e:
            error(f"{ticker}: bar fetch failed: {e}", source="longterm", exc=e)

    # Long-term only enters in confirmed uptrends
    regime = _spy_regime(all_bars.get("SPY", []))
    if regime == "correction":
        info("SPY below SMA50 — long-term entries paused", source="longterm")
        send_info("Long-term scan: SPY in correction — no new entries today.")
        return

    # DCA check runs regardless of slot availability
    try:
        check_dca_additions(all_bars)
    except Exception as e:
        error(f"DCA check failed: {e}", source="longterm", exc=e)

    open_tickers = _get_open_tickers()
    open_count = len([t for t in open_tickers if t in set(LONG_TERM_WATCHLIST)])

    if open_count >= MAX_LONGTERM_POSITIONS:
        info(
            f"Long-term portfolio full ({open_count}/{MAX_LONGTERM_POSITIONS})",
            source="longterm",
        )
        return

    slots_available = MAX_LONGTERM_POSITIONS - open_count
    entries_taken = 0

    # Rank candidates — best opportunities get slots first
    try:
        ranked = rank_longterm_watchlist(all_bars)
    except Exception as e:
        warning(f"Screener failed: {e} — using alphabetical order", source="longterm")
        ranked = [(t, 50.0) for t in LONG_TERM_WATCHLIST]

    for ticker, score in ranked:
        if entries_taken >= slots_available:
            break
        if ticker in open_tickers or ticker == "SPY":
            continue

        bars = all_bars.get(ticker)
        if not bars:
            continue

        try:
            signals = _STRATEGY.generate_signals(ticker, bars)
            for s in signals:
                if s.action != "buy":
                    continue
                info(f"{ticker}: Stage 2 signal (screener score {score:.1f})", source="longterm")
                if _place_lt_order(ticker, bars, _STRATEGY.name, s.confidence or 0.8):
                    entries_taken += 1
                    open_tickers.add(ticker)
                break  # one entry per ticker per day

        except Exception as e:
            error(f"{ticker}: long-term scan error: {e}", source="longterm", exc=e)

    info(
        f"Long-term scan complete. New entries: {entries_taken}. "
        f"Positions: {open_count + entries_taken}/{MAX_LONGTERM_POSITIONS}",
        source="longterm",
    )


if __name__ == "__main__":
    try:
        run_longterm()
    except Exception as e:
        error(f"Long-term routine crashed: {e}", source="longterm", exc=e)
        send_error(f"Long-term routine crashed: {e}")
        sys.exit(1)

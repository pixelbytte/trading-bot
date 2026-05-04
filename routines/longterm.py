"""
Long-term portfolio routine (Day 22).
Runs once per day at market open via longterm.yml.

Scans the LONG_TERM_WATCHLIST for Stage 2 uptrend entries.
Uses wider stops (3x ATR) and larger targets (9x ATR) than the day-trading
routine — long-term positions are held weeks to months, not hours.

Portfolio constraints:
  - Max 3 simultaneous long-term positions (MAX_LONGTERM_POSITIONS)
  - Max $800 per position (MAX_LONGTERM_POSITION_USD)
  - Same $50 risk-per-trade (RISK_PER_TRADE_USD) — consistent risk management
  - Won't enter if already in a position in the same ticker (either portfolio)
  - Kill switch respected

60/40 split: day-trading uses the first 60% of account capital,
long-term uses the remaining 40% ($2,000 of the $5,000 paper account).
"""

import sys
import pandas as pd
from brokers.alpaca import (
    get_bars, get_positions, get_quote, place_bracket_order,
)
from strategies.stage2_trend import Stage2TrendStrategy
from config.settings import LONG_TERM_WATCHLIST
from risk.sizing import compute_atr, compute_stop_target, compute_position_size
from risk.limits import (
    RISK_PER_TRADE_USD, MAX_LONGTERM_POSITIONS, MAX_LONGTERM_POSITION_USD,
)
from data.db import init_schema, log_signal, log_trade, is_trading_halted, log_llm_output
from routines.llm_filter import analyse_signal
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_error, send_info

# Wider ATR multipliers for long-term positions:
# 3x ATR stop gives room to breathe through multi-day swings.
# 9x ATR target = 3:1 reward-risk (better than day-trading's 2:1
# because we're willing to hold longer for bigger moves).
LONGTERM_STOP_MULT = 3.0
LONGTERM_TARGET_MULT = 9.0

_STRATEGY = Stage2TrendStrategy()


def _get_open_longterm_tickers():
    """
    Return the set of tickers with open long-term positions,
    sourced from Alpaca's current position list.
    We cross-reference with what we track as long-term to avoid duplicates.
    Since Alpaca doesn't tag portfolio type, we conservatively block ANY
    existing position in the ticker — prevents doubling across portfolios.
    """
    try:
        positions = get_positions()
        return {p["ticker"] for p in positions}
    except Exception:
        return set()


def _get_spy_regime(spy_bars):
    """Return 'uptrend' if SPY is above its 50-day SMA, else 'correction'."""
    if len(spy_bars) < 55:
        return "uptrend"
    closes = pd.Series([float(b["close"]) for b in spy_bars])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    return "uptrend" if float(closes.iloc[-1]) >= sma50 else "correction"


def run_longterm():
    """Main long-term entry point. Scans for Stage 2 entries once per day."""
    info("Long-term routine starting", source="longterm")

    init_schema()

    if is_trading_halted():
        warning("Trading halted — skipping long-term scan", source="longterm")
        return

    # Fetch 400 days so Stage 2 strategy has enough bars for SMA200 + 52W high
    all_bars = {}
    for ticker in LONG_TERM_WATCHLIST + ["SPY"]:
        try:
            bars = get_bars(ticker, days=400)
            if len(bars) >= 220:
                all_bars[ticker] = bars
        except Exception as e:
            error(f"{ticker}: bar fetch failed: {e}", source="longterm", exc=e)

    # Long-term only runs in confirmed uptrends (Stage 2 stocks need a healthy market)
    regime = _get_spy_regime(all_bars.get("SPY", []))
    if regime == "correction":
        info(
            "SPY below SMA50 — correction mode: long-term entries paused",
            source="longterm",
        )
        send_info("Long-term scan: SPY in correction — no new entries today.")
        return

    # Check how many long-term positions are already open
    open_tickers = _get_open_longterm_tickers()
    open_count = len([t for t in open_tickers if t in set(LONG_TERM_WATCHLIST)])

    if open_count >= MAX_LONGTERM_POSITIONS:
        info(
            f"Long-term portfolio full ({open_count}/{MAX_LONGTERM_POSITIONS} positions)",
            source="longterm",
        )
        return

    slots_available = MAX_LONGTERM_POSITIONS - open_count
    entries_taken = 0

    for ticker, bars in all_bars.items():
        if ticker == "SPY":
            continue
        if ticker in open_tickers:
            continue
        if entries_taken >= slots_available:
            break

        try:
            signals = _STRATEGY.generate_signals(ticker, bars)
            for s in signals:
                if s.action != "buy":
                    continue

                atr = compute_atr(bars)
                if atr is None:
                    log_signal(
                        ticker=ticker, strategy=_STRATEGY.name, action="buy",
                        confidence=s.confidence, acted=False,
                        skip_reason="insufficient bars for ATR",
                    )
                    continue

                quote = get_quote(ticker)
                entry_price = quote["ask"]
                stop_price, target_price = compute_stop_target(
                    entry_price, atr, side="buy",
                    stop_mult=LONGTERM_STOP_MULT,
                    target_mult=LONGTERM_TARGET_MULT,
                )

                # Size position (same $50 risk, but wider stop → fewer shares)
                qty = compute_position_size(entry_price, stop_price)
                if qty == 0:
                    log_signal(
                        ticker=ticker, strategy=_STRATEGY.name, action="buy",
                        confidence=s.confidence, acted=False,
                        skip_reason="position size computed as 0",
                    )
                    continue

                # Hard cap at long-term position limit
                notional = qty * entry_price
                if notional > MAX_LONGTERM_POSITION_USD:
                    qty = int(MAX_LONGTERM_POSITION_USD / entry_price)
                    if qty == 0:
                        continue

                # LLM filter — same quality gate as day-trading
                approved, llm_reason, llm_conviction = analyse_signal(
                    ticker, bars, _STRATEGY.name, s.confidence or 0.5
                )
                log_llm_output(
                    source="signal_filter", ticker=ticker,
                    output_type="longterm_approval",
                    content=llm_reason,
                    conviction=llm_conviction,
                    sentiment=1.0 if approved else -1.0,
                )
                if not approved:
                    log_signal(
                        ticker=ticker, strategy=_STRATEGY.name, action="buy",
                        confidence=s.confidence, acted=False,
                        skip_reason=f"LLM rejected: {llm_reason}",
                    )
                    info(f"{ticker}: long-term entry rejected by LLM — {llm_reason}", source="longterm")
                    continue

                result = place_bracket_order(
                    ticker=ticker, qty=qty, side="buy",
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    strategy=_STRATEGY.name,
                )

                log_signal(
                    ticker=ticker, strategy=_STRATEGY.name, action="buy",
                    confidence=s.confidence,
                    acted=(result.get("status") != "blocked"),
                    skip_reason=result.get("blocked_reason", ""),
                )

                if result.get("status") == "blocked":
                    info(f"BLOCKED {ticker} (long-term): {result['blocked_reason']}", source="longterm")
                else:
                    entries_taken += 1
                    open_tickers.add(ticker)
                    send_trade_alert(
                        ticker, "buy", qty, entry_price,
                        strategy=f"{_STRATEGY.name} [LONG-TERM]",
                    )
                    info(
                        f"Long-term entry: {ticker} {qty} shares @ ${entry_price:.2f}, "
                        f"stop ${stop_price:.2f}, target ${target_price:.2f}",
                        source="longterm",
                    )

        except Exception as e:
            error(f"{ticker}: long-term scan error: {e}", source="longterm", exc=e)

    info(
        f"Long-term scan complete. Entries taken: {entries_taken}. "
        f"Open positions: {open_count + entries_taken}/{MAX_LONGTERM_POSITIONS}",
        source="longterm",
    )


if __name__ == "__main__":
    try:
        run_longterm()
    except Exception as e:
        error(f"Long-term routine crashed: {e}", source="longterm", exc=e)
        send_error(f"Long-term routine crashed: {e}")
        sys.exit(1)

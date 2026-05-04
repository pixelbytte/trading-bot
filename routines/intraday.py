"""
Intraday trading routine.
Runs every 15 minutes during market hours via GitHub Actions.

Live strategies (all backtest-validated on 500-day window):
  - MA+RSI:         +0.427R expectancy, Sharpe 2.58
  - Momentum:       +0.341R expectancy, Sharpe 2.86 (enabled after 500D backtest)
  - Mean Reversion: +0.030R expectancy, Sharpe 0.22 (marginal — watching)

SPY regime gate: in correction (SPY < SMA50), only mean reversion runs.
Portfolio manager prevents doubling up on the same ticker.
"""

import sys
import pandas as pd
from brokers.alpaca import (
    get_bars, get_positions, get_quote,
    place_bracket_order, close_position, update_stop_order,
)
from strategies.ma_rsi import MARSIStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from routines.portfolio import filter_buy_signals
from routines.reconcile import reconcile_exits
from config.settings import WATCHLIST
from risk.sizing import compute_atr, compute_stop_target, compute_position_size
from risk.limits import RISK_PER_TRADE_USD
from data.db import init_schema, log_signal, log_trade, is_trading_halted, get_ticker_sentiments, log_llm_output
from routines.llm_filter import analyse_signal
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_error

# Strategies cleared for live deployment (backed by 500-day backtest)
STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
    MeanReversionStrategy(),
]

# These strategy names are disabled when SPY is in correction
TREND_ONLY_STRATEGIES = {"ma_rsi", "momentum"}


def get_market_regime(spy_bars):
    """
    Return 'uptrend' if SPY is above its 50-day SMA, 'correction' otherwise.
    Falls back to 'uptrend' (permissive) when SPY data is unavailable.
    O'Neil's 'M' principle: only trend-follow in confirmed uptrends.
    """
    if len(spy_bars) < 55:
        return "uptrend"
    closes = pd.Series([float(b["close"]) for b in spy_bars])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    regime = "uptrend" if float(closes.iloc[-1]) >= sma50 else "correction"
    return regime


def check_trailing_stops():
    """
    Inspect all open positions and trail their stops for winning trades.

    Rules (1R = RISK_PER_TRADE_USD = $50):
      +1R: move stop to breakeven (avg entry price)
      +2R: move stop to entry + 1R per share
    """
    positions = get_positions()
    for p in positions:
        ticker = p["ticker"]
        entry = p["avg_entry"]
        unrealized_pl = p["unrealized_pl"]
        qty = p["qty"]

        if qty <= 0:
            continue

        r_per_share = RISK_PER_TRADE_USD / qty

        if unrealized_pl >= 2 * RISK_PER_TRADE_USD:
            new_stop = round(entry + r_per_share, 2)
            info(
                f"{ticker} at +2R (${unrealized_pl:.2f}): trailing stop -> {new_stop:.2f}",
                source="intraday",
            )
            update_stop_order(ticker, new_stop)
        elif unrealized_pl >= RISK_PER_TRADE_USD:
            new_stop = round(entry, 2)
            info(
                f"{ticker} at +1R (${unrealized_pl:.2f}): stop to breakeven {new_stop:.2f}",
                source="intraday",
            )
            update_stop_order(ticker, new_stop)


def run_intraday():
    """Main intraday entry point."""
    info("Intraday routine starting", source="intraday")

    init_schema()

    if is_trading_halted():
        warning("Trading halted - skipping intraday cycle", source="intraday")
        return

    # Reconcile any bracket exits that filled since last cycle — updates pnl in DB
    # so daily_pnl_so_far() and the kill switch see accurate realized losses
    try:
        reconcile_exits()
    except Exception as e:
        error(f"Reconcile failed: {e}", source="intraday", exc=e)

    # Trail stops before scanning for new entries
    try:
        check_trailing_stops()
    except Exception as e:
        error(f"Trailing stop check failed: {e}", source="intraday", exc=e)

    # Fetch 300 calendar days per ticker — needed for 200-day MA computation
    all_bars = {}
    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=300)
            if len(bars) >= 35:
                all_bars[ticker] = bars
        except Exception as e:
            error(f"{ticker}: bar fetch failed: {e}", source="intraday", exc=e)

    # SPY regime gate
    regime = get_market_regime(all_bars.get("SPY", []))
    if regime == "correction":
        info(
            "SPY below SMA50 — correction mode: trend strategies disabled",
            source="intraday",
        )

    # Active strategies for this cycle
    active_strategies = [
        s for s in STRATEGIES
        if regime == "uptrend" or s.name not in TREND_ONLY_STRATEGIES
    ]

    # Current open positions
    try:
        open_positions = get_positions()
        open_tickers = {p["ticker"] for p in open_positions}
    except Exception as e:
        error(f"Failed to fetch positions: {e}", source="intraday", exc=e)
        open_positions = []
        open_tickers = set()

    # Collect signals from all active strategies across all tickers
    buy_candidates = {}   # {ticker: [(strategy_name, Signal), ...]}
    sell_signals = []     # [(strategy, Signal)]

    for ticker, bars in all_bars.items():
        for strat in active_strategies:
            try:
                signals = strat.generate_signals(ticker, bars)
                for s in signals:
                    if s.action == "buy":
                        buy_candidates.setdefault(ticker, []).append((strat.name, s))
                    elif s.action == "sell":
                        sell_signals.append((strat, s))
            except Exception as e:
                error(
                    f"{ticker}/{strat.name}: signal error: {e}",
                    source="intraday", exc=e,
                )

    # Portfolio manager: one trade per ticker, capped at available slots
    to_buy = filter_buy_signals(buy_candidates, open_tickers)

    signals_acted = 0
    signals_skipped = 0

    # Load today's pre-market sentiment scores (empty dict = no scores, fail open)
    try:
        sentiments = get_ticker_sentiments()
    except Exception as e:
        error(f"Could not load sentiment scores: {e}", source="intraday", exc=e)
        sentiments = {}

    # --- Execute buys ---
    for strat_name, s in to_buy:
        ticker = s.ticker
        bars = all_bars[ticker]
        try:
            # Sentiment gate: skip bearish tickers (threshold -0.3)
            ticker_sentiment = sentiments.get(ticker, {}).get("sentiment", 0.0)
            if ticker_sentiment < -0.3:
                log_signal(
                    ticker=ticker, strategy=strat_name, action="buy",
                    confidence=s.confidence, acted=False,
                    skip_reason=f"bearish news sentiment ({ticker_sentiment:.2f})",
                )
                info(f"{ticker}: buy skipped — bearish sentiment {ticker_sentiment:.2f}", source="intraday")
                signals_skipped += 1
                continue

            atr = compute_atr(bars)
            if atr is None:
                info(f"{ticker}: insufficient bars for ATR, skipping", source="intraday")
                signals_skipped += 1
                continue

            quote = get_quote(ticker)
            entry_price = quote["ask"]
            stop_price, target_price = compute_stop_target(entry_price, atr, side="buy")
            qty = compute_position_size(entry_price, stop_price)

            if qty == 0:
                log_signal(
                    ticker=ticker, strategy=strat_name, action="buy",
                    confidence=s.confidence, acted=False,
                    skip_reason="position size computed as 0",
                )
                signals_skipped += 1
                continue

            # LLM signal filter: Claude reviews setup against entry_signals knowledge base
            approved, llm_reason, llm_conviction = analyse_signal(
                ticker, bars, strat_name, s.confidence or 0.5
            )
            log_llm_output(
                source="signal_filter", ticker=ticker,
                output_type="trade_approval",
                content=llm_reason,
                conviction=llm_conviction,
                sentiment=1.0 if approved else -1.0,
            )
            if not approved:
                log_signal(
                    ticker=ticker, strategy=strat_name, action="buy",
                    confidence=s.confidence, acted=False,
                    skip_reason=f"LLM rejected: {llm_reason}",
                )
                info(f"{ticker}: buy rejected by LLM — {llm_reason}", source="intraday")
                signals_skipped += 1
                continue

            result = place_bracket_order(
                ticker=ticker, qty=qty, side="buy",
                entry_price=entry_price, stop_price=stop_price,
                target_price=target_price, strategy=strat_name,
            )

            log_signal(
                ticker=ticker, strategy=strat_name, action="buy",
                confidence=s.confidence,
                acted=(result.get("status") != "blocked"),
                skip_reason=result.get("blocked_reason", ""),
            )

            if result.get("status") == "blocked":
                signals_skipped += 1
                info(f"BLOCKED {ticker}: {result['blocked_reason']}", source="intraday")
            else:
                signals_acted += 1
                send_trade_alert(ticker, "buy", qty, entry_price, strategy=strat_name)

        except Exception as e:
            error(f"{ticker}: buy execution error: {e}", source="intraday", exc=e)

    # --- Execute sells ---
    seen_sell_tickers = set()
    for strat, s in sell_signals:
        ticker = s.ticker
        if ticker not in open_tickers or ticker in seen_sell_tickers:
            if ticker not in open_tickers:
                log_signal(
                    ticker=ticker, strategy=strat.name, action="sell",
                    confidence=s.confidence, acted=False,
                    skip_reason="no position to sell",
                )
            continue

        seen_sell_tickers.add(ticker)
        try:
            pos = next((p for p in open_positions if p["ticker"] == ticker), None)
            if not pos:
                continue

            close_result = close_position(ticker)
            log_trade(
                ticker=ticker, side="sell", qty=float(pos["qty"]),
                price=pos["current_price"], strategy=strat.name,
                order_id=close_result.get("closed_order_id", ""),
                status="submitted", notes="strategy exit",
            )
            log_signal(
                ticker=ticker, strategy=strat.name, action="sell",
                confidence=s.confidence, acted=True,
            )
            signals_acted += 1
            send_trade_alert(
                ticker, "sell", pos["qty"], pos["current_price"],
                strategy=strat.name,
            )
        except Exception as e:
            error(f"{ticker}: sell execution error: {e}", source="intraday", exc=e)

    info(
        f"Intraday cycle complete [{regime}]. "
        f"Strategies: {[s.name for s in active_strategies]}. "
        f"Acted: {signals_acted}, Skipped: {signals_skipped}",
        source="intraday",
    )


if __name__ == "__main__":
    try:
        run_intraday()
    except Exception as e:
        error(f"Intraday routine crashed: {e}", source="intraday", exc=e)
        send_error(f"Intraday routine crashed: {e}")
        sys.exit(1)

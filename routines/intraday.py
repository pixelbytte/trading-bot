"""
Intraday trading routine.
Runs every 15 minutes during market hours via GitHub Actions.

Live strategies (5.5-year backtest validated, full pipeline simulation):
  - MA+RSI:    +0.674R avg, 55.8% win, Profit Factor 2.82
  - Momentum:  +0.566R avg, 53.2% win, Profit Factor 2.41

RS Pullback removed: PF 1.24 on 5.5Y data, 31% win rate in pipeline sim — no real edge.
Mean reversion removed: PF 1.09 over 52 trades — no real edge.

SPY regime gate: both strategies are trend-following, so all pause in correction (SPY < SMA50).
Portfolio manager prevents doubling up on the same ticker.
"""

import sys
import pandas as pd
from brokers.alpaca import (
    get_bars, get_positions, get_quote,
    place_bracket_order, place_market_order, close_position, update_stop_order,
)
from strategies.ma_rsi import MARSIStrategy
from strategies.momentum import MomentumStrategy
from routines.portfolio import filter_buy_signals
from routines.reconcile import reconcile_exits
from config.settings import WATCHLIST, LONG_TERM_WATCHLIST, ACCOUNT_SIZE_USD
from risk.sizing import compute_atr, compute_stop_target, compute_position_size, dynamic_risk_usd
from risk.limits import RISK_PER_TRADE_USD, MAX_DAILY_LOSS_USD
from data.fundamentals import get_fundamentals, has_earnings_soon
from data.db import init_schema, log_signal, log_trade, is_trading_halted, get_ticker_sentiments, log_llm_output, daily_pnl_so_far, get_pyramid_state
from routines.llm_filter import analyse_signal
from routines.premarket import check_breaking_news
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_error, send_info

# Strategies cleared for live deployment (Profit Factor > 2.4 on 5.5-year backtest)
STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
]

# Both are trend-following — disable all in correction (SPY < SMA50)
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


def _maybe_pyramid(ticker: str, n_r: int, unrealized_pl: float):
    """
    Pyramid into a winner (Livermore / Minervini SEPA).

    +1R: add 0.5x of the BASE qty (the original bracket entry size).
    +2R: add 0.25x of the BASE qty.

    Idempotent: reads `pyramid_level` from the DB. Skips if already added at
    this level. Tagged with notes='pyramid_1' / 'pyramid_2' for state tracking.

    Skipped for long-term positions (those run on weekly holds, not R-units).
    """
    if ticker in LONG_TERM_WATCHLIST:
        return  # LT bucket uses different exit logic

    state = get_pyramid_state(ticker)
    if not state:
        return  # no base bracket entry on record

    base_qty = state["base_qty"]
    level = state["pyramid_level"]

    # +1R add (only if not already added)
    if n_r >= 1 and level < 1:
        add_qty = max(1, int(round(base_qty * 0.5)))
        info(
            f"{ticker} PYRAMID +1R: adding {add_qty} shares "
            f"(base={int(base_qty)}, unrealized=${unrealized_pl:.2f})",
            source="intraday",
        )
        try:
            result = place_market_order(
                ticker=ticker, qty=add_qty, side="buy",
                strategy="pyramid", notes="pyramid_1",
            )
            if result.get("status") != "blocked":
                send_trade_alert(ticker, "buy", add_qty,
                                 result.get("filled_avg_price") or 0.0,
                                 strategy="pyramid_1")
        except Exception as e:
            error(f"{ticker}: pyramid_1 failed: {e}", source="intraday", exc=e)

    # +2R add (only if not already added; counts both base and any pyramid_1)
    if n_r >= 2 and level < 2:
        add_qty = max(1, int(round(base_qty * 0.25)))
        info(
            f"{ticker} PYRAMID +2R: adding {add_qty} shares "
            f"(base={int(base_qty)}, unrealized=${unrealized_pl:.2f})",
            source="intraday",
        )
        try:
            result = place_market_order(
                ticker=ticker, qty=add_qty, side="buy",
                strategy="pyramid", notes="pyramid_2",
            )
            if result.get("status") != "blocked":
                send_trade_alert(ticker, "buy", add_qty,
                                 result.get("filled_avg_price") or 0.0,
                                 strategy="pyramid_2")
        except Exception as e:
            error(f"{ticker}: pyramid_2 failed: {e}", source="intraday", exc=e)


def check_trailing_stops():
    """
    Inspect all open positions: trail stops on winners, emergency-close losers,
    and pyramid into confirmed winners (+1R / +2R adds).

    Trailing rule: for every full R gained, ratchet stop up by 1R.
      +1R -> stop at breakeven (0R)
      +2R -> stop at +1R
      +3R -> stop at +2R
      +4R -> stop at +3R  ...and so on

    Pyramid rule (Livermore/Minervini, day-trading only):
      +1R -> add 0.5x base qty
      +2R -> add 0.25x base qty
    Pyramid orders are tagged 'pyramid_N' in the DB so they're idempotent
    across the 15-min cycles.

    Emergency stop (day-trading only):
      -2R: close immediately if bracket stop didn't fill (gap-down protection).
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
        n_r = int(unrealized_pl / RISK_PER_TRADE_USD)  # full R's in profit

        if n_r >= 1:
            # First: pyramid (only adds at clean +1R / +2R thresholds, idempotent)
            _maybe_pyramid(ticker, n_r, unrealized_pl)

            # Then: ratchet trailing stop at (n_r - 1) R above entry
            new_stop = round(entry + max(0, n_r - 1) * r_per_share, 2)
            info(
                f"{ticker} at +{n_r}R (${unrealized_pl:.2f}): trailing stop -> {new_stop:.2f}",
                source="intraday",
            )
            update_stop_order(ticker, new_stop)

        elif unrealized_pl <= -2 * RISK_PER_TRADE_USD and ticker not in LONG_TERM_WATCHLIST:
            # Emergency exit: day-trading position bleeding past 2R.
            warning(
                f"{ticker}: emergency close at -2R (${unrealized_pl:.2f})",
                source="intraday",
            )
            try:
                close_position(ticker)
                send_trade_alert(ticker, "sell", int(qty), entry, strategy="emergency_stop")
            except Exception as e:
                error(f"{ticker}: emergency close failed: {e}", source="intraday", exc=e)


def run_intraday():
    """Main intraday entry point."""
    info("Intraday routine starting", source="intraday")

    init_schema()

    if is_trading_halted():
        warning("Trading halted - skipping intraday cycle", source="intraday")
        return

    # Pre-loss warning: alert Discord when daily P&L crosses 80% of the kill switch
    # threshold so there's a chance to review before trading fully halts.
    try:
        daily_pnl = daily_pnl_so_far()
        warn_level = -MAX_DAILY_LOSS_USD * 0.80   # 80% of daily loss limit
        if daily_pnl <= warn_level:
            send_info(
                f"WARNING: Daily P&L is ${daily_pnl:.2f} — "
                f"approaching kill switch at -${MAX_DAILY_LOSS_USD:.0f}."
            )
    except Exception:
        pass  # never let this block trading

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

    # Relative Strength filter: only buy tickers outperforming SPY over 6 months.
    # 6-month window avoids false negatives from short-term corrections.
    # Leaders beat the market before you buy them, not after.
    spy_bars_rs = all_bars.get("SPY", [])
    if len(spy_bars_rs) >= 126:
        try:
            spy_6m = float(spy_bars_rs[-1]["close"]) / float(spy_bars_rs[-126]["close"]) - 1
            rs_passed = {}
            for ticker, candidates in buy_candidates.items():
                tbars = all_bars.get(ticker, [])
                if len(tbars) >= 126:
                    tick_6m = float(tbars[-1]["close"]) / float(tbars[-126]["close"]) - 1
                    if tick_6m >= spy_6m - 0.05:  # 5% tolerance for early-stage leaders
                        rs_passed[ticker] = candidates
                    else:
                        info(f"{ticker}: RS filter skipped (6M {tick_6m*100:+.1f}% vs SPY {spy_6m*100:+.1f}%)", source="intraday")
                else:
                    rs_passed[ticker] = candidates  # fail open
            buy_candidates = rs_passed
        except Exception:
            pass  # fail open — never block trading on RS computation error

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

            # Fundamental quality gate — skip stocks with declining EPS + revenue
            try:
                f = get_fundamentals(ticker)
                if f is not None:
                    eps_g = f.get("eps_growth", 0.0)
                    rev_g = f.get("revenue_growth", 0.0)
                    if eps_g < -0.20 and rev_g < -0.10:
                        log_signal(
                            ticker=ticker, strategy=strat_name, action="buy",
                            confidence=s.confidence, acted=False,
                            skip_reason=f"declining fundamentals (EPS {eps_g:.0%}, Rev {rev_g:.0%})",
                        )
                        info(f"{ticker}: skipped — declining fundamentals", source="intraday")
                        signals_skipped += 1
                        continue
            except Exception:
                pass  # fail open — never block trading on FMP outage

            # Earnings proximity gate — avoid binary event risk
            try:
                if has_earnings_soon(ticker, days=3):
                    log_signal(
                        ticker=ticker, strategy=strat_name, action="buy",
                        confidence=s.confidence, acted=False,
                        skip_reason="earnings within 3 days",
                    )
                    info(f"{ticker}: skipped — earnings within 3 days", source="intraday")
                    signals_skipped += 1
                    continue
            except Exception:
                pass  # fail open

            # Breaking news gate: re-check for headlines in the last 60 min.
            # Pre-market scan is stale by mid-session; this catches negative
            # news that breaks after the opening scan.
            try:
                is_bearish_now, news_reason = check_breaking_news(ticker, minutes_back=60)
                if is_bearish_now:
                    log_signal(
                        ticker=ticker, strategy=strat_name, action="buy",
                        confidence=s.confidence, acted=False,
                        skip_reason=f"breaking bearish news: {news_reason[:100]}",
                    )
                    info(f"{ticker}: skipped — breaking bearish news in last 60min", source="intraday")
                    signals_skipped += 1
                    continue
            except Exception:
                pass  # fail open — never block trading on news API outage

            atr = compute_atr(bars)
            if atr is None:
                info(f"{ticker}: insufficient bars for ATR, skipping", source="intraday")
                signals_skipped += 1
                continue

            quote = get_quote(ticker)
            entry_price = quote["ask"]
            # Wide target (10R) — trailing stop ratchet handles the actual exit,
            # not a fixed bracket ceiling.
            stop_price, target_price = compute_stop_target(
                entry_price, atr, side="buy", target_mult=10.0
            )
            # LLM signal filter: Claude reviews setup against entry_signals knowledge base
            # Run BEFORE sizing so Kelly multiplier uses the conviction score
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

            # Kelly sizing: scale risk by LLM conviction.
            # High conviction (>=0.8) -> 1.5x, medium -> 1.0x, low (<0.5) -> 0.5x.
            # Concentrates capital into the highest-quality setups.
            if llm_conviction >= 0.80:
                kelly_mult = 1.5
            elif llm_conviction >= 0.50:
                kelly_mult = 1.0
            else:
                kelly_mult = 0.5

            try:
                realized = daily_pnl_so_far()
            except Exception:
                realized = 0.0
            current_equity = ACCOUNT_SIZE_USD + realized
            risk = dynamic_risk_usd(current_equity) * kelly_mult
            qty = compute_position_size(entry_price, stop_price, risk_override=risk)

            if qty == 0:
                log_signal(
                    ticker=ticker, strategy=strat_name, action="buy",
                    confidence=s.confidence, acted=False,
                    skip_reason="position size computed as 0",
                )
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

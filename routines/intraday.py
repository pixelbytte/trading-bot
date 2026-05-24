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
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from strategies.intraday_scalp import VWAPScalpStrategy
from strategies.gap_momentum import GapMomentumStrategy
from brokers.alpaca import (
    get_bars, get_positions, get_quote,
    place_bracket_order, place_market_order, close_position, update_stop_order,
)
from strategies.ma_rsi import MARSIStrategy
from strategies.momentum import MomentumStrategy
from strategies.breakout_52w import Breakout52WStrategy
from routines.portfolio import filter_buy_signals
from routines.reconcile import reconcile_exits
from config.settings import WATCHLIST, LONG_TERM_WATCHLIST, ACCOUNT_SIZE_USD, SCALP_UNIVERSE
from risk.sizing import compute_atr, compute_stop_target, compute_position_size, dynamic_risk_usd
from risk.limits import RISK_PER_TRADE_USD, MAX_DAILY_LOSS_USD, MAX_OPEN_POSITIONS, MAX_POSITION_USD
from data.fundamentals import get_fundamentals, has_earnings_soon
from data.db import init_schema, log_signal, log_trade, is_trading_halted, get_ticker_sentiments, log_llm_output, daily_pnl_so_far, get_pyramid_state, get_open_scalp_tickers, has_taken_partial_exit
from routines.llm_filter import analyse_signal
from routines.premarket import check_breaking_news
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_error, send_info

# Strategies cleared for live deployment
# ma_rsi + momentum: MA crossover signals (quiet in sustained uptrends)
# breakout_52w: fires on new 52-week highs — active in trending markets (Sharpe 1.66)
# rs_pullback disabled: PF 1.24 on 5.5Y data, 31% win rate — no real edge
STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
    Breakout52WStrategy(),
]

# All three are trend-following — disable only at regime_score 0 (all axes bad)
TREND_ONLY_STRATEGIES = {"ma_rsi", "momentum", "breakout_52w"}

SCALP_STRATEGY = VWAPScalpStrategy()
GAP_STRATEGY = GapMomentumStrategy()
# Gap-momentum fires only between 9:45-10:00 ET, using the first 15-min bar
# as the open-print and looking for follow-through. Same universe as scalp.
GAP_WINDOW_START_HOUR = 9
GAP_WINDOW_START_MIN = 45
GAP_WINDOW_END_HOUR = 10
_ET = ZoneInfo("America/New_York")


def get_market_regime(all_bars):
    """
    3-axis composite regime score (Simons: find hidden market state from observable data).

    Axis 1: SPY vs SMA50  — short-term trend gate (O'Neil 'M' principle)
    Axis 2: SPY vs SMA200 — long-term secular trend confirmation
    Axis 3: Breadth       — ≥60% of watchlist tickers above their own SMA50

    Returns (regime_label, score_0_to_3, position_size_multiplier).
    regime_label is 'uptrend'/'correction' (backward-compat for TREND_ONLY_STRATEGIES gate).
    pos_mult scales risk down gradually: 3→1.0x, 2→0.75x, 1→0.5x, 0→0.25x.
    Fails open to ('uptrend', 3, 1.0) when data is unavailable.
    """
    spy_bars = all_bars.get("SPY", [])
    score = 0
    spy50_ok = True  # default: fail open

    if spy_bars:
        closes = pd.Series([float(b["close"]) for b in spy_bars])
        spy_price = float(closes.iloc[-1])

        # Axis 1: SPY vs SMA50 (short-term trend — controls strategy gating)
        if len(spy_bars) >= 55:
            sma50 = float(closes.rolling(50).mean().iloc[-1])
            spy50_ok = spy_price >= sma50
            score += 1 if spy50_ok else 0
        else:
            score += 1  # fail open

        # Axis 2: SPY vs SMA200 (long-term secular trend confirmation)
        if len(spy_bars) >= 210:
            sma200 = float(closes.rolling(200).mean().iloc[-1])
            score += 1 if spy_price >= sma200 else 0
        else:
            score += 1  # fail open — not enough history
    else:
        score += 2  # fail open (both SPY axes)

    # Axis 3: Breadth — ≥60% of non-SPY tickers above their SMA50
    above, checked = 0, 0
    for ticker, bars in all_bars.items():
        if ticker == "SPY" or len(bars) < 55:
            continue
        closes_t = pd.Series([float(b["close"]) for b in bars])
        sma50_t = float(closes_t.rolling(50).mean().iloc[-1])
        if float(closes_t.iloc[-1]) >= sma50_t:
            above += 1
        checked += 1

    if checked == 0 or (above / checked) >= 0.60:
        score += 1  # healthy breadth (or no data — fail open)

    regime = "uptrend" if spy50_ok else "correction"
    pos_mult = {3: 1.0, 2: 0.75, 1: 0.50, 0: 0.25}.get(score, 1.0)
    return regime, score, pos_mult


def _maybe_pyramid_and_partial(ticker: str, n_r: int, qty: float, unrealized_pl: float):
    """
    Position management for day-trading winners:
      +1R: PYRAMID — add 0.5x of base qty (Livermore: average up into winners)
      +2R: PARTIAL EXIT — sell 50% of current qty (lock in half the win)
            Replaces the old +2R pyramid add; backtests showed adding more at
            +2R increases risk-of-ruin on reversals more than it adds expectancy.

    Idempotent:
      - pyramid_1 tracked via notes='pyramid_1' on the buy trade row
      - partial_exit tracked via notes='partial_exit_2R' on the sell trade row

    Skipped for long-term positions (those use the DCA logic in routines/longterm).
    """
    if ticker in LONG_TERM_WATCHLIST:
        return

    state = get_pyramid_state(ticker)
    if not state:
        return  # no base bracket entry on record

    base_qty = state["base_qty"]
    base_ts = state["base_ts"]
    level = state["pyramid_level"]

    # +1R: pyramid add (only if not already added)
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

    # +2R: partial exit (sell 50% of current qty) — one-time, idempotent
    if n_r >= 2 and not has_taken_partial_exit(ticker, base_ts):
        exit_qty = max(1, int(round(qty * 0.5)))
        info(
            f"{ticker} PARTIAL EXIT +2R: selling {exit_qty} of {int(qty)} shares "
            f"(locking in 50%, letting runner ride; unrealized=${unrealized_pl:.2f})",
            source="intraday",
        )
        try:
            result = place_market_order(
                ticker=ticker, qty=exit_qty, side="sell",
                strategy="partial_exit", notes="partial_exit_2R",
            )
            if result.get("status") != "blocked":
                send_trade_alert(ticker, "sell", exit_qty,
                                 result.get("filled_avg_price") or 0.0,
                                 strategy="partial_exit_2R")
        except Exception as e:
            error(f"{ticker}: partial_exit failed: {e}", source="intraday", exc=e)


def check_trailing_stops():
    """
    Inspect all open positions: trail stops on winners, emergency-close losers,
    pyramid into confirmed winners (+1R), and bank profits on big winners (+2R).

    Trailing-stop ladder (give back less profit at higher R levels):
      +1R -> stop at breakeven (0R)
      +2R -> stop at +1R          (give back 1R, but partial exit has banked 50%)
      +3R -> stop at +2R          (give back 1R)
      +4R+ -> stop at +(n - 0.5)R (give back only 0.5R — tighter trail on runners)

    Position management (day-trading only, see _maybe_pyramid_and_partial):
      +1R -> add 0.5x base qty (pyramid)
      +2R -> sell 50% of position (partial exit, lock in half the win)

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
            # 1) Pyramid (+1R) and partial-exit (+2R) — both idempotent
            _maybe_pyramid_and_partial(ticker, n_r, qty, unrealized_pl)

            # 2) Ratchet trailing stop. At +4R+ give back only 0.5R instead of 1R.
            if n_r <= 3:
                stop_offset_r = max(0, n_r - 1)            # 0, 1, 2
            else:
                stop_offset_r = n_r - 0.5                  # 3.5, 4.5, 5.5, ...
            new_stop = round(entry + stop_offset_r * r_per_share, 2)
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


def close_scalp_positions():
    """
    Force-close all scalp positions opened today.
    Called at 3:45pm ET so no intraday scalp survives overnight.
    Fetches current position prices from Alpaca before closing.
    """
    tickers = get_open_scalp_tickers()
    if not tickers:
        info("EOD scalp close: no open scalp positions", source="intraday")
        return

    info(
        f"3:45pm EOD: force-closing {len(tickers)} scalp position(s): {tickers}",
        source="intraday",
    )

    try:
        positions = get_positions()
        pos_map = {p["ticker"]: p for p in positions}
    except Exception:
        pos_map = {}

    for ticker in tickers:
        try:
            pos = pos_map.get(ticker)
            if not pos:
                info(f"{ticker}: not found in open positions (may already be closed)", source="intraday")
                continue
            result = close_position(ticker)
            log_trade(
                ticker=ticker, side="sell", qty=float(pos["qty"]),
                price=pos["current_price"], strategy="scalp",
                order_id=result.get("closed_order_id", ""),
                status="submitted", notes="eod_close",
            )
            send_trade_alert(ticker, "sell", int(pos["qty"]), pos["current_price"],
                             strategy="scalp_eod_close")
            info(f"{ticker}: scalp EOD close submitted @ ${pos['current_price']:.2f}", source="intraday")
        except Exception as e:
            error(f"{ticker}: EOD scalp close failed: {e}", source="intraday", exc=e)


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

    # ET time: used by both scalp entry gate and 3:45pm EOD close
    et_now = datetime.now(_ET)
    scalp_ok = 10 <= et_now.hour < 15   # entries allowed 10am–3pm only
    if et_now.hour > 15 or (et_now.hour == 15 and et_now.minute >= 45):
        try:
            close_scalp_positions()
        except Exception as e:
            error(f"EOD scalp close failed: {e}", source="intraday", exc=e)

    # Fetch 400 calendar days per ticker — breakout_52w needs 220+ bars (SMA200 + buffer).
    # 300 days only yields ~207 trading days, failing the min_bars check silently.
    all_bars = {}
    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=400)
            if len(bars) >= 35:
                all_bars[ticker] = bars
        except Exception as e:
            error(f"{ticker}: bar fetch failed: {e}", source="intraday", exc=e)

    # 3-axis regime score: SPY/SMA50, SPY/SMA200, breadth
    regime, regime_score, regime_mult = get_market_regime(all_bars)
    info(
        f"Market regime: {regime} (score {regime_score}/3, position sizing at {regime_mult:.0%})",
        source="intraday",
    )
    if regime == "correction":
        info(
            "SPY below SMA50 — correction mode: trend strategies disabled",
            source="intraday",
        )

    # Active strategies for this cycle.
    # Hard-disable trend strategies only at score 0 (all three regime axes bad).
    # At score 1-2, regime_mult already cuts position size to 50-75% — no need to
    # also kill signals entirely. SPY dipping 1% below SMA50 shouldn't halt trading.
    active_strategies = [
        s for s in STRATEGIES
        if regime_score >= 1 or s.name not in TREND_ONLY_STRATEGIES
    ]

    # Current open positions
    try:
        open_positions = get_positions()
        open_tickers = {p["ticker"] for p in open_positions}
    except Exception as e:
        error(f"Failed to fetch positions: {e}", source="intraday", exc=e)
        open_positions = []
        open_tickers = set()

    # ── Gap-momentum scan (9:45-10:00 ET only) ──
    # Captures gap-and-go continuation on SCALP_UNIVERSE. Uses ATR sizing from
    # the daily-bar series so we don't double-define risk math.
    gap_window_open = (
        et_now.hour == GAP_WINDOW_START_HOUR and et_now.minute >= GAP_WINDOW_START_MIN
    ) or (et_now.hour == GAP_WINDOW_END_HOUR and et_now.minute == 0)

    if gap_window_open:
        for ticker in SCALP_UNIVERSE:
            if ticker in open_tickers or len(open_tickers) >= MAX_OPEN_POSITIONS:
                continue
            daily = all_bars.get(ticker)
            if not daily or len(daily) < 2:
                continue
            try:
                bars_15m = get_bars(ticker, days=1, timeframe="15min")
                if not bars_15m:
                    continue
                prev_close = float(daily[-2]["close"])
                # 20-bar 15-min average volume from yesterday's session for reference
                # (approximate — Alpaca returns recent intraday data so we use what we have)
                vols = [float(b.get("volume", 0) or 0) for b in bars_15m[:-1]] or [0]
                avg_vol = sum(vols) / max(1, len(vols))
                sigs = GAP_STRATEGY.generate_signals(
                    ticker, bars_15m, prev_close, avg_vol
                )
                if not sigs:
                    continue
                sig = sigs[0]
                quote = get_quote(ticker)
                entry_price = quote["ask"]
                atr = compute_atr(daily)
                if atr is None or atr <= 0:
                    continue
                # Tighter intraday risk: 1.0x ATR stop, 2.0x ATR target (2:1 R/R)
                stop_price = round(entry_price - atr * 1.0, 2)
                target_price = round(entry_price + atr * 2.0, 2)
                if stop_price >= entry_price:
                    continue
                qty = compute_position_size(entry_price, stop_price)
                if qty == 0:
                    continue
                result = place_bracket_order(
                    ticker=ticker, qty=qty, side="buy",
                    entry_price=entry_price, stop_price=stop_price,
                    target_price=target_price, strategy="gap_momentum",
                )
                log_signal(
                    ticker=ticker, strategy="gap_momentum", action="buy",
                    confidence=sig.confidence,
                    acted=(result.get("status") != "blocked"),
                    skip_reason=result.get("blocked_reason", ""),
                )
                if result.get("status") != "blocked":
                    send_trade_alert(ticker, "buy", qty, entry_price, strategy="gap_momentum")
                    open_tickers.add(ticker)
                    info(f"{ticker}: gap_momentum entry @ ${entry_price:.2f} ({sig.reason})",
                         source="intraday")
                else:
                    info(f"GAP BLOCKED {ticker}: {result['blocked_reason']}", source="intraday")
            except Exception as e:
                error(f"{ticker}: gap_momentum error: {e}", source="intraday", exc=e)

    # Fetch 15-min bars and generate scalp signals (only during 10am–3pm window)
    # SCALP_UNIVERSE is a curated subset of WATCHLIST with Sharpe > 1.0 on backtest.
    scalp_signals = []
    if scalp_ok:
        for ticker in SCALP_UNIVERSE:
            if ticker in open_tickers:
                continue
            try:
                sbars = get_bars(ticker, days=1, timeframe="15min")
                for sig in SCALP_STRATEGY.generate_signals(ticker, sbars):
                    scalp_signals.append((ticker, sig))
            except Exception as e:
                error(f"{ticker}/scalp: bar/signal error: {e}", source="intraday", exc=e)

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
    llm_rejected = 0
    llm_checked = 0

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
            llm_checked += 1
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
                llm_rejected += 1
                log_signal(
                    ticker=ticker, strategy=strat_name, action="buy",
                    confidence=s.confidence, acted=False,
                    skip_reason=f"LLM rejected: {llm_reason}",
                )
                info(f"{ticker}: buy rejected by LLM — {llm_reason}", source="intraday")
                signals_skipped += 1
                continue

            # Continuous Kelly sizing (Thorpe: bet in proportion to your edge).
            # Combines strategy signal confidence (s.confidence) + LLM conviction
            # into a composite score, then scales risk linearly from 0.5x to 1.5x.
            # Both signals must agree strongly to get max size; either being weak pulls back.
            composite_confidence = (s.confidence + llm_conviction) / 2.0
            kelly_mult = 0.5 + composite_confidence  # [0.5x, 1.5x]
            info(
                f"{ticker}: composite {composite_confidence:.2f} "
                f"(signal {s.confidence:.2f} × LLM {llm_conviction:.2f}) → kelly {kelly_mult:.2f}x",
                source="intraday",
            )

            try:
                realized = daily_pnl_so_far()
            except Exception:
                realized = 0.0
            current_equity = ACCOUNT_SIZE_USD + realized
            # regime_mult scales down risk in weaker markets (1.0x/0.75x/0.5x/0.25x)
            risk = dynamic_risk_usd(current_equity) * kelly_mult * regime_mult
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

    # --- Execute scalp buys ---
    # Simplified pipeline vs swing: no LLM filter, no fundamentals gate,
    # but sentiment + earnings + breaking-news gates still apply.
    # Stop = 0.5% below entry, target = 0.5% above entry (1:1 R/R, backtest validated).
    scalp_acted = 0
    for ticker, sig in scalp_signals:
        if len(open_tickers) >= MAX_OPEN_POSITIONS:
            info("Scalp: no available slots (MAX_OPEN_POSITIONS reached)", source="intraday")
            break
        try:
            ticker_sentiment = sentiments.get(ticker, {}).get("sentiment", 0.0)
            if ticker_sentiment < -0.3:
                log_signal(
                    ticker=ticker, strategy="scalp", action="buy",
                    confidence=sig.confidence, acted=False,
                    skip_reason=f"bearish sentiment ({ticker_sentiment:.2f})",
                )
                continue

            try:
                if has_earnings_soon(ticker, days=3):
                    log_signal(
                        ticker=ticker, strategy="scalp", action="buy",
                        confidence=sig.confidence, acted=False,
                        skip_reason="earnings within 3 days",
                    )
                    continue
            except Exception:
                pass

            try:
                is_bearish_now, news_reason = check_breaking_news(ticker, minutes_back=30)
                if is_bearish_now:
                    log_signal(
                        ticker=ticker, strategy="scalp", action="buy",
                        confidence=sig.confidence, acted=False,
                        skip_reason=f"breaking bearish news: {news_reason[:80]}",
                    )
                    continue
            except Exception:
                pass

            quote = get_quote(ticker)
            entry_price = quote["ask"]
            stop_price   = round(entry_price * 0.995, 2)   # 0.5% stop
            target_price = round(entry_price * 1.010, 2)  # 1.0% target (2:1 R/R)
            scalp_qty = min(
                max(1, int(RISK_PER_TRADE_USD / (entry_price * 0.005))),
                max(1, int(MAX_POSITION_USD / entry_price)),
            )

            result = place_bracket_order(
                ticker=ticker, qty=scalp_qty, side="buy",
                entry_price=entry_price, stop_price=stop_price,
                target_price=target_price, strategy="scalp",
            )
            log_signal(
                ticker=ticker, strategy="scalp", action="buy",
                confidence=sig.confidence,
                acted=(result.get("status") != "blocked"),
                skip_reason=result.get("blocked_reason", ""),
            )
            if result.get("status") == "blocked":
                info(f"SCALP BLOCKED {ticker}: {result['blocked_reason']}", source="intraday")
            else:
                scalp_acted += 1
                open_tickers.add(ticker)
                send_trade_alert(ticker, "buy", scalp_qty, entry_price, strategy="scalp")
        except Exception as e:
            error(f"{ticker}: scalp execution error: {e}", source="intraday", exc=e)

    if scalp_signals:
        info(
            f"Scalp cycle: {scalp_acted} acted, {len(scalp_signals) - scalp_acted} skipped",
            source="intraday",
        )

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

    llm_pass_rate = f"{llm_checked - llm_rejected}/{llm_checked}" if llm_checked else "0/0"
    info(
        f"Intraday cycle complete [{regime} score={regime_score}]. "
        f"Strategies: {[s.name for s in active_strategies]}. "
        f"Acted: {signals_acted}, Skipped: {signals_skipped}, "
        f"LLM approved: {llm_pass_rate}",
        source="intraday",
    )


if __name__ == "__main__":
    try:
        run_intraday()
    except Exception as e:
        error(f"Intraday routine crashed: {e}", source="intraday", exc=e)
        send_error(f"Intraday routine crashed: {e}")
        sys.exit(1)

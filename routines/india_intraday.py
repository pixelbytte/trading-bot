"""
India intraday routine — NSE equity trading via Upstox.
Runs every 15 minutes during NSE market hours (9:30 AM – 3:15 PM IST).
Scheduled via .github/workflows/india_intraday.yml.

Uses the same strategy stack as the US bot (MA+RSI, Momentum,
Breakout 52W, RS Pullback) — all strategies are price-action based
and work on any market without modification.

Paper mode: set INDIA_PAPER=true (default) to simulate trades with real
NSE data via yfinance. Set INDIA_PAPER=false to go live with Upstox.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from data.db import init_schema, _connect, log_signal
from utils.logger import info, warning, error
from utils.discord import send_info, send_error, send_trade_alert, send_halt

_PAPER = os.getenv("INDIA_PAPER", "true").lower() != "false"

if _PAPER:
    from brokers.upstox_paper import (
        get_bars, get_quote, get_account, get_positions,
        place_bracket_order, close_position, cancel_all_orders,
    )
else:
    from brokers.upstox import (
        get_bars, get_quote, get_account, get_positions,
        place_bracket_order, close_position, cancel_all_orders,
    )
from config.india_settings import (
    NSE_WATCHLIST, REGIME_PROXY,
    ACCOUNT_SIZE_INR, MAX_DAILY_LOSS_INR,
    MAX_OPEN_POSITIONS, MAX_TRADES_PER_DAY,
    SQUAREOFF_HOUR, SQUAREOFF_MINUTE,
    MIN_PRICE_INR, MIN_AVG_VOLUME,
)
from risk.india_limits import RISK_PER_TRADE_INR, PRE_LOSS_WARNING_INR

from strategies.ma_rsi import MARSIStrategy
from strategies.momentum import MomentumStrategy
from strategies.breakout_52w import Breakout52WStrategy
from strategies.rs_pullback import RSPullbackStrategy
from risk.sizing import compute_atr, compute_stop_target, compute_position_size

IST = ZoneInfo("Asia/Kolkata")

STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
    Breakout52WStrategy(),
    RSPullbackStrategy(),
]
TREND_ONLY = {"ma_rsi", "momentum", "breakout_52w", "rs_pullback"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ist_now() -> datetime:
    return datetime.now(IST)


def _is_near_squareoff() -> bool:
    now = _ist_now()
    return (now.hour > SQUAREOFF_HOUR or
            (now.hour == SQUAREOFF_HOUR and now.minute >= SQUAREOFF_MINUTE))


def _daily_pnl(con) -> float:
    row = con.execute("""
        SELECT COALESCE(SUM(pnl), 0) FROM trades
        WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
          AND portfolio_type = 'india'
    """).fetchone()
    return float(row[0]) if row else 0.0


def _trades_today(con) -> int:
    row = con.execute("""
        SELECT COUNT(*) FROM trades
        WHERE DATE(ts) = CURRENT_DATE AND portfolio_type = 'india'
    """).fetchone()
    return int(row[0]) if row else 0


def _is_halted(con) -> bool:
    row = con.execute("""
        SELECT value FROM kill_switch
        WHERE date = CURRENT_DATE
    """).fetchone()
    return bool(row and row[0])


def _get_regime(all_bars: dict) -> tuple[str, float]:
    """Simple regime: use NIFTYBEES (Nifty 50 ETF) as SPY proxy."""
    proxy_bars = all_bars.get(REGIME_PROXY, [])
    if not proxy_bars or len(proxy_bars) < 55:
        return "uptrend", 1.0

    import pandas as pd
    closes = pd.Series([float(b["close"]) for b in proxy_bars])
    price = float(closes.iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1])

    if price >= sma50:
        return "uptrend", 1.0
    return "correction", 0.5


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

def run_india_intraday():
    mode = "PAPER" if _PAPER else "LIVE"
    info(f"India intraday starting [{mode}]", source="india_intraday")
    init_schema()
    con = _connect()

    try:
        # Kill switch check
        if _is_halted(con):
            info("Kill switch active — skipping", source="india_intraday")
            return

        daily_pnl = _daily_pnl(con)
        trades_done = _trades_today(con)

        # Daily loss kill switch
        if daily_pnl <= -MAX_DAILY_LOSS_INR:
            con.execute("""
                INSERT INTO kill_switch (date, value, reason)
                VALUES (CURRENT_DATE, TRUE, 'india daily loss limit')
                ON CONFLICT (date) DO UPDATE SET value = TRUE
            """)
            send_halt(f"India: Daily loss ₹{abs(daily_pnl):,.0f} hit ₹{MAX_DAILY_LOSS_INR:,.0f} limit")
            return

        # Pre-loss warning
        if daily_pnl <= -PRE_LOSS_WARNING_INR:
            send_info(f"India WARNING: Down ₹{abs(daily_pnl):,.0f} today "
                      f"(₹{MAX_DAILY_LOSS_INR - abs(daily_pnl):,.0f} left before halt)")

        # Squareoff check — close all MIS positions before 3:15 PM IST
        if _is_near_squareoff():
            positions = get_positions()
            mis_positions = [p for p in positions if p.get("product") == "I"]
            for pos in mis_positions:
                info(f"Squareoff: closing {pos['ticker']} MIS position", source="india_intraday")
                close_position(pos["ticker"])
            if mis_positions:
                send_info(f"India: Squared off {len(mis_positions)} MIS positions before close")
            return

        # Trade limit
        if trades_done >= MAX_TRADES_PER_DAY:
            info(f"Trade limit reached ({trades_done})", source="india_intraday")
            return

        account = get_account()
        current_equity = account["equity"] or ACCOUNT_SIZE_INR
        positions = get_positions()
        open_count = len(positions)

        if open_count >= MAX_OPEN_POSITIONS:
            info(f"Max positions open ({open_count})", source="india_intraday")
            return

        # Fetch bars for all watchlist tickers + regime proxy
        all_bars = {}
        fetch_tickers = NSE_WATCHLIST + [REGIME_PROXY]
        for ticker in fetch_tickers:
            try:
                bars = get_bars(ticker, days=400)
                if len(bars) >= 35:
                    all_bars[ticker] = bars
            except Exception as e:
                warning(f"Bar fetch failed for {ticker}: {e}", source="india_intraday")

        regime, regime_mult = _get_regime(all_bars)
        info(f"Regime: {regime} (mult={regime_mult})", source="india_intraday")

        held_tickers = {p["ticker"] for p in positions}

        # Scan watchlist for signals
        for ticker in NSE_WATCHLIST:
            if ticker in held_tickers:
                continue
            if open_count >= MAX_OPEN_POSITIONS:
                break

            bars = all_bars.get(ticker)
            if not bars:
                continue

            # Basic quality filters
            price = float(bars[-1]["close"])
            if price < MIN_PRICE_INR:
                continue

            import pandas as pd
            volumes = pd.Series([float(b["volume"]) for b in bars])
            avg_vol = float(volumes.tail(20).mean())
            if avg_vol < MIN_AVG_VOLUME:
                continue

            # Run strategies
            for strat in STRATEGIES:
                if regime == "correction" and strat.name in TREND_ONLY:
                    continue

                try:
                    signals = strat.generate_signals(ticker, bars)
                except Exception as e:
                    warning(f"Strategy {strat.name} failed on {ticker}: {e}",
                            source="india_intraday")
                    continue

                for sig in signals:
                    if sig.action != "buy":
                        continue

                    # Risk sizing — compute ATR first, then stop/target
                    atr = compute_atr(bars)
                    if atr is None or atr <= 0:
                        continue
                    stop, target = compute_stop_target(price, atr)
                    if stop >= price:
                        continue

                    risk_inr = RISK_PER_TRADE_INR * regime_mult
                    # pass risk_override so position size scales to INR amounts
                    qty = compute_position_size(price, stop, risk_override=risk_inr)
                    if qty <= 0:
                        # compute_position_size caps at MAX_ORDER_QTY=100; re-size manually
                        stop_dist = abs(price - stop)
                        qty = max(1, int(risk_inr / stop_dist))

                    from config.india_settings import MAX_POSITION_INR
                    if qty * price > MAX_POSITION_INR:
                        qty = int(MAX_POSITION_INR / price)

                    if qty <= 0:
                        continue

                    # Log signal (skip_reason is empty — signal was acted on)
                    log_signal(ticker, strat.name, "buy", acted=True,
                               confidence=sig.confidence)

                    info(f"Signal: {strat.name} {ticker} @ ₹{price:.2f} "
                         f"qty={qty} SL=₹{stop:.2f} TGT=₹{target:.2f}",
                         source="india_intraday")

                    # Place order
                    result = place_bracket_order(ticker, qty, price, stop, target)
                    if result:
                        send_trade_alert(ticker, "buy", qty, price, strat.name)
                        open_count += 1
                        trades_done += 1
                        held_tickers.add(ticker)
                    break  # one strategy per ticker per cycle

    finally:
        con.close()

    info("India intraday complete", source="india_intraday")


if __name__ == "__main__":
    try:
        run_india_intraday()
    except Exception as e:
        error(f"India intraday crashed: {e}", source="india_intraday", exc=e)
        send_error(f"India intraday crashed: {e}")
        sys.exit(1)

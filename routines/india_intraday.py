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
        simulate_bracket_exits,
    )
else:
    from brokers.upstox import (
        get_bars, get_quote, get_account, get_positions,
        place_bracket_order, close_position, cancel_all_orders,
    )
    def simulate_bracket_exits():  # no-op in live mode (broker handles exits)
        return 0
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
from strategies.nse_oversold_bounce import NSEOversoldBounceStrategy
from strategies.india_orb import IndiaORBStrategy
from risk.sizing import compute_atr, compute_stop_target, compute_position_size

IST = ZoneInfo("Asia/Kolkata")

# Backtest results (2026-05-23, 500-day NSE walk-forward):
#   MA+RSI:             +0.076R, Sharpe 0.61  keep
#   Momentum:           +0.052R, Sharpe 0.66  keep
#   NSEOversoldBounce:  new strategy added 2026-05-23
#   Breakout52w:        -0.079R, Sharpe -0.76 disabled
#   RSPullback:         -0.126R, Sharpe -0.75 disabled
STRATEGIES = [
    MARSIStrategy(),
    MomentumStrategy(),
    NSEOversoldBounceStrategy(),
]
TREND_ONLY = {"ma_rsi", "momentum"}  # nse_oversold_bounce runs in all regimes

# Opening Range Breakout — runs only on liquid NSE banks/finance, after 9:45 IST.
# Separate from daily-bar strategies above because it needs 15-min intraday data.
ORB_STRATEGY = IndiaORBStrategy()
ORB_UNIVERSE = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "BAJFINANCE"]
ORB_OPEN_HOUR = 9
ORB_OPEN_MIN = 45  # opening range completes at 9:45 IST (2 × 15-min bars)


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


def _hydrate_from_dashboard(con) -> int:
    """
    Reload India trades from docs/data.json into the DB. Makes the paper bot
    stateful across GitHub Actions runs even though bot.db is wiped on cache
    misses. Idempotent: skips trades that already exist in the DB.
    Returns the number of trades inserted.
    """
    import json
    from pathlib import Path
    data_path = Path(__file__).parent.parent / "docs" / "data.json"
    if not data_path.exists():
        return 0
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    trades = data.get("india", {}).get("recent_trades", [])
    if not trades:
        return 0

    inserted = 0
    for t in trades:
        try:
            existing = con.execute("""
                SELECT 1 FROM trades
                WHERE ts = ? AND ticker = ? AND side = ? AND qty = ?
                  AND portfolio_type IN ('india_paper', 'india')
                LIMIT 1
            """, [t["ts"], t["ticker"], t["side"], float(t["qty"])]).fetchone()
            if existing:
                continue
            con.execute("""
                INSERT INTO trades (ts, ticker, side, qty, price, strategy,
                                    status, pnl, portfolio_type, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'india_paper', ?)
            """, [
                t["ts"], t["ticker"], t["side"], float(t["qty"]), float(t["price"]),
                t.get("strategy") or "", t.get("status") or "submitted",
                t.get("pnl"), t.get("notes") or "restored",
            ])
            inserted += 1
        except Exception:
            continue
    return inserted


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
        # Rebuild paper state from the committed dashboard JSON. Necessary because
        # GitHub Actions wipes bot.db on cache miss, which would otherwise cause
        # the bot to re-enter positions it already holds.
        if _PAPER:
            restored = _hydrate_from_dashboard(con)
            if restored:
                info(f"Hydrated {restored} India trade(s) from dashboard JSON",
                     source="india_intraday")

        # Simulate bracket exits — checks today's intraday H/L against the SL/TGT
        # of every open paper position and closes any that were touched. In live
        # mode this is a no-op (the broker fills brackets server-side).
        try:
            closed = simulate_bracket_exits()
            if closed:
                info(f"Bracket simulator closed {closed} position(s)",
                     source="india_intraday")
        except Exception as e:
            warning(f"Bracket simulator failed: {e}", source="india_intraday")

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

        # Squareoff check — close all intraday positions before 3:15 PM IST.
        # In live mode we only close MIS (product='I'). In paper mode we close
        # everything since paper positions don't carry a real product type.
        if _is_near_squareoff():
            positions = get_positions()
            if _PAPER:
                to_close = positions  # close all paper positions at EOD
            else:
                to_close = [p for p in positions if p.get("product") == "I"]
            for pos in to_close:
                info(f"Squareoff: closing {pos['ticker']} position", source="india_intraday")
                close_position(pos["ticker"])
            if to_close:
                send_info(f"India: Squared off {len(to_close)} position(s) before close")
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

        # ── Opening Range Breakout (banking subset, intraday 15-min bars) ──
        # Fires only after the OR has completed (9:45 IST). Uses tighter stops
        # (1.0xATR) because it's an intraday scalp with same-day MIS exits.
        now_ist = _ist_now()
        orb_window_open = (
            now_ist.hour > ORB_OPEN_HOUR
            or (now_ist.hour == ORB_OPEN_HOUR and now_ist.minute >= ORB_OPEN_MIN)
        )
        if orb_window_open and open_count < MAX_OPEN_POSITIONS:
            today_str = now_ist.strftime("%Y-%m-%d")
            for ticker in ORB_UNIVERSE:
                if ticker in held_tickers or open_count >= MAX_OPEN_POSITIONS:
                    continue
                try:
                    bars_15m = get_bars(ticker, days=2, timeframe="15min")
                    today_bars = [b for b in bars_15m if today_str in str(b.get("ts", ""))]
                    if len(today_bars) < 3:
                        continue
                    signals = ORB_STRATEGY.generate_signals(ticker, today_bars)
                    if not signals:
                        continue
                    sig = signals[0]
                    price = float(today_bars[-1]["close"])
                    if price < MIN_PRICE_INR:
                        continue

                    # Use opening range low as the stop (tighter than ATR for scalp)
                    or_low = min(float(b["low"]) for b in today_bars[:2])
                    stop = or_low
                    if stop >= price:
                        continue
                    target = price + (price - stop) * 2.0  # 2:1 R/R

                    risk_inr = RISK_PER_TRADE_INR * regime_mult * 0.5  # half-size scalp
                    qty = compute_position_size(price, stop, risk_override=risk_inr)
                    if qty <= 0:
                        stop_dist = abs(price - stop)
                        qty = max(1, int(risk_inr / stop_dist)) if stop_dist > 0 else 0

                    from config.india_settings import MAX_POSITION_INR
                    if qty * price > MAX_POSITION_INR:
                        qty = int(MAX_POSITION_INR / price)
                    if qty <= 0:
                        continue

                    log_signal(ticker, ORB_STRATEGY.name, "buy",
                               acted=True, confidence=sig.confidence)
                    info(
                        f"ORB Signal: {ticker} @ ₹{price:.2f} qty={qty} "
                        f"SL=₹{stop:.2f} TGT=₹{target:.2f} ({sig.reason})",
                        source="india_intraday",
                    )
                    result = place_bracket_order(ticker, qty, price, stop, target)
                    if result:
                        send_trade_alert(ticker, "buy", qty, price, ORB_STRATEGY.name)
                        open_count += 1
                        trades_done += 1
                        held_tickers.add(ticker)
                except Exception as e:
                    warning(f"ORB scan failed for {ticker}: {e}", source="india_intraday")

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

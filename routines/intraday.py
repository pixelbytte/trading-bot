"""
Intraday trading routine.
Runs every 15 minutes during market hours via GitHub Actions.
For each ticker in watchlist: gets bars, runs strategy, places trade if signal.
"""

import sys
from brokers.alpaca import (
    get_bars, get_positions, get_quote,
    place_bracket_order, close_position, update_stop_order,
)
from strategies.ma_rsi import MARSIStrategy
from config.settings import WATCHLIST
from risk.sizing import compute_atr, compute_stop_target, compute_position_size
from risk.limits import RISK_PER_TRADE_USD
from data.db import init_schema, log_signal, log_trade, is_trading_halted
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_info


def check_trailing_stops():
    """
    Inspect all open positions and trail their stops for winning trades.

    Rules (1R = RISK_PER_TRADE_USD = $50):
      - At unrealized P&L >= +1R: move stop to breakeven (avg entry price)
      - At unrealized P&L >= +2R: move stop to entry + 1R per share
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
                f"{ticker} at +2R (${unrealized_pl:.2f}): trailing stop → {new_stop:.2f}",
                source="intraday",
            )
            update_stop_order(ticker, new_stop)
        elif unrealized_pl >= RISK_PER_TRADE_USD:
            new_stop = round(entry, 2)
            info(
                f"{ticker} at +1R (${unrealized_pl:.2f}): moving stop to breakeven {new_stop:.2f}",
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

    # Trail stops before scanning for new entries
    try:
        check_trailing_stops()
    except Exception as e:
        error(f"Trailing stop check failed: {e}", source="intraday", exc=e)

    strategy = MARSIStrategy()
    signals_acted = 0
    signals_skipped = 0

    for ticker in WATCHLIST:
        try:
            bars = get_bars(ticker, days=90)
            if len(bars) < 35:
                continue

            signals = strategy.generate_signals(ticker, bars)

            for s in signals:
                if s.action == "buy":
                    atr = compute_atr(bars)
                    if atr is None:
                        info(f"{ticker}: not enough bars for ATR, skipping", source="intraday")
                        continue

                    quote = get_quote(ticker)
                    entry_price = quote["ask"]
                    stop_price, target_price = compute_stop_target(entry_price, atr, side="buy")
                    qty = compute_position_size(entry_price, stop_price)

                    if qty == 0:
                        log_signal(
                            ticker=s.ticker,
                            strategy=strategy.name,
                            action=s.action,
                            confidence=s.confidence,
                            acted=False,
                            skip_reason="position size computed as 0",
                        )
                        signals_skipped += 1
                        continue

                    result = place_bracket_order(
                        ticker=ticker,
                        qty=qty,
                        side="buy",
                        entry_price=entry_price,
                        stop_price=stop_price,
                        target_price=target_price,
                        strategy=strategy.name,
                    )

                    log_signal(
                        ticker=s.ticker,
                        strategy=strategy.name,
                        action=s.action,
                        confidence=s.confidence,
                        acted=(result.get("status") != "blocked"),
                        skip_reason=result.get("blocked_reason", ""),
                    )

                    if result.get("status") == "blocked":
                        signals_skipped += 1
                        info(f"BLOCKED {s.ticker}: {result['blocked_reason']}", source="intraday")
                    else:
                        signals_acted += 1
                        send_trade_alert(s.ticker, "buy", qty, entry_price, strategy=strategy.name)

                elif s.action == "sell":
                    # Close the position — this also cancels any open bracket legs
                    positions = get_positions()
                    pos = next((p for p in positions if p["ticker"] == s.ticker), None)
                    if pos:
                        close_result = close_position(s.ticker)
                        log_trade(
                            ticker=s.ticker,
                            side="sell",
                            qty=float(pos["qty"]),
                            price=pos["current_price"],
                            strategy=strategy.name,
                            order_id=close_result.get("closed_order_id", ""),
                            status="submitted",
                            notes="strategy exit",
                        )
                        log_signal(
                            ticker=s.ticker,
                            strategy=strategy.name,
                            action=s.action,
                            confidence=s.confidence,
                            acted=True,
                        )
                        signals_acted += 1
                        send_trade_alert(
                            s.ticker, "sell",
                            pos["qty"], pos["current_price"],
                            strategy=strategy.name,
                        )
                    else:
                        log_signal(
                            ticker=s.ticker,
                            strategy=strategy.name,
                            action=s.action,
                            confidence=s.confidence,
                            acted=False,
                            skip_reason="no position to sell",
                        )

        except Exception as e:
            error(f"{ticker}: {e}", source="intraday", exc=e)

    info(
        f"Intraday cycle complete. Acted: {signals_acted}, Skipped: {signals_skipped}",
        source="intraday",
    )


if __name__ == "__main__":
    try:
        run_intraday()
    except Exception as e:
        error(f"Intraday routine crashed: {e}", source="intraday", exc=e)
        sys.exit(1)

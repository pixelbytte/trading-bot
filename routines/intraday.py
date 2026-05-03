"""
Intraday trading routine.
Runs every 15 minutes during market hours via GitHub Actions.
For each ticker in watchlist: gets bars, runs strategy, places trade if signal.
"""

import sys
from datetime import datetime
from brokers.alpaca import get_bars, place_market_order
from strategies.ma_rsi import MARSIStrategy
from config.settings import WATCHLIST, RISK_PER_TRADE_USD
from data.db import init_schema, log_signal, is_trading_halted
from utils.logger import info, warning, error
from utils.discord import send_trade_alert, send_info


def run_intraday():
    """Main intraday entry point."""
    info("Intraday routine starting", source="intraday")

    # Make sure DB schema is up to date (no-op if already created)
    init_schema()

    # Hard kill switch check before anything else
    if is_trading_halted():
        warning("Trading halted - skipping intraday cycle", source="intraday")
        return

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
                # Position size: $RISK_PER_TRADE / current price (rounded down)
                price = bars[-1]["close"]
                qty = max(1, int(RISK_PER_TRADE_USD * 5 / price))  # ~5x risk for position size

                if s.action == "buy":
                    result = place_market_order(
                        ticker=ticker,
                        qty=qty,
                        side="buy",
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
                        send_trade_alert(s.ticker, "buy", qty, price, strategy=strategy.name)

                elif s.action == "sell":
                    # Only sell if we have a position
                    from brokers.alpaca import get_positions
                    positions = get_positions()
                    has_position = any(p["ticker"] == s.ticker for p in positions)
                    if has_position:
                        result = place_market_order(
                            ticker=ticker,
                            qty=qty,
                            side="sell",
                            strategy=strategy.name,
                        )
                        log_signal(
                            ticker=s.ticker,
                            strategy=strategy.name,
                            action=s.action,
                            confidence=s.confidence,
                            acted=True,
                        )
                        signals_acted += 1
                        send_trade_alert(s.ticker, "sell", qty, price, strategy=strategy.name)
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
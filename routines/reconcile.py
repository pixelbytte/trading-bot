"""
Position lifecycle reconciler (Day 12).

Runs at the start of every intraday cycle and again at EOD.
Detects bracket orders whose stop or target leg has filled, then records
the realized P&L on the original entry row in the trades table.

Why this matters:
  - daily_pnl_so_far() only sums rows where pnl IS NOT NULL.
  - Without reconciling, the kill switch never sees realized losses,
    so the daily loss limit can't protect us correctly.
  - EOD Discord summary would show $0 realized P&L even on active days.

Logic:
  1. Pull all buy-side trades with pnl=NULL (open entries) from the last 30 days.
  2. Pull current Alpaca positions.
  3. Any ticker in DB that is NOT in current positions has been closed.
  4. Query Alpaca for the most recent filled SELL order for that ticker.
  5. Compute realized P&L and update the DB row.
"""

from brokers.alpaca import get_positions, get_recent_orders
from data.db import get_open_trade_entries, update_trade_pnl
from utils.logger import info, warning, error as log_error


def reconcile_exits():
    """
    Scan for bracket exits that haven't been recorded in the DB yet.
    Safe to call multiple times — already-reconciled rows (pnl IS NOT NULL)
    are excluded by get_open_trade_entries().
    """
    open_entries = get_open_trade_entries()
    if not open_entries:
        return

    try:
        current_positions = {p["ticker"] for p in get_positions()}
    except Exception as e:
        log_error(f"reconcile: cannot fetch positions: {e}", source="reconcile", exc=e)
        return

    for entry in open_entries:
        ticker = entry["ticker"]
        if ticker in current_positions:
            continue  # position still open — nothing to reconcile yet

        # Position is gone — find the fill that closed it
        try:
            filled_sells = get_recent_orders(ticker=ticker, side="sell", limit=10)
        except Exception as e:
            log_error(
                f"reconcile: cannot fetch orders for {ticker}: {e}",
                source="reconcile", exc=e,
            )
            continue

        if not filled_sells:
            warning(
                f"reconcile: {ticker} position closed but no filled sell order found",
                source="reconcile",
            )
            continue

        # Most recent filled sell is the exit
        exit_order = filled_sells[0]
        exit_price = exit_order["fill_price"]
        entry_price = entry["price"]
        qty = entry["qty"]

        pnl = (exit_price - entry_price) * qty
        outcome = "target" if exit_price >= entry_price else "stop"

        try:
            update_trade_pnl(
                trade_id=entry["id"],
                exit_price=exit_price,
                pnl=pnl,
                notes=f"bracket {outcome}",
            )
            info(
                f"{ticker}: exit reconciled — {outcome} @ ${exit_price:.2f}, "
                f"P&L ${pnl:+.2f}",
                source="reconcile",
            )
        except Exception as e:
            log_error(
                f"reconcile: failed to update trade {entry['id']} for {ticker}: {e}",
                source="reconcile", exc=e,
            )

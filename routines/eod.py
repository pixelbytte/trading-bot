"""
End-of-day routine.
Runs at market close (~4:30pm ET).
Reconciles positions, computes daily P&L, sends Discord summary.
"""

import sys
from datetime import datetime, date
from brokers.alpaca import get_account, get_positions
from data.db import init_schema, _connect
from utils.logger import info, error
from utils.discord import send_daily_pnl, send_info


def run_eod():
    """End-of-day reconciliation and reporting."""
    info("EOD routine starting", source="eod")

    init_schema()

    # Pull current account state
    account = get_account()
    positions = get_positions()

    # Compute today's stats from the database
    con = _connect()
    try:
        # Trades placed today
        trades_today = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE DATE(ts) = CURRENT_DATE
        """).fetchone()[0]

        # Winning vs losing closed trades today
        wins = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl > 0
        """).fetchone()[0]

        losses = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl < 0
        """).fetchone()[0]

        # Realized P&L today
        realized_pnl = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
        """).fetchone()[0]

        # Unrealized P&L (open positions)
        unrealized = sum(p["unrealized_pl"] for p in positions)

        total_pnl = float(realized_pnl) + float(unrealized)

        # Win rate (only counts closed trades)
        closed = wins + losses
        win_rate = (wins / closed) if closed > 0 else 0.0

        # Persist to daily_pnl table (upsert today's row)
        con.execute("""
            INSERT INTO daily_pnl (date, pnl, num_trades, wins, losses, portfolio_type)
            VALUES (CURRENT_DATE, ?, ?, ?, ?, 'all')
            ON CONFLICT (date) DO UPDATE SET
                pnl = excluded.pnl,
                num_trades = excluded.num_trades,
                wins = excluded.wins,
                losses = excluded.losses
        """, [total_pnl, trades_today, wins, losses])
    finally:
        con.close()

    # Build Discord summary
    info(f"Account equity: ${account['equity']:,.2f}", source="eod")
    info(f"Open positions: {len(positions)}", source="eod")
    info(f"Trades today: {trades_today}", source="eod")
    info(f"Realized P&L: ${realized_pnl:.2f}", source="eod")
    info(f"Unrealized P&L: ${unrealized:.2f}", source="eod")
    info(f"Total P&L: ${total_pnl:.2f}", source="eod")

    send_daily_pnl(total_pnl, trades_today, win_rate)

    # If we have open positions, list them
    if positions:
        lines = ["**Open positions:**"]
        for p in positions:
            pl = p["unrealized_pl"]
            arrow = "🟢" if pl >= 0 else "🔴"
            lines.append(
                f"{arrow} {p['ticker']}: {p['qty']:.0f} shares @ "
                f"${p['avg_entry']:.2f}, P&L ${pl:+.2f}"
            )
        send_info("\n".join(lines))

    info("EOD routine complete", source="eod")


if __name__ == "__main__":
    try:
        run_eod()
    except Exception as e:
        error(f"EOD routine crashed: {e}", source="eod", exc=e)
        sys.exit(1)
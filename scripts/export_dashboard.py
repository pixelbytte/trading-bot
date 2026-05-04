"""
Export trading data from DuckDB to docs/data.json for the GitHub Pages dashboard.

Run at EOD after reconcile_exits() so P&L is accurate.
Merges today's DB data with the existing JSON history so daily_history
accumulates across runs even though bot.db is ephemeral on Actions.
"""

import json
from pathlib import Path
from datetime import date, datetime
from data.db import init_schema, _connect

DOCS_DIR = Path(__file__).parent.parent / "docs"
OUTPUT = DOCS_DIR / "data.json"


def _load_existing():
    if OUTPUT.exists():
        try:
            return json.loads(OUTPUT.read_text())
        except Exception:
            pass
    return {"daily_history": []}


def export():
    init_schema()
    con = _connect()
    try:
        today_str = date.today().isoformat()

        trades_today = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE
        """).fetchone()[0]

        realized_pnl = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
        """).fetchone()[0]

        wins = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE AND pnl > 0
        """).fetchone()[0]

        losses = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE AND pnl < 0
        """).fetchone()[0]

        rows = con.execute("""
            SELECT ts, ticker, side, qty, price, strategy, status, pnl
            FROM trades ORDER BY ts DESC LIMIT 50
        """).fetchall()
        recent_trades = [
            {
                "ts": str(r[0]),
                "ticker": r[1],
                "side": r[2],
                "qty": float(r[3]),
                "price": float(r[4]),
                "strategy": r[5] or "",
                "status": r[6] or "",
                "pnl": float(r[7]) if r[7] is not None else None,
            }
            for r in rows
        ]

        signal_rows = con.execute("""
            SELECT ts, ticker, strategy, action, confidence, acted, skip_reason
            FROM signals ORDER BY ts DESC LIMIT 30
        """).fetchall()
        recent_signals = [
            {
                "ts": str(r[0]),
                "ticker": r[1],
                "strategy": r[2],
                "action": r[3],
                "confidence": float(r[4]) if r[4] is not None else None,
                "acted": bool(r[5]),
                "skip_reason": r[6] or "",
            }
            for r in signal_rows
        ]

    finally:
        con.close()

    # Merge today's row into existing daily_history
    existing = _load_existing()
    history = [d for d in existing.get("daily_history", []) if d["date"] != today_str]
    closed = wins + losses
    history.insert(0, {
        "date": today_str,
        "pnl": float(realized_pnl),
        "num_trades": trades_today,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / closed if closed > 0 else 0.0,
    })
    history = history[:60]  # keep rolling 60 days

    payload = {
        "generated_at": datetime.now().isoformat(),
        "today": today_str,
        "summary": {
            "trades_today": trades_today,
            "realized_pnl": float(realized_pnl),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / closed if closed > 0 else 0.0,
        },
        "recent_trades": recent_trades,
        "recent_signals": recent_signals,
        "daily_history": history,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Dashboard exported -> {OUTPUT}")


if __name__ == "__main__":
    export()

"""
Export trading data from DuckDB to docs/data.json for the GitHub Pages dashboard.

Run at EOD after reconcile_exits() so P&L is accurate.
Merges today's DB data with the existing JSON history so daily_history
accumulates across runs even though bot.db is ephemeral on Actions.
"""

import json
from pathlib import Path
from datetime import date, datetime
from data.db import init_schema, _connect, get_longterm_open_positions

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

        # ── Overall totals ────────────────────────────────────────────
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

        # ── Portfolio split ───────────────────────────────────────────
        dt_pnl = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
              AND (portfolio_type = 'day_trading' OR portfolio_type IS NULL)
        """).fetchone()[0]

        lt_pnl = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
              AND portfolio_type = 'long_term'
        """).fetchone()[0]

        # ── Strategy performance (last 30 days, closed trades only) ──
        strat_rows = con.execute("""
            SELECT strategy,
                   COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                   COUNT(*) FILTER (WHERE pnl < 0) AS losses,
                   COALESCE(SUM(pnl), 0) AS total_pnl,
                   COALESCE(AVG(pnl / NULLIF(50, 0)), 0) AS avg_r
            FROM trades
            WHERE pnl IS NOT NULL
              AND ts >= NOW() - INTERVAL '30 days'
              AND strategy IS NOT NULL
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """).fetchall()
        strategy_stats = [
            {
                "strategy": r[0],
                "trades": int(r[1]),
                "wins": int(r[2]),
                "losses": int(r[3]),
                "total_pnl": round(float(r[4]), 2),
                "win_rate": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0.0,
                "avg_r": round(float(r[5]), 3),
            }
            for r in strat_rows
        ]

        # ── Recent trades ─────────────────────────────────────────────
        rows = con.execute("""
            SELECT ts, ticker, side, qty, price, strategy, status, pnl, portfolio_type
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
                "portfolio_type": r[8] or "day_trading",
            }
            for r in rows
        ]

        # ── Recent signals ────────────────────────────────────────────
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

    # ── Open long-term positions ──────────────────────────────────────
    try:
        lt_positions = get_longterm_open_positions()
        open_positions = [
            {
                "ticker": p["ticker"],
                "avg_entry": round(p["avg_entry"], 2),
                "qty": round(p["qty"], 4),
                "entry_count": p["entry_count"],
            }
            for p in lt_positions
        ]
    except Exception:
        open_positions = []

    # ── Merge today into rolling daily history ────────────────────────
    existing = _load_existing()
    history = [d for d in existing.get("daily_history", []) if d["date"] != today_str]
    closed = wins + losses
    history.insert(0, {
        "date": today_str,
        "pnl": float(realized_pnl),
        "dt_pnl": float(dt_pnl),
        "lt_pnl": float(lt_pnl),
        "num_trades": trades_today,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / closed if closed > 0 else 0.0,
    })
    history = history[:60]

    payload = {
        "generated_at": datetime.now().isoformat(),
        "today": today_str,
        "summary": {
            "trades_today": trades_today,
            "realized_pnl": float(realized_pnl),
            "dt_pnl": float(dt_pnl),
            "lt_pnl": float(lt_pnl),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / closed if closed > 0 else 0.0,
        },
        "open_positions": open_positions,
        "strategy_stats": strategy_stats,
        "recent_trades": recent_trades,
        "recent_signals": recent_signals,
        "daily_history": history,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Dashboard exported -> {OUTPUT}")


if __name__ == "__main__":
    export()

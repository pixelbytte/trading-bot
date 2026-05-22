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
    return {"daily_history": [], "india_daily_history": []}


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

        # ── India paper summary ───────────────────────────────────────
        india_trades_today = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE
              AND portfolio_type = 'india_paper'
        """).fetchone()[0]

        india_pnl_today = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
              AND portfolio_type = 'india_paper'
        """).fetchone()[0]

        india_wins = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE AND pnl > 0
              AND portfolio_type = 'india_paper'
        """).fetchone()[0]

        india_losses = con.execute("""
            SELECT COUNT(*) FROM trades WHERE DATE(ts) = CURRENT_DATE AND pnl < 0
              AND portfolio_type = 'india_paper'
        """).fetchone()[0]

        india_total_pnl = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE pnl IS NOT NULL AND portfolio_type = 'india_paper'
        """).fetchone()[0]

        india_total_trades = con.execute("""
            SELECT COUNT(*) FROM trades WHERE portfolio_type = 'india_paper'
        """).fetchone()[0]

        # India strategy stats (last 30 days)
        india_strat_rows = con.execute("""
            SELECT strategy,
                   COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                   COUNT(*) FILTER (WHERE pnl < 0) AS losses,
                   COALESCE(SUM(pnl), 0) AS total_pnl
            FROM trades
            WHERE pnl IS NOT NULL
              AND ts >= NOW() - INTERVAL '30 days'
              AND strategy IS NOT NULL
              AND portfolio_type = 'india_paper'
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """).fetchall()
        india_strategy_stats = [
            {
                "strategy": r[0],
                "trades": int(r[1]),
                "wins": int(r[2]),
                "losses": int(r[3]),
                "total_pnl": round(float(r[4]), 2),
                "win_rate": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0.0,
            }
            for r in india_strat_rows
        ]

        # India open positions
        india_pos_rows = con.execute("""
            SELECT ticker, SUM(qty) AS qty, AVG(price) AS avg_entry
            FROM trades
            WHERE pnl IS NULL AND side = 'buy' AND portfolio_type = 'india_paper'
            GROUP BY ticker
        """).fetchall()
        india_open_positions = [
            {"ticker": r[0], "qty": int(r[1]), "avg_entry": round(float(r[2]), 2)}
            for r in india_pos_rows
        ]

        # India recent trades
        india_trade_rows = con.execute("""
            SELECT ts, ticker, side, qty, price, strategy, status, pnl
            FROM trades WHERE portfolio_type = 'india_paper'
            ORDER BY ts DESC LIMIT 30
        """).fetchall()
        india_recent_trades = [
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
            for r in india_trade_rows
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

    # ── Merge today into rolling daily histories ──────────────────────
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

    india_history = [d for d in existing.get("india_daily_history", []) if d["date"] != today_str]
    india_closed = india_wins + india_losses
    india_history.insert(0, {
        "date": today_str,
        "pnl": float(india_pnl_today),
        "num_trades": india_trades_today,
        "wins": india_wins,
        "losses": india_losses,
        "win_rate": india_wins / india_closed if india_closed > 0 else 0.0,
    })
    india_history = india_history[:60]

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
        "india": {
            "summary": {
                "trades_today": india_trades_today,
                "pnl_today": float(india_pnl_today),
                "total_pnl": float(india_total_pnl),
                "total_trades": india_total_trades,
                "wins": india_wins,
                "losses": india_losses,
                "win_rate": india_wins / india_closed if india_closed > 0 else 0.0,
            },
            "open_positions": india_open_positions,
            "strategy_stats": india_strategy_stats,
            "recent_trades": india_recent_trades,
        },
        "india_daily_history": india_history,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Dashboard exported -> {OUTPUT}")


if __name__ == "__main__":
    export()

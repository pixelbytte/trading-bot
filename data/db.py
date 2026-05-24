"""
DuckDB wrapper for the trading bot.
All database operations go through this module.
The DB file lives at data/bot.db and is gitignored.
"""

import duckdb
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def _connect():
    """Open a connection to the DB. Caller must close it."""
    return duckdb.connect(str(DB_PATH))


def init_schema():
    """Create all tables if they don't exist. Safe to run repeatedly."""
    con = _connect()
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id BIGINT PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                ticker VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                qty DOUBLE NOT NULL,
                price DOUBLE NOT NULL,
                strategy VARCHAR,
                portfolio_type VARCHAR,
                order_id VARCHAR,
                status VARCHAR,
                pnl DOUBLE,
                notes VARCHAR
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id BIGINT PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                ticker VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                confidence DOUBLE,
                acted BOOLEAN,
                skip_reason VARCHAR
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id BIGINT PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                ticker VARCHAR NOT NULL,
                bid DOUBLE,
                ask DOUBLE,
                mid DOUBLE
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS llm_outputs (
                id BIGINT PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                source VARCHAR NOT NULL,
                ticker VARCHAR,
                output_type VARCHAR,
                content VARCHAR,
                conviction DOUBLE,
                sentiment DOUBLE
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                date DATE PRIMARY KEY,
                pnl DOUBLE NOT NULL,
                num_trades INTEGER NOT NULL,
                wins INTEGER,
                losses INTEGER,
                portfolio_type VARCHAR
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                id BIGINT PRIMARY KEY,
                ts TIMESTAMP NOT NULL,
                source VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                stacktrace VARCHAR
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch (
                date DATE PRIMARY KEY,
                value BOOLEAN NOT NULL,
                reason VARCHAR,
                ts TIMESTAMP NOT NULL
            )
        """)

        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS trades_seq START 1
        """)
        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS signals_seq START 1
        """)
        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS quotes_seq START 1
        """)
        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS llm_seq START 1
        """)
        con.execute("""
            CREATE SEQUENCE IF NOT EXISTS errors_seq START 1
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                ticker VARCHAR NOT NULL,
                date DATE NOT NULL,
                pe_ratio DOUBLE,
                eps_growth DOUBLE,
                revenue_growth DOUBLE,
                debt_to_equity DOUBLE,
                gross_margin DOUBLE,
                fcf_yield DOUBLE,
                market_cap DOUBLE,
                PRIMARY KEY (ticker, date)
            )
        """)
    finally:
        con.close()


def log_trade(ticker, side, qty, price, strategy="", portfolio_type="day_trading",
              order_id="", status="", pnl=None, notes=""):
    """Log a trade to the trades table."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO trades (id, ts, ticker, side, qty, price, strategy,
                                portfolio_type, order_id, status, pnl, notes)
            VALUES (nextval('trades_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [datetime.now(), ticker, side, qty, price, strategy,
              portfolio_type, order_id, status, pnl, notes])
    finally:
        con.close()


def log_signal(ticker, strategy, action, confidence=None, acted=False, skip_reason=""):
    """Log a strategy signal (whether or not we acted on it)."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO signals (id, ts, ticker, strategy, action, confidence, acted, skip_reason)
            VALUES (nextval('signals_seq'), ?, ?, ?, ?, ?, ?, ?)
        """, [datetime.now(), ticker, strategy, action, confidence, acted, skip_reason])
    finally:
        con.close()


def log_quote(ticker, bid, ask):
    """Log a price quote."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO quotes (id, ts, ticker, bid, ask, mid)
            VALUES (nextval('quotes_seq'), ?, ?, ?, ?, ?)
        """, [datetime.now(), ticker, bid, ask, (bid + ask) / 2])
    finally:
        con.close()


def log_error(source, message, stacktrace=""):
    """Log an error or exception."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO errors (id, ts, source, message, stacktrace)
            VALUES (nextval('errors_seq'), ?, ?, ?, ?)
        """, [datetime.now(), source, message, stacktrace])
    finally:
        con.close()


def get_trades(limit=20):
    """Fetch most recent trades."""
    con = _connect()
    try:
        rows = con.execute(f"""
            SELECT ts, ticker, side, qty, price, strategy, status, pnl
            FROM trades
            ORDER BY ts DESC
            LIMIT {int(limit)}
        """).fetchall()
        return [
            {"ts": r[0], "ticker": r[1], "side": r[2], "qty": r[3],
             "price": r[4], "strategy": r[5], "status": r[6], "pnl": r[7]}
            for r in rows
        ]
    finally:
        con.close()


def trade_count_today():
    """How many trades placed today (for risk limits)."""
    con = _connect()
    try:
        result = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE DATE(ts) = CURRENT_DATE
        """).fetchone()
        return result[0] if result else 0
    finally:
        con.close()


def is_trading_halted():
    """Check if kill switch is currently flipped."""
    con = _connect()
    try:
        result = con.execute("""
            SELECT value FROM kill_switch WHERE date = CURRENT_DATE
        """).fetchone()
        return bool(result[0]) if result else False
    finally:
        con.close()


def set_trading_halted(reason=""):
    """Flip the kill switch. Use when daily loss exceeded."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO kill_switch (date, value, reason, ts)
            VALUES (CURRENT_DATE, TRUE, ?, ?)
            ON CONFLICT (date) DO UPDATE SET value = TRUE, reason = excluded.reason
        """, [reason, datetime.now()])
    finally:
        con.close()


def reset_kill_switch():
    """Clear today's kill switch (run at midnight)."""
    con = _connect()
    try:
        con.execute("""
            DELETE FROM kill_switch WHERE date = CURRENT_DATE
        """)
    finally:
        con.close()


def trades_in_last_hour():
    """Count trades placed in the last 60 minutes (circuit breaker check)."""
    con = _connect()
    try:
        result = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE ts > NOW() - INTERVAL '1 hour'
        """).fetchone()
        return result[0] if result else 0
    finally:
        con.close()


def get_pyramid_state(ticker: str):
    """
    Return (base_qty, base_entry_price, pyramid_level) for the most recent open
    bracket entry on this ticker. pyramid_level is the count of pyramid orders
    already placed (0 = none, 1 = +1R add done, 2 = both done).

    Used by intraday.check_trailing_stops() to decide whether to add a new
    pyramid tranche when a position crosses +1R or +2R.
    """
    con = _connect()
    try:
        # Find the latest open base entry for this ticker
        base = con.execute("""
            SELECT id, qty, price, ts FROM trades
            WHERE ticker = ?
              AND side = 'buy'
              AND pnl IS NULL
              AND notes LIKE 'bracket%'
              AND ts >= NOW() - INTERVAL '14 days'
            ORDER BY ts DESC
            LIMIT 1
        """, [ticker]).fetchone()

        if not base:
            return None

        _, base_qty, base_price, base_ts = base

        # Count pyramid orders for this ticker since the base entry
        pyramid_count = con.execute("""
            SELECT COUNT(*) FROM trades
            WHERE ticker = ?
              AND side = 'buy'
              AND notes LIKE 'pyramid_%'
              AND ts >= ?
        """, [ticker, base_ts]).fetchone()[0]

        return {
            "base_qty": float(base_qty),
            "base_entry": float(base_price),
            "pyramid_level": int(pyramid_count),
            "base_ts": base_ts,
        }
    finally:
        con.close()


def has_taken_partial_exit(ticker: str, since_ts) -> bool:
    """
    Return True if a partial-exit sell has already been logged for this ticker
    since the given base-entry timestamp. Used to make the +2R partial-exit
    idempotent across the 15-min intraday cycles.
    """
    con = _connect()
    try:
        row = con.execute("""
            SELECT 1 FROM trades
            WHERE ticker = ?
              AND side = 'sell'
              AND notes LIKE 'partial_exit%'
              AND ts >= ?
            LIMIT 1
        """, [ticker, since_ts]).fetchone()
        return bool(row)
    finally:
        con.close()


def get_open_trade_entries():
    """
    Return buy-side trades that have not yet been reconciled (pnl IS NULL).
    These are bracket entries waiting for their stop or target to fill.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT id, ticker, qty, price, order_id, ts
            FROM trades
            WHERE side = 'buy'
              AND pnl IS NULL
              AND notes LIKE 'bracket%'
              AND ts >= NOW() - INTERVAL '3 days'
            ORDER BY ts DESC
        """).fetchall()
        return [
            {"id": r[0], "ticker": r[1], "qty": r[2],
             "price": r[3], "order_id": r[4], "ts": r[5]}
            for r in rows
        ]
    finally:
        con.close()


def update_trade_pnl(trade_id, exit_price, pnl, notes=""):
    """Record the exit price and realized P&L on an existing trade entry."""
    con = _connect()
    try:
        con.execute("""
            UPDATE trades
            SET pnl = ?, notes = COALESCE(notes || ' | ', '') || ?
            WHERE id = ?
        """, [pnl, f"exit={exit_price:.2f} {notes}", trade_id])
    finally:
        con.close()


def log_llm_output(source, ticker, output_type, content, conviction=None, sentiment=None):
    """Log a Claude analysis result (sentiment score, trade thesis, etc.)."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO llm_outputs (id, ts, source, ticker, output_type, content, conviction, sentiment)
            VALUES (nextval('llm_seq'), ?, ?, ?, ?, ?, ?, ?)
        """, [datetime.now(), source, ticker, output_type, content, conviction, sentiment])
    finally:
        con.close()


def get_ticker_sentiments():
    """
    Return today's latest pre-market sentiment per ticker.
    Dict: {ticker: {"sentiment": float, "conviction": float, "content": str}}
    Empty dict if no scores logged today.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT DISTINCT ON (ticker) ticker, sentiment, conviction, content
            FROM llm_outputs
            WHERE source = 'premarket_news'
              AND output_type = 'sentiment'
              AND DATE(ts) = CURRENT_DATE
              AND ticker IS NOT NULL
            ORDER BY ticker, ts DESC
        """).fetchall()
        return {
            r[0]: {"sentiment": float(r[1] or 0), "conviction": float(r[2] or 0), "content": r[3] or ""}
            for r in rows
        }
    finally:
        con.close()


def store_fundamentals(ticker, data):
    """Upsert fundamental metrics for a ticker (one row per ticker per day)."""
    con = _connect()
    try:
        con.execute("""
            INSERT INTO fundamentals
                (ticker, date, pe_ratio, eps_growth, revenue_growth,
                 debt_to_equity, gross_margin, fcf_yield, market_cap)
            VALUES (?, CURRENT_DATE, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, date) DO UPDATE SET
                pe_ratio = excluded.pe_ratio,
                eps_growth = excluded.eps_growth,
                revenue_growth = excluded.revenue_growth,
                debt_to_equity = excluded.debt_to_equity,
                gross_margin = excluded.gross_margin,
                fcf_yield = excluded.fcf_yield,
                market_cap = excluded.market_cap
        """, [ticker, data.get("pe_ratio"), data.get("eps_growth"),
              data.get("revenue_growth"), data.get("debt_to_equity"),
              data.get("gross_margin"), data.get("fcf_yield"), data.get("market_cap")])
    finally:
        con.close()


def get_fundamentals(ticker):
    """Return the most recent fundamental metrics for a ticker, or None."""
    con = _connect()
    try:
        row = con.execute("""
            SELECT pe_ratio, eps_growth, revenue_growth, debt_to_equity,
                   gross_margin, fcf_yield, market_cap
            FROM fundamentals
            WHERE ticker = ?
            ORDER BY date DESC LIMIT 1
        """, [ticker]).fetchone()
        if not row:
            return None
        return {
            "pe_ratio": row[0], "eps_growth": row[1], "revenue_growth": row[2],
            "debt_to_equity": row[3], "gross_margin": row[4],
            "fcf_yield": row[5], "market_cap": row[6],
        }
    finally:
        con.close()


def get_latest_thesis(ticker):
    """Return the most recent Claude thesis for a ticker, or None."""
    con = _connect()
    try:
        row = con.execute("""
            SELECT content, conviction, sentiment
            FROM llm_outputs
            WHERE source = 'thesis' AND ticker = ?
            ORDER BY ts DESC LIMIT 1
        """, [ticker]).fetchone()
        if not row:
            return None
        return {"content": row[0], "conviction": float(row[1] or 0.5),
                "sentiment": float(row[2] or 0)}
    finally:
        con.close()


def get_longterm_open_positions():
    """
    Return open long-term positions with their average entry and tranche count.
    An 'open' entry is a buy with pnl IS NULL placed within the last 120 days.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT ticker,
                   AVG(price)  AS avg_entry,
                   COUNT(*)    AS entry_count,
                   SUM(qty)    AS total_qty
            FROM trades
            WHERE side = 'buy'
              AND portfolio_type = 'long_term'
              AND pnl IS NULL
              AND ts >= NOW() - INTERVAL '120 days'
            GROUP BY ticker
        """).fetchall()
        return [
            {"ticker": r[0], "avg_entry": float(r[1]),
             "entry_count": int(r[2]), "qty": float(r[3])}
            for r in rows
        ]
    finally:
        con.close()


def daily_pnl_so_far():
    """Sum of realized P&L from today's closed trades."""
    con = _connect()
    try:
        result = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE DATE(ts) = CURRENT_DATE AND pnl IS NOT NULL
        """).fetchone()
        return float(result[0]) if result else 0.0
    finally:
        con.close()


def get_open_scalp_tickers() -> list:
    """
    Return tickers where a scalp buy was placed today with no realized P&L yet.
    Used by intraday.py to identify positions to force-close at 3:45pm ET.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT DISTINCT ticker FROM trades
            WHERE side = 'buy'
              AND strategy = 'scalp'
              AND pnl IS NULL
              AND DATE(ts) = CURRENT_DATE
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def get_deep_research_picks(min_conviction=0.70):
    """
    Return (ticker, conviction) pairs from Sunday deep research within the last 7 days.
    Only returns picks with conviction >= min_conviction.
    Used by longterm.py to extend its scan universe beyond the static watchlist.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT ticker, MAX(conviction) AS conv
            FROM llm_outputs
            WHERE source = 'deep_research'
              AND output_type = 'long_term_pick'
              AND conviction >= ?
              AND ticker IS NOT NULL
              AND ts >= NOW() - INTERVAL '7 days'
            GROUP BY ticker
            ORDER BY conv DESC
        """, [min_conviction]).fetchall()
        return [(r[0], float(r[1])) for r in rows]
    finally:
        con.close()
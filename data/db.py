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
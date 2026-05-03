"""
DuckDB wrapper for the trading bot.
All database operations go through this module.
The DB file lives at data/bot.db and is gitignored.
"""

import duckdb
import os
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
"""
Paper trading wrapper for the India bot.
Uses yfinance for real NSE market data — no Upstox credentials needed.
Simulates order fills at current price and tracks positions in DuckDB.

Switch to live: set INDIA_PAPER=false in .env / GitHub Secrets.
"""

import uuid
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

from data.db import _connect, log_trade
from utils.logger import info, warning, error

IST = ZoneInfo("Asia/Kolkata")

from config.india_settings import ACCOUNT_SIZE_INR


# Symbols that need special yfinance handling
_YF_OVERRIDES = {
    "M&M": "M&M.NS",           # ampersand is literal in yfinance
    "BAJAJ-AUTO": "BAJAJ-AUTO.NS",
}


def _yf(symbol: str) -> str:
    return _YF_OVERRIDES.get(symbol, f"{symbol}.NS")


# ---------------------------------------------------------------------------
# Public API — identical signatures to brokers/upstox.py
# ---------------------------------------------------------------------------

def get_bars(symbol: str, days: int = 400, timeframe: str = "day") -> list:
    """Fetch NSE daily bars via yfinance.download. Returns list of dicts (ascending)."""
    from datetime import timedelta
    try:
        start = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
        end = datetime.now(IST).strftime("%Y-%m-%d")
        interval = {"day": "1d", "15min": "15m", "30min": "30m", "hour": "60m"}.get(timeframe, "1d")
        df = yf.download(_yf(symbol), start=start, end=end,
                         interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        # yfinance may return multi-level columns: ('Close', 'SYM.NS') — flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        date_col = "Date" if "Date" in df.columns else "Datetime"
        bars = []
        for _, row in df.iterrows():
            try:
                close_val = row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]
                if pd.isna(close_val):
                    continue  # skip partial/empty bars (e.g. today before close)
                bars.append({
                    "ts":     str(row[date_col]),
                    "open":   float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]),
                    "high":   float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]),
                    "low":    float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]),
                    "close":  float(close_val),
                    "volume": float(row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return bars
    except Exception as e:
        error(f"Paper get_bars failed for {symbol}: {e}", source="upstox_paper")
        return []


def get_quote(symbol: str) -> dict | None:
    """Return current price using yfinance fast_info."""
    try:
        fi = yf.Ticker(_yf(symbol)).fast_info
        price = float(fi.last_price or fi.regular_market_previous_close)
        return {"ticker": symbol, "price": price, "bid": price, "ask": price}
    except Exception as e:
        error(f"Paper get_quote failed for {symbol}: {e}", source="upstox_paper")
        return None


def get_account() -> dict:
    """Derive paper account balance: starting capital ± realised P&L − open exposure."""
    con = _connect()
    try:
        realised = con.execute("""
            SELECT COALESCE(SUM(pnl), 0) FROM trades
            WHERE pnl IS NOT NULL AND portfolio_type = 'india_paper'
        """).fetchone()[0]
        exposure = con.execute("""
            SELECT COALESCE(SUM(price * qty), 0) FROM trades
            WHERE pnl IS NULL AND portfolio_type = 'india_paper' AND side = 'buy'
        """).fetchone()[0]
        equity = ACCOUNT_SIZE_INR + float(realised)
        available = equity - float(exposure)
        return {"equity": equity, "buying_power": available, "cash": available}
    finally:
        con.close()


def get_positions() -> list:
    """Return open paper positions (buy trades without a closing pnl)."""
    con = _connect()
    try:
        rows = con.execute("""
            SELECT ticker, qty, price FROM trades
            WHERE pnl IS NULL AND side = 'buy' AND portfolio_type = 'india_paper'
            ORDER BY ts DESC
        """).fetchall()
    finally:
        con.close()

    positions = []
    for ticker, qty, avg_entry in rows:
        quote = get_quote(ticker)
        ltp = quote["price"] if quote else float(avg_entry)
        positions.append({
            "ticker":       ticker,
            "qty":          int(qty),
            "avg_entry":    float(avg_entry),
            "current_price": ltp,
            "unrealized_pl": (ltp - float(avg_entry)) * int(qty),
            "product":      "D",
        })
    return positions


def place_bracket_order(
    symbol: str, qty: int, entry_price: float,
    stop_price: float, target_price: float,
) -> dict | None:
    """Simulate a bracket order — fills immediately at entry_price."""
    fake_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
    log_trade(
        symbol, "buy", qty, entry_price, "bracket",
        portfolio_type="india_paper",
        order_id=fake_id, status="paper_fill",
        notes=f"SL={stop_price:.2f} TGT={target_price:.2f}",
    )
    info(
        f"[PAPER] BUY {qty} {symbol} @ ₹{entry_price:.2f} "
        f"SL=₹{stop_price:.2f} TGT=₹{target_price:.2f} [{fake_id}]",
        source="upstox_paper",
    )
    return {"order_id": fake_id, "status": "paper_fill", "ticker": symbol}


def place_market_order(symbol: str, qty: int, side: str, product: str = "D") -> dict | None:
    """Simulate a market order fill at current quote price."""
    quote = get_quote(symbol)
    price = quote["price"] if quote else 0.0
    fake_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
    log_trade(
        symbol, side.lower(), qty, price, "market",
        portfolio_type="india_paper",
        order_id=fake_id, status="paper_fill",
    )
    info(f"[PAPER] {side.upper()} {qty} {symbol} @ ₹{price:.2f} [{fake_id}]",
         source="upstox_paper")
    return {"order_id": fake_id, "status": "paper_fill", "ticker": symbol}


def close_position(symbol: str) -> bool:
    """Simulate closing at current price; writes realized P&L back to the trade row."""
    positions = get_positions()
    pos = next((p for p in positions if p["ticker"] == symbol), None)
    if not pos:
        warning(f"No paper position to close for {symbol}", source="upstox_paper")
        return False

    quote = get_quote(symbol)
    exit_price = quote["price"] if quote else pos["avg_entry"]
    realised = (exit_price - pos["avg_entry"]) * pos["qty"]

    con = _connect()
    try:
        # Update the most recent open buy row for this ticker
        con.execute("""
            UPDATE trades
            SET pnl = ?, notes = COALESCE(notes, '') || ' | CLOSED @' || CAST(? AS VARCHAR)
            WHERE ticker = ? AND pnl IS NULL AND side = 'buy'
              AND portfolio_type = 'india_paper'
            ORDER BY ts DESC
            LIMIT 1
        """, [realised, round(exit_price, 2), symbol])
    finally:
        con.close()

    info(f"[PAPER] Closed {symbol} @ ₹{exit_price:.2f} P&L ₹{realised:+.0f}",
         source="upstox_paper")
    return True


def cancel_all_orders() -> int:
    return 0  # Nothing pending in paper mode

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
from utils.discord import send_india_close_alert

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
    """Fetch NSE bars via yfinance.download. Returns list of dicts (ascending).

    For intraday intervals (15m/30m/60m), yfinance's start/end mode is buggy and
    often returns 'possibly delisted' for NSE tickers. Use period= shorthand
    instead — yfinance handles the date math correctly that way.
    """
    from datetime import timedelta
    try:
        interval = {"day": "1d", "15min": "15m", "30min": "30m", "hour": "60m"}.get(timeframe, "1d")
        is_intraday = interval in ("15m", "30m", "60m", "1h", "5m", "1m")

        if is_intraday:
            # yfinance only allows up to 60d for intraday — clamp accordingly
            period_days = max(min(days, 60), 5)
            df = yf.download(_yf(symbol), period=f"{period_days}d",
                             interval=interval, progress=False, auto_adjust=True)
        else:
            start = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
            end = datetime.now(IST).strftime("%Y-%m-%d")
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


def simulate_bracket_exits() -> int:
    """
    For each open paper position, check today's intraday high/low against the
    SL/TGT levels stored in the trade's notes. If either was touched, close
    the position at that level and write the realized P&L.

    Returns the number of positions closed this cycle. Call this once per
    intraday cycle before the bot looks for new entries.
    """
    con = _connect()
    try:
        rows = con.execute("""
            SELECT id, ticker, qty, price, notes FROM trades
            WHERE pnl IS NULL AND side = 'buy' AND portfolio_type = 'india_paper'
        """).fetchall()
    finally:
        con.close()

    if not rows:
        return 0

    closed = 0
    for row_id, ticker, qty, entry, notes in rows:
        if not notes or "SL=" not in notes or "TGT=" not in notes:
            continue
        try:
            sl = float(notes.split("SL=")[1].split()[0])
            tgt = float(notes.split("TGT=")[1].split()[0].rstrip(","))
        except (ValueError, IndexError):
            continue

        # Pull today's intraday bars to see if SL/TGT was touched
        bars = get_bars(ticker, days=2, timeframe="15min")
        if not bars:
            continue
        # Filter to bars at/after the entry timestamp (only count today's session)
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        today_bars = [b for b in bars if today_str in str(b.get("ts", ""))]
        if not today_bars:
            continue

        exit_price = None
        exit_reason = None
        for bar in today_bars:
            lo = float(bar.get("low", 0) or 0)
            hi = float(bar.get("high", 0) or 0)
            if lo <= sl:
                exit_price = sl
                exit_reason = "STOP"
                break
            if hi >= tgt:
                exit_price = tgt
                exit_reason = "TARGET"
                break

        if exit_price is None:
            continue

        realised = (exit_price - float(entry)) * int(qty)
        con = _connect()
        try:
            con.execute("""
                UPDATE trades
                SET pnl = ?,
                    notes = COALESCE(notes, '') || ' | ' || ? || ' @' || CAST(? AS VARCHAR)
                WHERE id = ?
            """, [realised, exit_reason, round(exit_price, 2), row_id])
        finally:
            con.close()
        info(
            f"[PAPER] {ticker} hit {exit_reason} @ ₹{exit_price:.2f} — closed "
            f"{int(qty)} shares, P&L ₹{realised:+.0f}",
            source="upstox_paper",
        )
        try:
            send_india_close_alert(ticker, int(qty), exit_price, exit_reason, realised)
        except Exception:
            pass  # Discord failures must never block trading
        closed += 1

    return closed


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

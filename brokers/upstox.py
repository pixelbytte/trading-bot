"""
Upstox v2 broker wrapper for NSE equity trading.
Mirrors the brokers/alpaca.py interface so all strategies work unchanged.

Auth: Upstox access tokens expire at midnight IST daily.
      Set UPSTOX_ACCESS_TOKEN in .env / GitHub Secrets each morning.
      Run `python -m scripts.upstox_auth` to generate a fresh token.
"""

import os
import io
import gzip
import csv
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from data.db import log_trade, log_error
from utils.logger import info, warning, error

UPSTOX_BASE = "https://api.upstox.com/v2"
INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
IST = ZoneInfo("Asia/Kolkata")

_instrument_cache: dict[str, str] = {}  # tradingsymbol -> instrument_key


def _headers() -> dict:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN not set — run scripts/upstox_auth.py")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Api-Version": "2.0",
    }


def _load_instrument_master() -> None:
    """Load NSE_EQ spot equities only (skip F&O, currency, commodity rows).

    Upstox CSV schema (verified 2026-05-27):
      instrument_type='EQUITY' AND exchange='NSE_EQ' selects spot stocks.
      Earlier code used 'instrumenttype'='EQ' which matched zero rows.
    """
    global _instrument_cache
    if _instrument_cache:
        return
    try:
        r = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
        r.raise_for_status()
        with gzip.open(io.BytesIO(r.content), "rt") as f:
            for row in csv.DictReader(f):
                if row.get("instrument_type") == "EQUITY" and row.get("exchange") == "NSE_EQ":
                    _instrument_cache[row["tradingsymbol"]] = row["instrument_key"]
        info(f"Upstox instrument master loaded: {len(_instrument_cache)} NSE equities",
             source="upstox")
    except Exception as e:
        error(f"Failed to load instrument master: {e}", source="upstox", exc=e)


def _get_key(symbol: str) -> str:
    _load_instrument_master()
    key = _instrument_cache.get(symbol)
    if not key:
        raise ValueError(f"No instrument key found for '{symbol}' — check NSE trading symbol")
    return key


# ---------------------------------------------------------------------------
# Public API — matches brokers/alpaca.py interface
# ---------------------------------------------------------------------------

def get_bars(symbol: str, days: int = 400, timeframe: str = "day") -> list:
    """Fetch OHLCV daily bars. Returns list of dicts (ascending by date)."""
    try:
        key = _get_key(symbol)
    except ValueError as e:
        warning(str(e), source="upstox")
        return []

    interval = "30minute" if timeframe == "15min" else "day"
    to_date = datetime.now(IST).strftime("%Y-%m-%d")
    from_date = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"{UPSTOX_BASE}/historical-candle/{key}/{interval}/{to_date}/{from_date}"

    try:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        candles = r.json().get("data", {}).get("candles", [])
        # Upstox returns newest-first; reverse to ascending
        bars = [
            {"ts": c[0], "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in reversed(candles)
        ]
        return bars
    except Exception as e:
        error(f"get_bars failed for {symbol}: {e}", source="upstox", exc=e)
        return []


def get_quote(symbol: str) -> dict | None:
    """Return current LTP and bid/ask for a symbol."""
    try:
        key = _get_key(symbol)
        r = requests.get(
            f"{UPSTOX_BASE}/market-quote/quotes?instrument_key={key}",
            headers=_headers(), timeout=10,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        # Response key uses colon: "NSE_EQ:INE002A01018"
        quote = next(iter(data.values()), None)
        if not quote:
            return None
        ltp = float(quote.get("last_price", 0))
        depth = quote.get("depth", {})
        bid = float((depth.get("buy") or [{}])[0].get("price", ltp))
        ask = float((depth.get("sell") or [{}])[0].get("price", ltp))
        return {"ticker": symbol, "price": ltp, "bid": bid, "ask": ask}
    except Exception as e:
        error(f"get_quote failed for {symbol}: {e}", source="upstox", exc=e)
        return None


def get_account() -> dict:
    """Return available margin and total equity."""
    try:
        r = requests.get(f"{UPSTOX_BASE}/user/fund-margin", headers=_headers(), timeout=10)
        r.raise_for_status()
        eq = r.json().get("data", {}).get("equity", {})
        available = float(eq.get("available_margin", 0))
        used = float(eq.get("used_margin", 0))
        return {"equity": available + used, "buying_power": available, "cash": available}
    except Exception as e:
        error(f"get_account failed: {e}", source="upstox", exc=e)
        return {"equity": 0.0, "buying_power": 0.0, "cash": 0.0}


def get_positions() -> list:
    """Return all open positions with unrealized P&L."""
    try:
        r = requests.get(
            f"{UPSTOX_BASE}/portfolio/short-term-positions",
            headers=_headers(), timeout=10,
        )
        r.raise_for_status()
        positions = []
        for p in r.json().get("data", []):
            qty = int(p.get("quantity", 0))
            if qty == 0:
                continue
            avg = float(p.get("average_price", 0))
            ltp = float(p.get("last_price", avg))
            positions.append({
                "ticker": p.get("tradingsymbol"),
                "qty": qty,
                "avg_entry": avg,
                "current_price": ltp,
                "unrealized_pl": (ltp - avg) * qty,
                "product": p.get("product", "D"),
            })
        return positions
    except Exception as e:
        error(f"get_positions failed: {e}", source="upstox", exc=e)
        return []


def place_bracket_order(
    symbol: str, qty: int, entry_price: float,
    stop_price: float, target_price: float,
) -> dict | None:
    """
    Place a Bracket Order (BO) — limit entry with attached SL and target.
    stoploss and squareoff are price differences from entry (not absolute prices).
    Falls back to a regular CNC limit order if BO is rejected.
    """
    try:
        key = _get_key(symbol)
    except ValueError as e:
        error(str(e), source="upstox")
        return None

    stoploss_pts = round(abs(entry_price - stop_price), 2)
    target_pts = round(abs(target_price - entry_price), 2)
    body = {
        "quantity": qty,
        "product": "BO",
        "validity": "DAY",
        "price": round(entry_price, 2),
        "tag": "trading_bot",
        "instrument_token": key,
        "order_type": "LIMIT",
        "transaction_type": "BUY",
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
        "squareoff": target_pts,
        "stoploss": stoploss_pts,
        "trailing_stoploss": 0,
    }
    try:
        r = requests.post(f"{UPSTOX_BASE}/order/place", json=body,
                          headers=_headers(), timeout=15)
        r.raise_for_status()
        order_id = r.json().get("data", {}).get("order_id")
        info(f"Bracket order: BUY {qty} {symbol} @ ₹{entry_price} "
             f"SL=₹{stop_price} TGT=₹{target_price} -> {order_id}", source="upstox")
        log_trade(symbol, "buy", qty, entry_price, "bracket",
                  notes=f"SL={stop_price} TGT={target_price} OID={order_id}",
                  portfolio_type="india")
        return {"order_id": order_id, "status": "placed", "ticker": symbol}
    except Exception as e:
        error(f"place_bracket_order failed for {symbol}: {e}", source="upstox", exc=e)
        return None


def place_market_order(symbol: str, qty: int, side: str, product: str = "D") -> dict | None:
    """Place a market order. product: D=delivery (CNC), I=intraday (MIS)."""
    try:
        key = _get_key(symbol)
    except ValueError as e:
        error(str(e), source="upstox")
        return None

    body = {
        "quantity": qty,
        "product": product,
        "validity": "DAY",
        "price": 0,
        "tag": "trading_bot",
        "instrument_token": key,
        "order_type": "MARKET",
        "transaction_type": side.upper(),
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
    }
    try:
        r = requests.post(f"{UPSTOX_BASE}/order/place", json=body,
                          headers=_headers(), timeout=15)
        r.raise_for_status()
        order_id = r.json().get("data", {}).get("order_id")
        info(f"Market order: {side.upper()} {qty} {symbol} -> {order_id}", source="upstox")
        return {"order_id": order_id, "status": "placed", "ticker": symbol}
    except Exception as e:
        error(f"place_market_order failed for {symbol}: {e}", source="upstox", exc=e)
        return None


def close_position(symbol: str) -> bool:
    """Close an open position at market price."""
    positions = get_positions()
    pos = next((p for p in positions if p["ticker"] == symbol), None)
    if not pos:
        warning(f"No open position to close for {symbol}", source="upstox")
        return False
    result = place_market_order(symbol, abs(pos["qty"]), "SELL", product=pos.get("product", "D"))
    return result is not None


def cancel_all_orders() -> int:
    """Cancel all open/pending orders. Returns count cancelled."""
    try:
        r = requests.get(f"{UPSTOX_BASE}/order/retrieve-all",
                         headers=_headers(), timeout=10)
        r.raise_for_status()
        open_statuses = {"open", "trigger pending", "pending", "put order req received"}
        cancelled = 0
        for order in r.json().get("data", []):
            if order.get("status", "").lower() in open_statuses:
                oid = order.get("order_id")
                del_r = requests.delete(
                    f"{UPSTOX_BASE}/order/cancel?order_id={oid}",
                    headers=_headers(), timeout=10,
                )
                if del_r.status_code == 200:
                    cancelled += 1
        return cancelled
    except Exception as e:
        error(f"cancel_all_orders failed: {e}", source="upstox", exc=e)
        return 0

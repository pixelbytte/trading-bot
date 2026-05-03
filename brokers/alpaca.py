"""
Alpaca broker wrapper.
All Alpaca API interactions live here.
The rest of the bot uses these functions, not Alpaca SDK directly.
"""

import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from data.db import log_trade, log_error
from risk.check import check_order
from utils.logger import info as log_info, error as log_error_msg

load_dotenv()

_KEY = os.getenv("ALPACA_KEY")
_SECRET = os.getenv("ALPACA_SECRET")

if not _KEY or not _SECRET:
    raise RuntimeError("ALPACA_KEY or ALPACA_SECRET missing from .env")

trading = TradingClient(_KEY, _SECRET, paper=True)
data = StockHistoricalDataClient(_KEY, _SECRET)

log_info("Alpaca client initialized (paper mode)", source="alpaca")
def get_account():
    """Return account info: cash, equity, status."""
    a = trading.get_account()
    return {
        "status": str(a.status),
        "cash": float(a.cash),
        "equity": float(a.equity),
        "buying_power": float(a.buying_power),
    }


def get_quote(ticker):
    """Get latest bid/ask for a ticker."""
    req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
    quotes = data.get_stock_latest_quote(req)
    q = quotes[ticker]
    return {
        "ticker": ticker,
        "bid": float(q.bid_price),
        "ask": float(q.ask_price),
        "mid": (float(q.bid_price) + float(q.ask_price)) / 2,
    }

def get_bars(ticker, days=90, timeframe="day"):
    """Get historical bars for a ticker. timeframe: 'day', 'hour', '15min'."""
    from datetime import datetime, timedelta
    from alpaca.data.enums import DataFeed

    end = datetime.now() - timedelta(minutes=20)
    start = end - timedelta(days=days)

    tf_map = {
        "day": TimeFrame.Day,
        "hour": TimeFrame.Hour,
        "15min": TimeFrame(15, "Min"),
    }

    req = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=tf_map.get(timeframe, TimeFrame.Day),
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = data.get_stock_bars(req).df
    bars = bars.reset_index()
    bars = bars[bars["symbol"] == ticker] if "symbol" in bars.columns else bars

    return [
        {
            "ts": row["timestamp"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for _, row in bars.iterrows()
    ]
def get_positions():
    """List currently held positions."""
    positions = trading.get_all_positions()
    return [
        {
            "ticker": p.symbol,
            "qty": float(p.qty),
            "avg_entry": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
        }
        for p in positions
    ]


def place_market_order(ticker, qty, side, strategy="", portfolio_type="day_trading"):
    """Place a market order. side = 'buy' or 'sell'. Risk-checked, logs to DB."""
    log_info(f"Placing {side.upper()} {ticker} x{qty} ({strategy})", source="alpaca")

    # Get current price first (needed for risk check)
    quote = get_quote(ticker)
    price = quote["mid"]

    # Risk check
    allowed, reason = check_order(ticker, qty, side, price)
    if not allowed:
        log_info(f"Order BLOCKED: {reason}", source="alpaca")
        return {
            "id": None,
            "ticker": ticker,
            "qty": qty,
            "side": side,
            "status": "blocked",
            "blocked_reason": reason,
        }

    try:
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = trading.submit_order(req)

        log_trade(
            ticker=ticker,
            side=side.lower(),
            qty=float(qty),
            price=price,
            strategy=strategy,
            portfolio_type=portfolio_type,
            order_id=str(order.id),
            status=str(order.status),
        )

        return {
            "id": str(order.id),
            "ticker": order.symbol,
            "qty": float(order.qty),
            "side": str(order.side),
            "status": str(order.status),
        }
    except Exception as e:
        log_error(source="alpaca.place_market_order", message=str(e))
        raise
def close_position(ticker):
    """Close an open position for a ticker."""
    closed = trading.close_position(ticker)
    return {"ticker": ticker, "closed_order_id": str(closed.id)}


def cancel_all_orders():
    """Cancel every open order. Use as emergency reset."""
    trading.cancel_orders()
    return {"status": "all_cancelled"}
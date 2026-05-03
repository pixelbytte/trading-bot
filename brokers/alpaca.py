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

load_dotenv()

_KEY = os.getenv("ALPACA_KEY")
_SECRET = os.getenv("ALPACA_SECRET")

if not _KEY or not _SECRET:
    raise RuntimeError("ALPACA_KEY or ALPACA_SECRET missing from .env")

trading = TradingClient(_KEY, _SECRET, paper=True)
data = StockHistoricalDataClient(_KEY, _SECRET)


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


def place_market_order(ticker, qty, side):
    """Place a market order. side = 'buy' or 'sell'."""
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    req = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )
    order = trading.submit_order(req)
    return {
        "id": str(order.id),
        "ticker": order.symbol,
        "qty": float(order.qty),
        "side": str(order.side),
        "status": str(order.status),
    }


def close_position(ticker):
    """Close an open position for a ticker."""
    closed = trading.close_position(ticker)
    return {"ticker": ticker, "closed_order_id": str(closed.id)}


def cancel_all_orders():
    """Cancel every open order. Use as emergency reset."""
    trading.cancel_orders()
    return {"status": "all_cancelled"}
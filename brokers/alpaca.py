"""
Alpaca broker wrapper.
All Alpaca API interactions live here.
The rest of the bot uses these functions, not Alpaca SDK directly.
"""

import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, GetOrdersRequest, ReplaceOrderRequest,
    TakeProfitRequest, StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, OrderClass, QueryOrderStatus
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
def place_bracket_order(ticker, qty, side, entry_price, stop_price, target_price,
                         strategy="", portfolio_type="day_trading"):
    """
    Place a bracket order: market entry + stop-loss + take-profit legs.

    Args:
        ticker: Stock symbol
        qty: Number of shares
        side: 'buy' or 'sell'
        entry_price: Expected entry price (for risk check and DB logging)
        stop_price: Stop-loss price (1.5 * ATR below entry for longs)
        target_price: Take-profit limit price (3.0 * ATR above entry for longs)
    """
    log_info(
        f"Placing bracket {side.upper()} {ticker} x{qty} "
        f"entry~{entry_price:.2f} stop={stop_price:.2f} target={target_price:.2f}",
        source="alpaca",
    )

    allowed, reason = check_order(ticker, qty, side, entry_price)
    if not allowed:
        log_info(f"Bracket order BLOCKED: {reason}", source="alpaca")
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
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
            stop_loss=StopLossRequest(stop_price=round(float(stop_price), 2)),
        )
        order = trading.submit_order(req)

        log_trade(
            ticker=ticker,
            side=side.lower(),
            qty=float(qty),
            price=entry_price,
            strategy=strategy,
            portfolio_type=portfolio_type,
            order_id=str(order.id),
            status=str(order.status),
            notes=f"bracket stop={stop_price:.2f} target={target_price:.2f}",
        )

        return {
            "id": str(order.id),
            "ticker": order.symbol,
            "qty": float(order.qty),
            "side": str(order.side),
            "status": str(order.status),
            "stop_price": stop_price,
            "target_price": target_price,
        }
    except Exception as e:
        log_error(source="alpaca.place_bracket_order", message=str(e))
        raise


def update_stop_order(ticker, new_stop_price):
    """
    Find the open stop-loss leg for a position and move it to new_stop_price.
    Used to trail stops on winning trades.

    Returns dict with updated order info, or None if no stop order found.
    """
    try:
        req = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[ticker],
        )
        orders = trading.get_orders(req)

        # Stop-loss leg is a stop-type sell order (for long positions)
        stop_order = next(
            (o for o in orders
             if str(o.order_type) in ("stop", "OrderType.STOP")
             and str(o.side) in ("sell", "OrderSide.SELL")),
            None,
        )

        if stop_order is None:
            log_info(f"No open stop order found for {ticker}", source="alpaca")
            return None

        updated = trading.replace_order_by_id(
            str(stop_order.id),
            ReplaceOrderRequest(stop_price=round(float(new_stop_price), 2)),
        )
        log_info(
            f"Trailing stop updated for {ticker}: → {new_stop_price:.2f}",
            source="alpaca",
        )
        return {"id": str(updated.id), "ticker": ticker, "new_stop": new_stop_price}
    except Exception as e:
        log_error_msg(f"Failed to update stop for {ticker}: {e}", source="alpaca")
        return None


def close_position(ticker):
    """Close an open position for a ticker."""
    closed = trading.close_position(ticker)
    return {"ticker": ticker, "closed_order_id": str(closed.id)}


def cancel_all_orders():
    """Cancel every open order. Use as emergency reset."""
    trading.cancel_orders()
    return {"status": "all_cancelled"}
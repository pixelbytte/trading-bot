"""
Pre-trade safety check. Every order passes through here.
Returns (allowed: bool, reason: str).
"""

from risk.limits import (
    MAX_POSITION_USD,
    MIN_POSITION_USD,
    MAX_DAILY_LOSS_USD,
    MAX_TRADES_PER_DAY,
    MAX_TRADES_PER_HOUR,
    MAX_OPEN_POSITIONS,
    MIN_PRICE_USD,
    MAX_ORDER_QTY,
)
from data.db import (
    trade_count_today,
    is_trading_halted,
    daily_pnl_so_far,
    trades_in_last_hour,
    set_trading_halted,
)


def check_order(ticker, qty, side, price):
    """
    Check whether an order should be allowed.

    Returns:
        (True, "") if allowed
        (False, "reason") if blocked
    """
    # 1. Hard kill switch
    if is_trading_halted():
        return False, "Trading halted (kill switch active)"

    # 2. Daily P&L floor — flip kill switch if exceeded
    pnl = daily_pnl_so_far()
    if pnl <= -MAX_DAILY_LOSS_USD:
        set_trading_halted(f"Daily loss limit hit: ${pnl:.2f}")
        return False, f"Daily loss limit hit: ${pnl:.2f}"

    # 3. Trade count limits
    if trade_count_today() >= MAX_TRADES_PER_DAY:
        return False, f"Daily trade limit ({MAX_TRADES_PER_DAY}) reached"

    if trades_in_last_hour() >= MAX_TRADES_PER_HOUR:
        return False, f"Hourly trade limit ({MAX_TRADES_PER_HOUR}) reached"

    # 4. Order sanity checks
    if qty <= 0:
        return False, "Order qty must be positive"

    if qty > MAX_ORDER_QTY:
        return False, f"Order qty {qty} exceeds sanity limit {MAX_ORDER_QTY}"

    if price < MIN_PRICE_USD:
        return False, f"Price ${price:.2f} below minimum ${MIN_PRICE_USD}"

    notional = qty * price

    if notional > MAX_POSITION_USD:
        return False, (
            f"Position notional ${notional:.2f} exceeds max ${MAX_POSITION_USD}"
        )

    if notional < MIN_POSITION_USD:
        return False, (
            f"Position notional ${notional:.2f} below minimum ${MIN_POSITION_USD}"
        )

    # 5. Position count limit (only for buys; sells reduce positions)
    if side.lower() == "buy":
        from brokers.alpaca import get_positions
        try:
            open_count = len(get_positions())
            if open_count >= MAX_OPEN_POSITIONS:
                return False, (
                    f"Already at max open positions ({MAX_OPEN_POSITIONS})"
                )
        except Exception as e:
            return False, f"Cannot check positions: {e}"

    return True, ""
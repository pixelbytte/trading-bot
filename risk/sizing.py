"""
Position sizing and stop/target calculations.
All exit math lives here.
"""

import pandas as pd
from ta.volatility import AverageTrueRange
from risk.limits import (
    RISK_PER_TRADE_USD,
    MAX_POSITION_USD,
    MIN_POSITION_USD,
    MAX_ORDER_QTY,
)

ATR_PERIOD = 14
STOP_ATR_MULTIPLIER = 1.5     # stop = 1.5 * ATR below entry
TARGET_ATR_MULTIPLIER = 3.0   # target = 3.0 * ATR above entry (so 2:1 reward:risk)


def compute_atr(bars):
    """
    Compute ATR(14) from a list of bars.
    Returns a single float — the current ATR value.
    """
    if len(bars) < ATR_PERIOD + 1:
        return None

    df = pd.DataFrame(bars)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=ATR_PERIOD,
    ).average_true_range()

    return float(atr.iloc[-1])


def compute_stop_target(entry_price, atr, side="buy",
                        stop_mult=None, target_mult=None):
    """
    Given entry price and ATR, compute stop-loss and take-profit prices.

    For a long (buy):
      stop = entry - (stop_mult * ATR)
      target = entry + (target_mult * ATR)

    stop_mult / target_mult default to the module-level constants.
    Pass custom values for the long-term portfolio (wider stops).
    """
    stop_mult = stop_mult if stop_mult is not None else STOP_ATR_MULTIPLIER
    target_mult = target_mult if target_mult is not None else TARGET_ATR_MULTIPLIER
    stop_distance = stop_mult * atr
    target_distance = target_mult * atr

    if side.lower() == "buy":
        stop = entry_price - stop_distance
        target = entry_price + target_distance
    else:
        stop = entry_price + stop_distance
        target = entry_price - target_distance

    return round(stop, 2), round(target, 2)


def compute_position_size(entry_price, stop_price, risk_override=None):
    """
    Size the position so we risk RISK_PER_TRADE_USD if the stop hits.

    Formula: qty = risk_dollars / (entry_price - stop_price)

    risk_override: pass a custom dollar risk amount (e.g. 1% of current equity
    for proportional compounding). If None, uses the hardcoded RISK_PER_TRADE_USD.

    Returns int qty (whole shares only).
    """
    risk = risk_override if risk_override is not None else RISK_PER_TRADE_USD
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0

    raw_qty = risk / stop_distance
    qty = int(raw_qty)  # round down

    # Cap by max position size (scale with risk_override if provided)
    max_pos = MAX_POSITION_USD
    if risk_override is not None:
        max_pos = risk_override * 15  # keep 15x risk = 15% of account cap

    notional = qty * entry_price
    if notional > max_pos:
        qty = int(max_pos / entry_price)

    qty = min(qty, MAX_ORDER_QTY)

    if qty * entry_price < MIN_POSITION_USD:
        return 0

    return max(0, qty)


def dynamic_risk_usd(account_equity: float) -> float:
    """Return 1% of current equity as the risk-per-trade amount.
    Used for proportional (compounding) position sizing."""
    return max(account_equity * 0.01, 25.0)  # floor at $25
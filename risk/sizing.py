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


def compute_stop_target(entry_price, atr, side="buy"):
    """
    Given entry price and ATR, compute stop-loss and take-profit prices.

    For a long (buy):
      stop = entry - (STOP_MULT * ATR)
      target = entry + (TARGET_MULT * ATR)

    For a short (sell):
      stop = entry + (STOP_MULT * ATR)
      target = entry - (TARGET_MULT * ATR)
    """
    stop_distance = STOP_ATR_MULTIPLIER * atr
    target_distance = TARGET_ATR_MULTIPLIER * atr

    if side.lower() == "buy":
        stop = entry_price - stop_distance
        target = entry_price + target_distance
    else:
        stop = entry_price + stop_distance
        target = entry_price - target_distance

    return round(stop, 2), round(target, 2)


def compute_position_size(entry_price, stop_price):
    """
    Size the position so we risk RISK_PER_TRADE_USD if the stop hits.

    Formula: qty = risk_dollars / (entry_price - stop_price)

    Then clamp to:
      - max position notional (MAX_POSITION_USD)
      - max sanity qty (MAX_ORDER_QTY)
      - min position notional (MIN_POSITION_USD)

    Returns int qty (whole shares only for now).
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0

    raw_qty = RISK_PER_TRADE_USD / stop_distance
    qty = int(raw_qty)  # round down

    # Cap by max position size
    notional = qty * entry_price
    if notional > MAX_POSITION_USD:
        qty = int(MAX_POSITION_USD / entry_price)

    # Cap by sanity limit
    qty = min(qty, MAX_ORDER_QTY)

    # Reject if too small
    if qty * entry_price < MIN_POSITION_USD:
        return 0

    return max(0, qty)
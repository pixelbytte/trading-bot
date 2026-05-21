"""
India risk limits — mirrors risk/limits.py but in INR for the Upstox bot.
All strategies and check.py import from here when running India mode.
"""

from config.india_settings import (
    ACCOUNT_SIZE_INR,
    MAX_POSITION_INR,
    MIN_POSITION_INR,
    RISK_PER_TRADE_INR,
    MAX_DAILY_LOSS_INR,
    MAX_OPEN_POSITIONS,
    MAX_TRADES_PER_DAY,
    MAX_TRADES_PER_HOUR,
    MAX_ORDER_QTY,
    MIN_PRICE_INR,
    MIN_AVG_VOLUME,
)

# Pre-loss warning fires at 80% of daily stop
PRE_LOSS_WARNING_INR = MAX_DAILY_LOSS_INR * 0.80   # ₹60,000

# Maximum concentration in any single stock
MAX_CONCENTRATION_PCT = 0.20   # 20% — slightly tighter than US bot (15%)

# Circuit breaker thresholds — Indian markets have upper/lower circuits.
# Skip a stock if it's within 2% of its circuit limit to avoid partial fills.
CIRCUIT_BUFFER_PCT = 0.02

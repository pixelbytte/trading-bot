"""
Hardcoded risk limits.
THESE VALUES MUST NEVER BE OVERRIDDEN BY STRATEGIES OR LLMS.
Tuned for a $5,000 pretend account.
"""

from config.settings import ACCOUNT_SIZE_USD

# Per-position limits
MAX_POSITION_USD = 750               # 15% of $5,000
MIN_POSITION_USD = 25                # below this, not worth the slippage
RISK_PER_TRADE_USD = 50              # 1% risk per trade -> sets stop distance

# Daily limits (kill switch territory)
MAX_DAILY_LOSS_USD = 150             # 3% daily stop -> halt trading
MAX_TRADES_PER_DAY = 10              # prevent overtrading
MAX_TRADES_PER_HOUR = 5              # circuit breaker for runaway loops

# Portfolio limits
MAX_OPEN_POSITIONS = 4
MAX_CONCENTRATION_PCT = 0.30         # one ticker can't be > 30% of portfolio

# Asset filters
MIN_PRICE_USD = 5                    # no penny stocks
MIN_AVG_VOLUME = 500_000             # need liquidity

# Sanity guards (catch bugs)
MAX_ORDER_QTY = 100                  # if a strategy says "buy 10000" something is wrong
ABSURD_PRICE_DELTA_PCT = 0.20        # if quoted price moved 20% from last bar, suspicious

# Display the rules at import time
def describe():
    return f"""
Risk Limits (hardcoded):
  Account size: ${ACCOUNT_SIZE_USD:,.0f}
  Max position: ${MAX_POSITION_USD} ({MAX_POSITION_USD/ACCOUNT_SIZE_USD:.0%} of account)
  Risk per trade: ${RISK_PER_TRADE_USD} ({RISK_PER_TRADE_USD/ACCOUNT_SIZE_USD:.0%} of account)
  Daily loss limit: ${MAX_DAILY_LOSS_USD} ({MAX_DAILY_LOSS_USD/ACCOUNT_SIZE_USD:.0%} of account)
  Max open positions: {MAX_OPEN_POSITIONS}
  Max trades/day: {MAX_TRADES_PER_DAY}
  Max trades/hour: {MAX_TRADES_PER_HOUR}
""".strip()
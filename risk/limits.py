"""
Hardcoded risk limits.
THESE VALUES MUST NEVER BE OVERRIDDEN BY STRATEGIES OR LLMS.
Tuned for a $100,000 pretend account (paper-only, parent-supervised).

Sizing: 1.5% risk/trade, 20% max position, 4% daily kill-switch.
Raised from 1%/15%/3% on 2026-05-23 to increase capital deployment.
"""

from config.settings import ACCOUNT_SIZE_USD

# Per-position limits
MAX_POSITION_USD = 20_000            # 20% of $100,000 (raised from 15%)
MIN_POSITION_USD = 500               # below this, not worth the slippage
RISK_PER_TRADE_USD = 1_500           # 1.5% risk per trade (raised from 1%)

# Daily limits (kill switch territory)
MAX_DAILY_LOSS_USD = 4_000           # 4% daily stop -> halt trading (raised from 3%)
MAX_TRADES_PER_DAY = 10              # prevent overtrading
MAX_TRADES_PER_HOUR = 5               # circuit breaker for runaway loops

# Portfolio limits — day trading
MAX_OPEN_POSITIONS = 4
MAX_CONCENTRATION_PCT = 0.30         # one ticker can't be > 30% of portfolio

# Portfolio limits — long-term holds
# Raised 2026-05-23: 5 positions @ $12k = $60k total (60% LT allocation) vs old
# 3 @ $16k = $48k. Same overall exposure but better single-name risk diversification.
MAX_LONGTERM_POSITIONS = 5           # max simultaneous long-term positions
MAX_LONGTERM_POSITION_USD = 12_000   # 12% of account per long-term position

# Asset filters
MIN_PRICE_USD = 5                    # no penny stocks
MIN_AVG_VOLUME = 500_000             # need liquidity

# Sanity guards (catch bugs)
MAX_ORDER_QTY = 2_000                # 100 made sense at $5k; 2000 for $100k
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
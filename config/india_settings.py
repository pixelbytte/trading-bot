"""India-specific configuration — Upstox / NSE paper+live setup."""

# Working capital for the first 2 months (conservative: use cash, no full margin)
ACCOUNT_SIZE_INR = 25_00_000        # ₹25 lakhs

DAY_TRADING_BUDGET_INR = ACCOUNT_SIZE_INR * 0.40    # ₹10 lakhs intraday
LONG_TERM_BUDGET_INR   = ACCOUNT_SIZE_INR * 0.60    # ₹15 lakhs delivery

# Risk limits — identical % ratios to the US bot
MAX_POSITION_INR    = ACCOUNT_SIZE_INR * 0.15   # ₹3.75 lakhs per position
MIN_POSITION_INR    = 5_000                      # ₹5,000 min
RISK_PER_TRADE_INR  = ACCOUNT_SIZE_INR * 0.01   # ₹25,000 risk per trade (1%)
MAX_DAILY_LOSS_INR  = ACCOUNT_SIZE_INR * 0.03   # ₹75,000 daily kill switch (3%)
MAX_OPEN_POSITIONS  = 6
MAX_TRADES_PER_DAY  = 15
MAX_TRADES_PER_HOUR = 5
MAX_ORDER_QTY       = 5_000         # sanity cap on qty per order
MIN_PRICE_INR       = 50            # no stocks below ₹50
MIN_AVG_VOLUME      = 5_00_000      # 5 lakh shares/day minimum liquidity

# NSE Watchlist — Nifty 50 quality names + high-growth picks
# Use exact NSE trading symbols (what Upstox's instrument master uses)
NSE_WATCHLIST = [
    # Banking & Finance — most liquid sector on NSE
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJFINANCE",

    # IT Services — dollar-earning, range-bound, good for momentum
    "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM",

    # Consumer / Retail — strong brand moats
    "HINDUNILVR", "NESTLEIND", "ASIANPAINT", "TITAN", "TRENT",

    # Industrial & Auto — capex cycle plays
    "LT", "MARUTI", "M&M", "BAJAJ-AUTO", "EICHERMOT",

    # Energy & Infra
    "RELIANCE", "NTPC", "POWERGRID",

    # Pharma — defensive + export earnings
    "SUNPHARMA", "DRREDDY", "CIPLA",

    # High-growth / new-age — volatile, higher ROI potential
    "IRCTC",        # Monopoly on online rail ticketing, high margins
    "ADANIPORTS",   # Largest port operator, infrastructure moat
    "TATACONSUM",   # Consumer staples + beverages, acquisitive growth
    "DMART",        # DMart: everyday low price retail, high ROCE
    "DIXON",        # Dixon Technologies: electronics manufacturing, import substitution
    "PERSISTENT",   # Persistent Systems: mid-cap IT, strong revenue growth
]

# Nifty 50 index symbol for regime detection (Upstox uses ETF as proxy)
REGIME_PROXY = "NIFTYBEES"   # ETF tracking Nifty 50

# Market hours (IST = UTC+5:30) — no DST in India
MARKET_OPEN_HOUR    = 9
MARKET_OPEN_MINUTE  = 15
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30

# Intraday square-off cutoff — close all MIS positions by this time
SQUAREOFF_HOUR   = 15
SQUAREOFF_MINUTE = 15

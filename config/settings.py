"""Central configuration for the trading bot."""

ACCOUNT_SIZE_USD = 5000

MAX_POSITION_USD = 750
MAX_DAILY_LOSS_USD = 150
RISK_PER_TRADE_USD = 50
MAX_OPEN_POSITIONS = 4
MAX_TRADES_PER_DAY = 10
MIN_PRICE_USD = 5

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "AMD", "JPM", "SPY",
]

# Long-term portfolio: quality companies held for weeks-months.
# Broader sector coverage than the day-trading watchlist.
LONG_TERM_WATCHLIST = [
    # Mega-cap tech (overlaps day-trading — fine, different portfolio type)
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    # Additional tech
    "CRM", "ADBE", "AVGO", "NOW", "QCOM", "ORCL",
    # Financials
    "JPM", "V", "MA", "GS",
    # Healthcare
    "LLY", "UNH", "ABBV",
    # Consumer
    "COST", "WMT", "HD",
    # Industrial
    "CAT", "DE",
    # Energy
    "XOM",
]

DAY_TRADING_BUDGET_USD = ACCOUNT_SIZE_USD * 0.60
LONG_TERM_BUDGET_USD = ACCOUNT_SIZE_USD * 0.40

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"
USE_PAPER = True
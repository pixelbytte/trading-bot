"""Central configuration for the trading bot."""

ACCOUNT_SIZE_USD = 100_000

# All ratios preserved from the $5k tune — only absolute dollars scaled 20x.
MAX_POSITION_USD = 20_000     # 20% of account
MAX_DAILY_LOSS_USD = 4_000    # 4% daily kill switch
RISK_PER_TRADE_USD = 1_500    # 1.5% risk per trade
MAX_OPEN_POSITIONS = 4
MAX_TRADES_PER_DAY = 10
MIN_PRICE_USD = 5

WATCHLIST = [
    # Original 10 — mega-cap tech + SPY
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "AMD", "JPM", "SPY",
    # Tier 1 expansion — high-momentum large caps
    "CRWD", "NFLX", "UBER", "PLTR", "MU",
    # Financials / payments
    "V", "MA", "GS", "BAC",
    # Healthcare / consumer / energy
    "LLY", "UNH", "COST", "HD", "XOM", "AVGO",
    # High-growth cheap stocks: liquid, volatile, AI/tech catalysts
    "SOUN",  # SoundHound AI — 27M avg vol, 99% revenue growth, AI voice
    "MRVL",  # Marvell — custom chips for hyperscalers, NVIDIA-endorsed
    "IONQ",  # IonQ — quantum computing, high volatility
    "HIMS",  # Hims & Hers — telehealth/GLP-1, fast growth
]

# Long-term portfolio: quality companies held for weeks-months.
# Curated for structural tailwinds + Stage 2 uptrend entries.
# Removed: ADBE/CRM/ORCL/NOW (poor Stage 2 results in backtest).
# Sunday deep research (Opus) supplements this list with weekly picks.
LONG_TERM_WATCHLIST = [
    # Mega-cap tech — proven compounders
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",

    # Semiconductors — AI hardware supercycle
    "AVGO",   # Broadcom: custom AI chips for Apple/Google, 50%+ FCF margins
    "QCOM",   # Qualcomm: mobile + automotive + AI edge chips
    "ARM",    # ARM Holdings: architecture for every AI chip ever built, royalty model

    # AI infrastructure & software
    "PLTR",   # Palantir: AI platform govt + enterprise, accelerating commercial growth
    "APP",    # AppLovin: mobile ad platform, 70%+ EBITDA margins, fastest-growing ad co

    # Power (AI data centers need massive electricity)
    "CEG",    # Constellation Energy: nuclear renaissance, locked in Microsoft contract
    "VST",    # Vistra Energy: nuclear + gas power, pricing power from AI demand

    # Defense tech
    "AXON",   # Axon Enterprise: tasers + AI body cams + software, 90%+ gross margin on SaaS

    # Financials & payments
    "JPM", "V", "MA", "GS",

    # Healthcare
    "LLY",    # Eli Lilly: GLP-1 drugs (weight loss + diabetes), pipeline is deepest in pharma
    "UNH",    # UnitedHealth: manages care + owns hospitals + PBM, durable compounder
    "ABBV",   # AbbVie: Skyrizi/Rinvoq replacing Humira revenue, strong pipeline
    "ISRG",   # Intuitive Surgical: robotic surgery near-monopoly, installed base compounds

    # Consumer
    "COST",   # Costco: membership model, pricing power, deflationary for consumers
    "WMT",    # Walmart: logistics + advertising revenue, e-commerce accelerating
    "CELH",   # Celsius: energy drink brand taking share from Monster/Red Bull

    # Industrial
    "CAT",    # Caterpillar: mining + construction, Stage 2 returned +87.9% last cycle
    "DE",     # Deere: precision ag + autonomous machinery, high switching cost moat
]

# Tickers where VWAP scalp has validated edge (Sharpe > 1.0, 90-day backtest).
# Running on the full WATCHLIST dilutes returns — restrict to this curated list.
# Re-validate if market regime changes significantly.
SCALP_UNIVERSE = ["SOUN", "NFLX", "UNH", "CRWD", "GOOGL", "V", "MA"]

DAY_TRADING_BUDGET_USD = ACCOUNT_SIZE_USD * 0.40
LONG_TERM_BUDGET_USD = ACCOUNT_SIZE_USD * 0.60

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"
USE_PAPER = True
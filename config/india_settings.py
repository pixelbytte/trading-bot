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

# NSE Watchlist — curated from 500-day backtest (2026-05-23).
# Only stocks with positive expectancy in at least one strategy are kept.
# Removed: INFY/WIPRO (trend-following fails), CHOLAFIN/TATAPOWER/PFC/RECLTD/SJVN
#   (range-bound in correction), BAJAJ-AUTO/HAVELLS/HINDUNILVR/NESTLEIND/ASIANPAINT
#   (all negative across strategies), SUNPHARMA/CIPLA/DIVISLAB (momentum negative),
#   IRCTC/MAZDOCK/COFORGE/PERSISTENT (late-cycle or weak edge).
NSE_WATCHLIST = [
    # ── Banking & Finance ─────────────────────────────────────────────
    # Momentum works well here: SBIN +0.314R, AXISBANK +0.127R, BAJFINANCE +1.039R
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJFINANCE",
    "CANBK",        # momentum +0.161R — PSU re-rating still running
    "MUTHOOTFIN",   # bounce +1.035R — gold loans spike on FII selling days

    # ── IT Services — only the ones with positive backtested edge ─────
    # TCS/HCLTECH/TECHM show flat-to-positive; INFY/WIPRO are dead weight
    "TCS", "HCLTECH", "TECHM",

    # ── Consumer / Retail ─────────────────────────────────────────────
    # TITAN +0.438R (bounce), TRENT +0.475R, DMART +1.135R, TATACONSUM +0.956R
    "TITAN", "TRENT", "TATACONSUM", "DMART",

    # ── Industrial & Auto ─────────────────────────────────────────────
    # MARUTI +0.470R, M&M +0.396R, TVSMOTOR +0.829R, POLYCAB (top performer)
    "LT", "MARUTI", "M&M", "EICHERMOT", "TVSMOTOR",
    "POLYCAB",      # STAR: momentum +0.470R (+1.29L in 500d), ma_rsi +0.776R

    # ── Energy & Infra ────────────────────────────────────────────────
    # POWERGRID +1.153R momentum, ADANIPORTS +0.657R — positive expectancy
    "RELIANCE", "POWERGRID",
    "ADANIPORTS",   # STAR: momentum +0.657R (+1.31L in 500d)

    # ── Defence ───────────────────────────────────────────────────────
    # BEL/HAL still showing positive edge; MAZDOCK is late-cycle — removed
    "BEL", "HAL",

    # ── Pharma ────────────────────────────────────────────────────────
    # DRREDDY +0.453R, AUROPHARMA +1.139R — pharma exports holding up
    "DRREDDY", "AUROPHARMA",

    # ── Telecom / Digital ─────────────────────────────────────────────
    "BHARTIARTL",   # momentum +0.546R — ARPU upgrade cycle
    "CDSL",         # marginal but demat monopoly with long-term tailwind

    # ── High-growth ───────────────────────────────────────────────────
    # DIXON +0.872R momentum, TATACONSUM already above
    "DIXON",        # India's Foxconn — import substitution momentum
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

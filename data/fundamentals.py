"""
Financial Modeling Prep (FMP) API client for fundamental data (Day 23).

Free tier: 250 requests/day. At 2 calls per ticker × 26 tickers = 52 calls/run.
Well within limits.

Setup:
  1. Sign up at https://financialmodelingprep.com/developer/docs/ (free)
  2. Add FMP_KEY=your_key to .env (local) and GitHub Secrets (cloud)

If FMP_KEY is missing or the API call fails, returns None — all callers fail open.
"""

import os
import time
import requests
from utils.logger import warning

FMP_KEY = os.getenv("FMP_KEY")
FMP_BASE = "https://financialmodelingprep.com/api/v3"
TIMEOUT = 10
_CALL_DELAY = 0.25   # seconds between requests — avoids rate-limit on free tier


def _get(path, params=None):
    """GET one FMP endpoint. Returns parsed JSON or None on any failure."""
    if not FMP_KEY:
        return None
    url = f"{FMP_BASE}{path}"
    p = {"apikey": FMP_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        warning(f"FMP {path}: {e}", source="fundamentals")
        return None


def get_fundamentals(ticker):
    """
    Fetch key fundamental metrics for a ticker via FMP.

    Returns a dict with standardized fields, or None if FMP_KEY is absent
    or the API call fails (callers must handle None gracefully).

    Fields:
        pe_ratio         P/E ratio (TTM)
        eps_growth       EPS growth YoY  (0.20 = 20%)
        revenue_growth   Revenue growth YoY
        debt_to_equity   Total debt / total equity
        gross_margin     Gross profit / revenue
        fcf_yield        Free cash flow / market cap
        market_cap       Market capitalisation in USD
    """
    metrics = _get(f"/key-metrics-ttm/{ticker}")
    time.sleep(_CALL_DELAY)

    growth = _get(f"/financial-growth/{ticker}", {"limit": 1})
    time.sleep(_CALL_DELAY)

    if not metrics or not isinstance(metrics, list):
        return None

    m = metrics[0] if metrics else {}
    g = (growth[0] if growth and isinstance(growth, list) else {}) or {}

    return {
        "pe_ratio":        float(m.get("peRatioTTM") or 0),
        "eps_growth":      float(g.get("epsgrowth") or 0),
        "revenue_growth":  float(g.get("revenueGrowth") or 0),
        "debt_to_equity":  float(m.get("debtToEquityTTM") or 0),
        "gross_margin":    float(m.get("grossProfitMarginTTM") or 0),
        "fcf_yield":       float(m.get("freeCashFlowYieldTTM") or 0),
        "market_cap":      float(m.get("marketCapTTM") or 0),
    }

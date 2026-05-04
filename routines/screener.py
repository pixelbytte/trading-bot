"""
Long-term ticker screener and ranking (Days 25-26).

Scores every ticker in LONG_TERM_WATCHLIST on three dimensions:
  - Momentum (40%): 3-month price return — are institutions buying?
  - Fundamentals (35%): EPS growth + revenue growth + margin + debt quality
  - Thesis (25%): Claude's buy/hold/avoid conviction from Sunday thesis run

Returns a ranked list so longterm.py enters the highest-quality setups first
when multiple Stage 2 signals fire on the same day.
"""

from data.db import get_fundamentals, get_latest_thesis
from config.settings import LONG_TERM_WATCHLIST
from utils.logger import info, warning

MOMENTUM_WEIGHT = 0.40
FUNDAMENTAL_WEIGHT = 0.35
THESIS_WEIGHT = 0.25

MOMENTUM_WINDOW = 63   # ~3 calendar months of trading days


def _momentum_score(bars):
    """
    3-month return normalised to 0-1.
    −40% → 0.0  |  flat → 0.5  |  +40% → 1.0
    """
    if not bars or len(bars) < MOMENTUM_WINDOW + 2:
        return 0.5
    closes = [float(b["close"]) for b in bars]
    ret = (closes[-1] - closes[-(MOMENTUM_WINDOW + 1)]) / closes[-(MOMENTUM_WINDOW + 1)]
    return max(0.0, min(1.0, (ret + 0.40) / 0.80))


def _fundamental_score(fund):
    """
    Composite of EPS growth, revenue growth, gross margin, and debt/equity.
    Returns 0-1; 0.5 when no data (neutral, not punished).
    """
    if not fund:
        return 0.5

    # EPS growth: −10% → 0.0, flat → 0.25, +30% → 1.0
    eps = max(0.0, min(1.0, (fund["eps_growth"] + 0.10) / 0.40))

    # Revenue growth: −5% → 0.0, flat → 0.17, +25% → 1.0
    rev = max(0.0, min(1.0, (fund["revenue_growth"] + 0.05) / 0.30))

    # Gross margin: 0% → 0.0, 60%+ → 1.0 (tech-calibrated)
    margin = max(0.0, min(1.0, fund["gross_margin"] / 0.60))

    # Debt/equity: 0 → 1.0, 2.0+ → 0.0 (less debt = better)
    debt = max(0.0, min(1.0, 1.0 - fund["debt_to_equity"] / 2.0))

    return 0.35 * eps + 0.35 * rev + 0.20 * margin + 0.10 * debt


def _thesis_score(thesis):
    """
    Convert Claude's thesis sentiment + conviction to 0-1.
    avoid=−1 → 0.1  |  hold=0 → 0.5  |  buy=+1 → 0.9
    Conviction amplifies toward the extremes.
    """
    if not thesis:
        return 0.5
    sentiment = float(thesis.get("sentiment", 0) or 0)
    conviction = float(thesis.get("conviction", 0.5) or 0.5)
    raw = (sentiment + 1.0) / 2.0           # map −1..1 → 0..1
    return 0.5 + (raw - 0.5) * conviction   # conviction pulls toward 0 or 1


def score_ticker(ticker, bars):
    """
    Composite 0-100 score for a ticker.
    Higher = better long-term entry candidate right now.
    """
    fund = get_fundamentals(ticker)
    thesis = get_latest_thesis(ticker)

    m = _momentum_score(bars)
    f = _fundamental_score(fund)
    t = _thesis_score(thesis)

    composite = MOMENTUM_WEIGHT * m + FUNDAMENTAL_WEIGHT * f + THESIS_WEIGHT * t
    return round(composite * 100, 1)


def rank_longterm_watchlist(all_bars):
    """
    Score and rank all LONG_TERM_WATCHLIST tickers.
    Returns list of (ticker, score) sorted descending by score.
    Tickers with no bars are omitted.
    """
    scores = []
    for ticker in LONG_TERM_WATCHLIST:
        bars = all_bars.get(ticker)
        if not bars:
            continue
        try:
            s = score_ticker(ticker, bars)
            scores.append((ticker, s))
        except Exception as e:
            warning(f"{ticker}: score error: {e}", source="screener")

    ranked = sorted(scores, key=lambda x: -x[1])
    for rank, (ticker, score) in enumerate(ranked[:5], 1):
        info(f"Screener rank #{rank}: {ticker} = {score:.1f}/100", source="screener")
    return ranked

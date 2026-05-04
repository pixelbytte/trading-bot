"""
Thesis generation routine (Day 24).
Runs every Sunday at 7am ET via thesis.yml — before the weekly review.

For each long-term watchlist ticker:
  1. Fetches fresh fundamentals from FMP and stores to DB
  2. Gets recent price action from Alpaca
  3. Asks Claude Haiku for a buy / hold / avoid verdict with reasoning
  4. Stores result in llm_outputs (source="thesis")

The screener and longterm routine read these thesis scores when ranking entries.
Fails open throughout — if FMP is down or Claude is unavailable, existing cached
data is used or the ticker gets a neutral score.
"""

import sys
import os
import json

from data.fundamentals import get_fundamentals as fetch_live_fundamentals
from data.db import (
    init_schema, store_fundamentals, get_fundamentals, log_llm_output,
)
from brokers.alpaca import get_bars
from config.settings import LONG_TERM_WATCHLIST
from utils.logger import info, warning, error
from utils.discord import send_info, send_error

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")


def _price_context(bars):
    """Extract key price metrics for the thesis prompt."""
    if not bars or len(bars) < 65:
        return None
    closes = [float(b["close"]) for b in bars]
    n = len(closes)
    price = closes[-1]
    ret_3m = (price - closes[-(63 + 1)]) / closes[-(63 + 1)] * 100
    ret_1m = (price - closes[-(21 + 1)]) / closes[-(21 + 1)] * 100
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200 if n >= 200 else None
    high_52w = max(closes[-252:]) if n >= 252 else max(closes)
    pct_from_high = (high_52w - price) / high_52w * 100
    return {
        "price": round(price, 2),
        "ret_1m_pct": round(ret_1m, 1),
        "ret_3m_pct": round(ret_3m, 1),
        "above_sma50": price > sma50,
        "above_sma200": (price > sma200) if sma200 else "N/A",
        "pct_from_52w_high": round(pct_from_high, 1),
    }


def _generate_thesis(ticker, bars, fund):
    """
    Ask Claude Haiku for a buy/hold/avoid verdict.
    Returns (verdict, conviction, thesis_text) or None on failure.
    """
    if not ANTHROPIC_KEY:
        return None

    px = _price_context(bars)
    if not px:
        return None

    if fund:
        fund_text = (
            f"P/E: {fund['pe_ratio']:.1f} | "
            f"EPS growth: {fund['eps_growth']*100:+.1f}% | "
            f"Revenue growth: {fund['revenue_growth']*100:+.1f}% | "
            f"Gross margin: {fund['gross_margin']*100:.1f}% | "
            f"Debt/equity: {fund['debt_to_equity']:.2f} | "
            f"FCF yield: {fund['fcf_yield']*100:.1f}%"
        )
    else:
        fund_text = "No fundamental data available."

    price_text = (
        f"Price: ${px['price']:.2f} | "
        f"1M: {px['ret_1m_pct']:+.1f}% | 3M: {px['ret_3m_pct']:+.1f}% | "
        f"Above SMA50: {px['above_sma50']} | Above SMA200: {px['above_sma200']} | "
        f"{px['pct_from_52w_high']:.1f}% from 52W high"
    )

    prompt = (
        f"You are a systematic long-term equity analyst.\n"
        f"Assess {ticker} for a 3-6 month hold in a paper trading portfolio.\n\n"
        f"PRICE ACTION:\n{price_text}\n\n"
        f"FUNDAMENTALS:\n{fund_text}\n\n"
        f"Consider: (1) Is the business growing? (2) Is the stock in an uptrend? "
        f"(3) Is valuation reasonable for the growth rate?\n\n"
        f"Return ONLY this JSON, no other text:\n"
        f'{{"verdict": "buy" or "hold" or "avoid", '
        f'"conviction": <0.0-1.0>, "thesis": "<2-3 sentences>"}}'
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(msg.content[0].text.strip())
        verdict = str(data.get("verdict", "hold")).lower()
        if verdict not in ("buy", "hold", "avoid"):
            verdict = "hold"
        conviction = max(0.0, min(1.0, float(data.get("conviction", 0.5))))
        thesis_text = str(data.get("thesis", ""))[:400]
        return verdict, conviction, thesis_text
    except json.JSONDecodeError:
        warning(f"{ticker}: Claude thesis parse error", source="thesis")
        return None
    except Exception as e:
        warning(f"{ticker}: thesis generation failed: {e}", source="thesis")
        return None


def run_thesis():
    """Generate and store thesis for every long-term watchlist ticker."""
    info("Thesis generation starting", source="thesis")
    init_schema()

    verdicts = {"buy": 0, "hold": 0, "avoid": 0, "error": 0}
    _SENTIMENT_MAP = {"buy": 1.0, "hold": 0.0, "avoid": -1.0}

    for ticker in LONG_TERM_WATCHLIST:
        try:
            # Refresh fundamentals from FMP, fall back to cached DB values
            fund_live = fetch_live_fundamentals(ticker)
            if fund_live:
                store_fundamentals(ticker, fund_live)
            fund = fund_live or get_fundamentals(ticker)

            bars = get_bars(ticker, days=400)

            result = _generate_thesis(ticker, bars, fund)
            if not result:
                verdicts["error"] += 1
                continue

            verdict, conviction, thesis_text = result
            sentiment = _SENTIMENT_MAP.get(verdict, 0.0)

            log_llm_output(
                source="thesis",
                ticker=ticker,
                output_type="buy_hold_avoid",
                content=thesis_text,
                conviction=conviction,
                sentiment=sentiment,
            )

            verdicts[verdict] += 1
            info(
                f"{ticker}: {verdict.upper()} ({conviction:.0%}) — {thesis_text[:80]}",
                source="thesis",
            )

        except Exception as e:
            error(f"{ticker}: thesis error: {e}", source="thesis", exc=e)
            verdicts["error"] += 1

    summary = (
        f"**Sunday Thesis Update — {len(LONG_TERM_WATCHLIST)} tickers**\n"
        f"BUY: {verdicts['buy']}  |  HOLD: {verdicts['hold']}  |  "
        f"AVOID: {verdicts['avoid']}  |  ERROR: {verdicts['error']}"
    )
    send_info(summary)
    info("Thesis generation complete", source="thesis")


if __name__ == "__main__":
    try:
        run_thesis()
    except Exception as e:
        error(f"Thesis routine crashed: {e}", source="thesis", exc=e)
        send_error(f"Thesis routine crashed: {e}")
        sys.exit(1)

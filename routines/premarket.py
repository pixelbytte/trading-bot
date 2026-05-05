"""
Pre-market news sentiment scan (Day 15).
Runs at 8:30am ET via premarket.yml, before any intraday cycles fire.

Flow:
  1. Fetch last 20h of headlines for each watchlist ticker (Alpaca News API).
  2. Ask Claude to score sentiment as JSON (-1.0 bearish → +1.0 bullish).
  3. Store each result in llm_outputs table.
  4. Intraday routine reads these scores and skips buys on bearish tickers.

New secret required: ANTHROPIC_KEY — add it to GitHub Secrets and your .env.
"""

import os
import sys
import json
from datetime import datetime, timedelta

from config.settings import WATCHLIST
from data.db import init_schema, log_llm_output
from utils.logger import info, warning, error
from utils.discord import send_info, send_error

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
BEARISH_THRESHOLD = -0.3


def fetch_news(ticker, hours_back=20):
    """
    Fetch recent headlines for ticker via Alpaca News API.
    Returns list of headline strings. Falls back to [] on any failure.
    """
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
        nc = NewsClient(os.getenv("ALPACA_KEY"), os.getenv("ALPACA_SECRET"))
        start = datetime.now() - timedelta(hours=hours_back)
        req = NewsRequest(symbols=[ticker], start=start, limit=10)
        result = nc.get_news(req)
        items = result.news if hasattr(result, "news") else []
        return [item.headline for item in items[:8] if hasattr(item, "headline")]
    except Exception as e:
        warning(f"{ticker}: news fetch failed ({type(e).__name__}: {e})", source="premarket")
        return []


def score_sentiment(ticker, headlines):
    """
    Call Claude API to score sentiment from headlines.
    Returns (sentiment: float, conviction: float, reason: str).
    Fails open: returns neutral (0.0, 0.2) on any error.
    """
    if not ANTHROPIC_KEY:
        warning("ANTHROPIC_KEY not set — all sentiments will be neutral", source="premarket")
        return 0.0, 0.0, "no api key"

    if not headlines:
        return 0.0, 0.2, "no news found"

    bullets = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        f"Analyze these recent news headlines for {ticker} stock.\n\n"
        f"Headlines:\n{bullets}\n\n"
        f"Return ONLY this JSON, no extra text:\n"
        f'{{ "sentiment": <float -1.0 to 1.0>, "conviction": <float 0.0 to 1.0>, "reason": "<one sentence>" }}\n\n'
        f"Where -1.0 = very bearish, 0.0 = neutral, +1.0 = very bullish. "
        f"conviction = how confident you are given the available headlines."
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        data = json.loads(raw)
        sentiment = max(-1.0, min(1.0, float(data.get("sentiment", 0.0))))
        conviction = max(0.0, min(1.0, float(data.get("conviction", 0.5))))
        reason = str(data.get("reason", ""))[:200]
        return sentiment, conviction, reason
    except json.JSONDecodeError:
        warning(f"{ticker}: Claude response was not valid JSON", source="premarket")
        return 0.0, 0.0, "parse error"
    except Exception as e:
        error(f"{ticker}: Claude API error: {e}", source="premarket", exc=e)
        return 0.0, 0.0, "api error"


def check_breaking_news(ticker: str, minutes_back: int = 60):
    """
    On-demand breaking news check for a single ticker.
    Fetches the last `minutes_back` minutes of headlines and scores sentiment.
    Returns (is_bearish: bool, reason: str).
    Called by intraday.py just before placing a bracket order.
    Fails open on any error — never blocks trading on API outage.
    """
    try:
        headlines = fetch_news(ticker, hours_back=minutes_back / 60)
        if not headlines:
            return False, ""
        sentiment, conviction, reason = score_sentiment(ticker, headlines)
        is_bearish = sentiment < -0.4 and conviction >= 0.5
        return is_bearish, reason
    except Exception as e:
        warning(f"{ticker}: breaking news check failed: {e}", source="premarket")
        return False, ""


def run_premarket():
    """Score sentiment for every watchlist ticker and persist to DB."""
    info("Pre-market news scan starting", source="premarket")
    init_schema()

    results = []
    for ticker in WATCHLIST:
        headlines = fetch_news(ticker)
        sentiment, conviction, reason = score_sentiment(ticker, headlines)

        log_llm_output(
            source="premarket_news",
            ticker=ticker,
            output_type="sentiment",
            content=reason,
            conviction=conviction,
            sentiment=sentiment,
        )

        direction = "+" if sentiment > 0.1 else ("-" if sentiment < -0.1 else "~")
        info(
            f"{ticker}: {direction}{abs(sentiment):.2f} "
            f"(conviction {conviction:.0%}) — {reason}",
            source="premarket",
        )
        results.append((ticker, sentiment, conviction))

    # Discord summary
    bullish = [t for t, s, _ in results if s > 0.3]
    bearish = [t for t, s, _ in results if s < BEARISH_THRESHOLD]
    neutral = [t for t, s, _ in results if BEARISH_THRESHOLD <= s <= 0.3]

    lines = ["**Pre-market sentiment scan complete:**"]
    if bullish:
        lines.append(f"Bullish ({len(bullish)}): {', '.join(bullish)}")
    if bearish:
        lines.append(f"Bearish — buys blocked ({len(bearish)}): {', '.join(bearish)}")
    if neutral:
        lines.append(f"Neutral ({len(neutral)}): {', '.join(neutral)}")
    send_info("\n".join(lines))

    info(
        f"Scan complete. {len(bullish)} bullish, {len(bearish)} bearish, {len(neutral)} neutral.",
        source="premarket",
    )


if __name__ == "__main__":
    try:
        run_premarket()
    except Exception as e:
        error(f"Pre-market scan crashed: {e}", source="premarket", exc=e)
        send_error(f"Pre-market scan crashed: {e}")
        sys.exit(1)

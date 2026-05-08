"""
Pre-market news sentiment scan (Day 15).
Runs at 8:30am ET via premarket.yml, before any intraday cycles fire.

Flow:
  1. Fetch last 20h of headlines for each watchlist ticker (Alpaca News API).
  2. Ask Claude to score sentiment as JSON (-1.0 bearish → +1.0 bullish).
  3. Store each result in llm_outputs table.
  4. Intraday routine reads these scores and skips buys on bearish tickers.

  Also runs a deal signal scan over a wider "picks and shovels" universe:
  optical fiber, networking, power, and cooling suppliers that benefit from
  AI infrastructure deals (the Corning-NVIDIA pattern). Discord alert fires
  when a new partnership, supply agreement, or major investment is detected.

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

# Picks-and-shovels tickers: AI infrastructure suppliers not in the main
# trading watchlist but useful as early-signal canaries.
# Pattern: Corning (GLW) signed a Meta fiber deal in Q1 earnings (Apr 28),
# then the NVIDIA $3.2B deal hit mainstream May 6 — 8 days later.
# Watching these for new partnership/investment announcements early.
DEAL_SIGNAL_UNIVERSE = {
    "GLW",   # Corning — optical fiber for data centers
    "COHR",  # Coherent — lasers/photonics (NVIDIA invested $4B Mar 2026)
    "LITE",  # Lumentum — optical components (NVIDIA invested $4B Mar 2026)
    "MRVL",  # Marvell — custom ASICs + networking, NVIDIA-endorsed
    "ANET",  # Arista Networks — hyperscaler network switches
    "VRT",   # Vertiv — data center power + cooling
    "GEV",   # GE Vernova — grid power for AI data centers
    "ONTO",  # Onto Innovation — semiconductor inspection equipment
}


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


def detect_deal_signal(ticker: str, headlines: list) -> dict | None:
    """
    Scan headlines for partnership/investment/supply deal announcements.
    Returns a dict if a high-confidence deal signal is found, else None.

    What to look for (the Corning-NVIDIA pattern):
      - New multi-year supply or manufacturing agreement
      - Strategic investment / equity warrant from a hyperscaler or chip co
      - New factory / capacity expansion tied to a named customer
      - "Long-term partnership" with a named tech company

    Fails open — returns None on any error so the main scan never breaks.
    """
    if not ANTHROPIC_KEY or not headlines:
        return None

    bullets = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        f"You are scanning news headlines for {ticker} stock looking for "
        f"early-signal deal announcements that haven't yet gone mainstream.\n\n"
        f"Headlines:\n{bullets}\n\n"
        f"Does any headline suggest a NEW: partnership, supply agreement, "
        f"manufacturing investment, multi-year contract, equity warrant, or "
        f"strategic relationship with a major tech company (hyperscaler, chip co)?\n\n"
        f"Return ONLY this JSON, no extra text:\n"
        f'{{"deal_detected": <bool>, "deal_type": "<partnership|supply|investment|capacity|none>", '
        f'"partner": "<company name or unknown>", "significance": <float 0.0-1.0>, '
        f'"summary": "<one sentence max>"}}\n\n'
        f"significance: 0.0 = minor/vague, 1.0 = major named deal with dollar figures. "
        f"Only set deal_detected=true if you are confident this is a real new deal announcement."
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        data = json.loads(raw)
        if data.get("deal_detected") and float(data.get("significance", 0)) >= 0.5:
            return {
                "ticker": ticker,
                "deal_type": data.get("deal_type", "unknown"),
                "partner": data.get("partner", "unknown"),
                "significance": float(data.get("significance", 0)),
                "summary": str(data.get("summary", ""))[:300],
            }
        return None
    except Exception as e:
        warning(f"{ticker}: deal scan error: {e}", source="premarket")
        return None


def scan_deal_signals():
    """
    Scan the picks-and-shovels universe for early deal signals.
    Sends a Discord alert for any high-confidence finding.
    Does not affect trading — purely informational.
    """
    info("Deal signal scan starting", source="premarket")
    found = []

    for ticker in DEAL_SIGNAL_UNIVERSE:
        try:
            headlines = fetch_news(ticker, hours_back=20)
            result = detect_deal_signal(ticker, headlines)
            if result:
                found.append(result)
                info(
                    f"DEAL SIGNAL [{ticker}]: {result['deal_type']} with "
                    f"{result['partner']} (sig={result['significance']:.2f}) — "
                    f"{result['summary']}",
                    source="premarket",
                )
                log_llm_output(
                    source="deal_scan",
                    ticker=ticker,
                    output_type="deal_signal",
                    content=result["summary"],
                    conviction=result["significance"],
                    sentiment=0.8,
                )
        except Exception as e:
            warning(f"{ticker}: deal scan failed: {e}", source="premarket")

    if found:
        lines = ["**DEAL SIGNAL ALERT** (picks-and-shovels scan):"]
        for r in found:
            lines.append(
                f"  {r['ticker']} — {r['deal_type']} with {r['partner']} "
                f"(confidence {r['significance']:.0%}): {r['summary']}"
            )
        lines.append(
            "These are early signals, not trade recommendations. "
            "Check SEC 8-K filings and earnings transcripts to confirm."
        )
        send_info("\n".join(lines))
    else:
        info("Deal scan: no new signals detected", source="premarket")


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

    # Deal signal scan runs after sentiment — separate concern, fail-safe
    try:
        scan_deal_signals()
    except Exception as e:
        error(f"Deal signal scan failed: {e}", source="premarket", exc=e)


if __name__ == "__main__":
    try:
        run_premarket()
    except Exception as e:
        error(f"Pre-market scan crashed: {e}", source="premarket", exc=e)
        send_error(f"Pre-market scan crashed: {e}")
        sys.exit(1)

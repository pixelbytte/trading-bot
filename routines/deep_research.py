"""
Sunday deep research routine.
Fires every Sunday 6am ET via deep_research.yml — before thesis.yml (7am).

Uses Claude Sonnet (not Haiku — this needs real reasoning) to identify
5 high-conviction long-term picks targeting 150%+ ROI. Looks beyond the
standard watchlist into up-and-coming sectors and asymmetric setups.

For each pick Claude produces:
  - Full bull case (what makes this 2-3x)
  - Full bear case (what kills it)
  - Key catalysts to watch
  - Entry criteria (technical setup needed)
  - 12-18 month price target with reasoning

Results sent to Discord + stored in llm_outputs (source="deep_research").
"""

import os
import sys
import json
from datetime import datetime, date

from brokers.alpaca import get_bars
from config.settings import LONG_TERM_WATCHLIST
from data.db import init_schema, log_llm_output
from data.fundamentals import get_fundamentals
from utils.logger import info, warning, error
from utils.discord import send_info, send_error

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
MODEL = "claude-opus-4-7"     # Opus — most capable model for high-stakes research


def _build_price_context(all_bars: dict) -> str:
    """Build a compact price/trend summary for all tickers to feed Claude."""
    lines = []
    for ticker, bars in sorted(all_bars.items()):
        if len(bars) < 50:
            continue
        closes = [float(b["close"]) for b in bars]
        current = closes[-1]
        sma50  = sum(closes[-50:]) / 50
        sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        ret_3m = (current / closes[-63] - 1) * 100 if len(closes) >= 63 else None
        ret_1y = (current / closes[-252] - 1) * 100 if len(closes) >= 252 else None

        above_50  = "above" if current > sma50 else "below"
        above_200 = ("above" if sma200 and current > sma200 else "below") if sma200 else "N/A"

        parts = [f"{ticker}: ${current:.2f}  SMA50={above_50}  SMA200={above_200}"]
        if ret_3m is not None:
            parts.append(f"3M={ret_3m:+.1f}%")
        if ret_1y is not None:
            parts.append(f"1Y={ret_1y:+.1f}%")
        lines.append("  " + "  ".join(parts))
    return "\n".join(lines)


def _build_fundamentals_context(tickers: list) -> str:
    """Pull latest fundamentals for watchlist tickers as context."""
    lines = []
    for ticker in tickers[:15]:  # limit to avoid huge prompts
        try:
            f = get_fundamentals(ticker)
            if not f:
                continue
            parts = [f"{ticker}:"]
            if f.get("pe_ratio"):
                parts.append(f"P/E={f['pe_ratio']:.1f}")
            if f.get("eps_growth"):
                parts.append(f"EPS_growth={f['eps_growth']*100:.1f}%")
            if f.get("revenue_growth"):
                parts.append(f"Rev_growth={f['revenue_growth']*100:.1f}%")
            if f.get("gross_margin"):
                parts.append(f"Margin={f['gross_margin']*100:.1f}%")
            lines.append("  " + "  ".join(parts))
        except Exception:
            pass
    return "\n".join(lines) if lines else "  (fundamentals unavailable)"


def _call_claude(prompt: str) -> str:
    """Call Claude Sonnet and return the raw response text."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _format_discord_report(picks: list, macro: str, sectors: str) -> str:
    """Format the 5 picks into a readable Discord message."""
    lines = [
        "**Sunday Deep Research — 5 High-Conviction Long-Term Picks**",
        f"*{date.today().isoformat()} | Target: 150%+ ROI | 12-18 month horizon*",
        "",
    ]

    if macro:
        lines += ["**Macro Context:**", macro[:400], ""]

    if sectors:
        lines += ["**Emerging Sectors to Watch:**", sectors[:400], ""]

    for i, p in enumerate(picks, 1):
        ticker = p.get("ticker", "?")
        sector = p.get("sector", "")
        target = p.get("price_target_18m", "?")
        current = p.get("current_price", "?")
        roi = p.get("roi_potential", "?")
        confidence = p.get("confidence", 0)

        lines.append(f"**{i}. {ticker}** — {sector}")
        lines.append(f"   Current: ${current}  |  18M Target: ${target}  |  Upside: {roi}  |  Conviction: {confidence:.0%}")
        lines.append(f"   **Bull:** {p.get('bull_case', '')[:200]}")
        lines.append(f"   **Bear:** {p.get('bear_case', '')[:150]}")
        cats = p.get("catalysts", [])
        if cats:
            lines.append(f"   **Catalysts:** {' / '.join(str(c) for c in cats[:3])}")
        entry = p.get("entry_criteria", "")
        if entry:
            lines.append(f"   **Entry when:** {entry[:150]}")
        lines.append("")

    return "\n".join(lines)


def run_deep_research():
    """Main entry point."""
    info("Deep research routine starting", source="deep_research")
    init_schema()

    if not ANTHROPIC_KEY:
        warning("ANTHROPIC_KEY missing — deep research skipped", source="deep_research")
        return

    # Fetch price context for existing watchlist
    info("Fetching price data for watchlist context...", source="deep_research")
    all_bars = {}
    for ticker in LONG_TERM_WATCHLIST:
        try:
            bars = get_bars(ticker, days=400)
            if len(bars) >= 50:
                all_bars[ticker] = bars
        except Exception as e:
            warning(f"{ticker}: bar fetch failed: {e}", source="deep_research")

    price_context = _build_price_context(all_bars)
    fund_context  = _build_fundamentals_context(LONG_TERM_WATCHLIST)

    prompt = f"""You are a professional equity research analyst with a focus on growth investing and identifying asymmetric opportunities. Today is {date.today().isoformat()}.

I need your deep research to identify 5 high-conviction long-term stock picks with 150%+ ROI potential over 12-18 months. These are for a paper trading account for learning purposes.

CURRENT PRICE + TREND CONTEXT (our existing watchlist):
{price_context}

FUNDAMENTALS (where available):
{fund_context}

YOUR RESEARCH TASK:
Think broadly — do NOT limit yourself to the tickers above. Look at:
1. Up-and-coming sectors with massive tailwinds (AI infrastructure, defense tech, biotech breakthroughs, energy transition, robotics, space economy, etc.)
2. Under-the-radar companies at the beginning of an S-curve (market hasn't priced in the full opportunity yet)
3. Market leaders in emerging categories that could 2-3x if the thesis plays out
4. High short-interest stocks with strong fundamentals that could squeeze
5. Companies with upcoming catalysts (FDA approvals, contract wins, product launches, earnings inflections)

For each of your 5 picks, think through both sides rigorously.

Return ONLY valid JSON in this exact format, no other text:
{{
  "macro_context": "<2-3 sentence summary of current macro environment and what it means for growth stocks>",
  "emerging_sectors": "<2-3 sentence summary of the most exciting sectors right now and why>",
  "picks": [
    {{
      "ticker": "<ticker symbol>",
      "company_name": "<full company name>",
      "sector": "<specific sector/industry>",
      "current_price": <approximate current price as a number>,
      "price_target_18m": <18-month price target as a number>,
      "roi_potential": "<% upside as string, e.g. +180%>",
      "confidence": <0.0 to 1.0>,
      "timeframe": "<e.g. 12-18 months>",
      "thesis": "<2-3 sentence core investment thesis — why does this have 150%+ potential?>",
      "bull_case": "<specific scenario where this 2-3x: what needs to go right, what's the mechanism, what's the addressable market?>",
      "bear_case": "<specific scenario where this fails or cuts in half: what are the real risks?>",
      "catalysts": ["<catalyst 1>", "<catalyst 2>", "<catalyst 3>"],
      "risks": ["<risk 1>", "<risk 2>"],
      "entry_criteria": "<what technical setup to wait for before buying — e.g. Stage 2 breakout above $X, pullback to SMA50, etc.>",
      "why_now": "<why is this the right time to be researching this stock?>"
    }}
  ]
}}

Be bold. Be specific. Use real company names and tickers. Don't just pick the obvious mega-caps — find the next NVDA before it becomes NVDA."""

    info("Calling Claude Sonnet for deep research...", source="deep_research")
    try:
        raw = _call_claude(prompt)
    except Exception as e:
        error(f"Claude call failed: {e}", source="deep_research", exc=e)
        send_error(f"Deep research failed: {e}")
        return

    # Parse JSON
    try:
        # Strip markdown code fences if present
        clean = raw
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        error(f"JSON parse failed: {e}\nRaw: {raw[:500]}", source="deep_research")
        # Store raw output anyway
        log_llm_output(
            source="deep_research", ticker=None,
            output_type="research_picks",
            content=raw[:2000], conviction=None, sentiment=None,
        )
        send_error(f"Deep research: Claude response was not valid JSON. Stored raw output.")
        return

    picks = data.get("picks", [])
    macro = data.get("macro_context", "")
    sectors = data.get("emerging_sectors", "")

    info(f"Deep research complete: {len(picks)} picks identified", source="deep_research")

    # Store each pick individually in llm_outputs
    for p in picks:
        ticker = p.get("ticker", "UNKNOWN")
        roi = p.get("roi_potential", "")
        target = p.get("price_target_18m")
        conf = float(p.get("confidence", 0.5))

        # sentiment: 1.0 = strong buy, 0.5 = moderate, 0.0 = neutral
        log_llm_output(
            source="deep_research",
            ticker=ticker,
            output_type="long_term_pick",
            content=json.dumps(p),
            conviction=conf,
            sentiment=conf,  # use confidence as sentiment proxy
        )
        info(f"Stored pick: {ticker} — {roi} upside, conf={conf:.0%}", source="deep_research")

    # Store the full report
    log_llm_output(
        source="deep_research", ticker=None,
        output_type="research_report",
        content=json.dumps(data)[:4000],
        conviction=None, sentiment=None,
    )

    # Send to Discord
    try:
        report = _format_discord_report(picks, macro, sectors)
        # Discord limit 2000 chars per message — split if needed
        chunk_size = 1900
        for i in range(0, len(report), chunk_size):
            send_info(report[i:i + chunk_size])
    except Exception as e:
        error(f"Discord send failed: {e}", source="deep_research", exc=e)

    info("Deep research routine complete", source="deep_research")


if __name__ == "__main__":
    try:
        run_deep_research()
    except Exception as e:
        error(f"Deep research crashed: {e}", source="deep_research", exc=e)
        send_error(f"Deep research crashed: {e}")
        sys.exit(1)

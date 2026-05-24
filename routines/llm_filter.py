"""
LLM signal filter (Day 17-18).

Called for each buy candidate that has passed all mechanical checks.
Loads the entry_signals knowledge base and asks Claude Haiku whether
the technical setup is worth taking. Fails open — if Claude is down or
the key is missing, the trade proceeds without LLM approval.

Returns: (approved: bool, reason: str, conviction: float)
"""

import os
import json
from pathlib import Path

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
_KNOWLEDGE = Path(__file__).parent.parent / "knowledge"


def _load(filename):
    try:
        return (_KNOWLEDGE / filename).read_text()
    except Exception:
        return ""


def _compute_technicals(bars):
    """Extract key technical readings from a bars list."""
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    volumes = [float(b["volume"]) for b in bars]

    price = closes[-1]
    n = len(closes)

    sma10 = sum(closes[-10:]) / 10 if n >= 10 else None
    sma30 = sum(closes[-30:]) / 30 if n >= 30 else None
    sma50 = sum(closes[-50:]) / 50 if n >= 50 else None
    sma200 = sum(closes[-200:]) / 200 if n >= 200 else None

    # RSI(14)
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(max(1, n - 14), n)]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(max(1, n - 14), n)]
    ag = sum(gains) / len(gains) if gains else 0
    al = sum(losses) / len(losses) if losses else 0
    rsi = round(100 - (100 / (1 + ag / al)), 1) if al > 0 else 100.0

    avg_vol = sum(volumes[-20:]) / 20 if n >= 20 else sum(volumes) / n
    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    lookback = min(252, n)
    high_52w = max(highs[-lookback:])
    pct_from_high = round((price - high_52w) / high_52w * 100, 1)

    return {
        "price": price,
        "sma10": sma10,
        "sma30": sma30,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi,
        "vol_ratio": vol_ratio,
        "pct_from_52w_high": pct_from_high,
    }


def analyse_signal(ticker, bars, strategy_name, confidence=0.5):
    """
    Ask Claude whether this buy signal meets quality entry criteria.

    Args:
        ticker: Stock symbol.
        bars: List of OHLCV dicts (needs at least 30 bars).
        strategy_name: Name of the strategy that generated the signal.
        confidence: Strategy-reported confidence (0.0-1.0).

    Returns:
        (approved: bool, reason: str, conviction: float)
    """
    if not ANTHROPIC_KEY:
        return True, "ANTHROPIC_KEY not set — LLM filter skipped", 0.0

    if len(bars) < 30:
        return True, "insufficient bars for LLM analysis", 0.5

    t = _compute_technicals(bars)
    entry_criteria = _load("entry_signals.md")

    def _fmt(val, prefix="$"):
        return f"{prefix}{val:.2f}" if val is not None else "N/A"

    above_below = lambda price, ma: "ABOVE" if (price is not None and ma is not None and price > ma) else "BELOW"

    technicals_text = (
        f"Price: {_fmt(t['price'])}\n"
        f"SMA10: {_fmt(t['sma10'])} — price is {above_below(t['price'], t['sma10'])} SMA10\n"
        f"SMA30: {_fmt(t['sma30'])} — price is {above_below(t['price'], t['sma30'])} SMA30\n"
        f"SMA50: {_fmt(t['sma50'])} — price is {above_below(t['price'], t['sma50'])} SMA50\n"
        f"SMA200: {_fmt(t['sma200'])} — price is {above_below(t['price'], t['sma200'])} SMA200\n"
        f"RSI(14): {t['rsi']}\n"
        f"Volume vs 20-day avg: {t['vol_ratio']}x\n"
        f"Distance from 52-week high: {t['pct_from_52w_high']}%"
    )

    prompt = (
        f"You are a VETO filter for a quantitative trading system. The underlying\n"
        f"strategy is already backtested and statistically positive — your ONLY job\n"
        f"is to block trades that show a SPECIFIC red flag.\n\n"
        f"TICKER: {ticker}\n"
        f"STRATEGY: {strategy_name} (confidence {confidence:.0%})\n\n"
        f"TECHNICAL SNAPSHOT:\n{technicals_text}\n\n"
        f"VETO ONLY IF you see one of these specific red flags:\n"
        f"  - RSI(14) > 80 (parabolic / blow-off top — exhausted)\n"
        f"  - Price > 40% above SMA200 (extremely extended, unsustainable)\n"
        f"  - Price < SMA200 AND distance from 52w high > 30% (broken structure)\n"
        f"  - Volume ratio < 0.3 (illiquid / no participation)\n\n"
        f"DEFAULT IS APPROVE. Do not block for mildly negative readings, weak\n"
        f"momentum, or 'could be better' setups. The strategy already filtered\n"
        f"those. Approve unless one of the red flags above is clearly present.\n\n"
        f"Set conviction to your confidence in the setup quality (0.5 = neutral,\n"
        f"0.7+ = strong setup, used for Kelly position sizing).\n\n"
        f"Reference entry criteria (for conviction grading only, not approval):\n{entry_criteria}\n\n"
        f"Return ONLY this JSON, no other text:\n"
        f'{{ "approved": true or false, "conviction": <0.0-1.0>, "reason": "<one concise sentence>" }}'
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
        approved = bool(data.get("approved", True))
        conviction = max(0.0, min(1.0, float(data.get("conviction", 0.5))))
        reason = str(data.get("reason", ""))[:200]
        return approved, reason, conviction
    except json.JSONDecodeError:
        return True, "LLM response parse error — trade allowed", 0.3
    except Exception as e:
        return True, f"LLM unavailable ({type(e).__name__}) — trade allowed", 0.0

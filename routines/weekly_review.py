"""
Weekly review routine (Day 19).
Runs every Sunday at 5pm ET via weekly_review.yml.

Queries the past 7 days of trades, signals, and sentiment from DuckDB,
then asks Claude to identify patterns and generate a structured review.
Sends the review to Discord. Fails open — if Claude is down, sends raw stats.
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from data.db import init_schema, _connect
from risk.limits import RISK_PER_TRADE_USD
from utils.logger import info, warning, error
from utils.discord import send_info, send_error

ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
_KNOWLEDGE = Path(__file__).parent.parent / "knowledge"


def _load(filename):
    try:
        return (_KNOWLEDGE / filename).read_text()
    except Exception:
        return ""


def _gather_weekly_stats():
    """
    Pull 7 days of trades, signals, and sentiment from DuckDB.
    Returns a dict with all stats needed for the review prompt.
    """
    con = _connect()
    try:
        # Daily P&L for each of the past 7 trading days
        daily_rows = con.execute("""
            SELECT date, pnl, num_trades, wins, losses
            FROM daily_pnl
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY date ASC
        """).fetchall()

        # All trades this week (closed + open)
        trade_rows = con.execute("""
            SELECT ts, ticker, side, qty, price, strategy, pnl, notes
            FROM trades
            WHERE ts >= NOW() - INTERVAL '7 days'
            ORDER BY ts DESC
        """).fetchall()

        # Signal breakdown — acted vs skipped, skip reasons
        signal_rows = con.execute("""
            SELECT strategy, action, acted, skip_reason, COUNT(*) as cnt
            FROM signals
            WHERE ts >= NOW() - INTERVAL '7 days'
              AND action = 'buy'
            GROUP BY strategy, action, acted, skip_reason
            ORDER BY cnt DESC
        """).fetchall()

        # LLM filter stats: how many rejected vs approved
        llm_rows = con.execute("""
            SELECT
                COUNT(*) FILTER (WHERE sentiment > 0) as approved,
                COUNT(*) FILTER (WHERE sentiment < 0) as rejected
            FROM llm_outputs
            WHERE source = 'signal_filter'
              AND ts >= NOW() - INTERVAL '7 days'
        """).fetchone()

        # Sentiment gate stats: bearish blocks
        sentiment_skips = con.execute("""
            SELECT COUNT(*) FROM signals
            WHERE ts >= NOW() - INTERVAL '7 days'
              AND skip_reason LIKE 'bearish news sentiment%'
        """).fetchone()[0]

        # Best and worst trades
        best = con.execute("""
            SELECT ticker, strategy, pnl, price, qty
            FROM trades
            WHERE ts >= NOW() - INTERVAL '7 days'
              AND pnl IS NOT NULL
            ORDER BY pnl DESC LIMIT 3
        """).fetchall()

        worst = con.execute("""
            SELECT ticker, strategy, pnl, price, qty
            FROM trades
            WHERE ts >= NOW() - INTERVAL '7 days'
              AND pnl IS NOT NULL
            ORDER BY pnl ASC LIMIT 3
        """).fetchall()

        # Per-strategy P&L breakdown
        strat_pnl = con.execute("""
            SELECT strategy, COUNT(*) as trades,
                   COUNT(*) FILTER (WHERE pnl > 0) as wins,
                   COUNT(*) FILTER (WHERE pnl < 0) as losses,
                   COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE ts >= NOW() - INTERVAL '7 days'
              AND pnl IS NOT NULL
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """).fetchall()

        # Rolling 20-trade edge score per strategy (Simons: detect pattern decay early)
        # edge_score = mean_R / std_R — un-annualized ratio; drops toward 0 when edge fades
        rolling_rows = con.execute("""
            WITH ranked AS (
                SELECT strategy, pnl,
                       ROW_NUMBER() OVER (PARTITION BY strategy ORDER BY ts DESC) AS rn
                FROM trades
                WHERE pnl IS NOT NULL
            )
            SELECT strategy,
                   COUNT(*)       AS n,
                   AVG(pnl)       AS mean_pnl,
                   STDDEV(pnl)    AS std_pnl
            FROM ranked
            WHERE rn <= 20
            GROUP BY strategy
            ORDER BY strategy
        """).fetchall()

    finally:
        con.close()

    # Format daily P&L summary
    days = []
    for r in daily_rows:
        day_str = r[0].strftime("%a %b %d") if hasattr(r[0], "strftime") else str(r[0])
        closed = (r[3] or 0) + (r[4] or 0)
        wr = f"{r[3]/closed*100:.0f}%" if closed > 0 else "N/A"
        days.append(f"  {day_str}: P&L ${r[1]:+.2f}, {r[2]} trades, win rate {wr}")

    # Weekly totals
    all_trades = [r for r in trade_rows if r[6] is not None]  # closed only
    total_pnl = sum(r[6] for r in all_trades)
    total_wins = sum(1 for r in all_trades if r[6] > 0)
    total_losses = sum(1 for r in all_trades if r[6] < 0)
    closed_count = total_wins + total_losses
    week_wr = f"{total_wins/closed_count*100:.0f}%" if closed_count > 0 else "N/A"

    # Signal filter stats
    total_signals = sum(r[4] for r in signal_rows)
    acted_signals = sum(r[4] for r in signal_rows if r[2])  # acted=True
    skip_reasons = {}
    for r in signal_rows:
        if not r[2] and r[3]:  # not acted, has reason
            reason = r[3][:60]
            skip_reasons[reason] = skip_reasons.get(reason, 0) + r[4]

    llm_approved = llm_rows[0] if llm_rows else 0
    llm_rejected = llm_rows[1] if llm_rows else 0

    # Rolling edge score: mean_R / std_R per strategy (last 20 trades)
    edge_lines = []
    decaying_strategies = []
    for r in rolling_rows:
        strat, n, mean_pnl, std_pnl = r[0], int(r[1]), r[2] or 0.0, r[3] or 0.0
        if n >= 5 and std_pnl > 0:
            mean_r = mean_pnl / RISK_PER_TRADE_USD
            std_r = std_pnl / RISK_PER_TRADE_USD
            edge = round(mean_r / std_r, 2)
            status = "OK" if edge >= 0.30 else ("WARNING" if edge >= 0.0 else "ALERT — edge gone")
            edge_lines.append(f"  {strat}: {edge:+.2f} ({n} trades) [{status}]")
            if edge < 0.0:
                decaying_strategies.append(strat)
        elif n > 0:
            edge_lines.append(f"  {strat}: ({n} trades — need ≥5 to score)")

    # Format best/worst trades
    def fmt_trade(t):
        return f"{t[0]} via {t[1]}: ${t[2]:+.2f} P&L (entry ${t[3]:.2f}, {t[4]:.0f} shares)"

    return {
        "period": "Mon-Sun this week",
        "daily_breakdown": "\n".join(days) if days else "  No daily P&L data yet",
        "total_pnl": total_pnl,
        "closed_trades": closed_count,
        "total_trades_placed": len(trade_rows),
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": week_wr,
        "signals_generated": total_signals,
        "signals_acted": acted_signals,
        "signals_skipped": total_signals - acted_signals,
        "sentiment_blocks": int(sentiment_skips),
        "llm_approved": int(llm_approved),
        "llm_rejected": int(llm_rejected),
        "top_skip_reasons": "\n".join(
            f"  '{k}': {v}x" for k, v in sorted(skip_reasons.items(), key=lambda x: -x[1])[:5]
        ) or "  None",
        "best_trades": "\n".join(f"  {fmt_trade(t)}" for t in best) or "  None",
        "worst_trades": "\n".join(f"  {fmt_trade(t)}" for t in worst) or "  None",
        "strategy_breakdown": "\n".join(
            f"  {r[0]}: {r[1]} trades, {r[2]}W/{r[3]}L, ${r[4]:+.2f} P&L"
            for r in strat_pnl
        ) or "  No data",
        "rolling_edge_scores": "\n".join(edge_lines) or "  No closed trades yet",
        "decaying_strategies": decaying_strategies,
    }


def _ask_claude(stats):
    """
    Call Claude with weekly stats and knowledge base to generate a review.
    Returns the review text, or None if Claude is unavailable.
    """
    if not ANTHROPIC_KEY:
        return None

    risk_kb = _load("risk_management.md")
    psych_kb = _load("psychology.md")

    prompt = f"""You are a trading performance coach reviewing a paper trading bot's weekly results.
The bot trades US large-cap stocks systematically using three strategies on a $100,000 paper account:
- VWAP Scalp (intraday, Sharpe 7.0): 15-min bars, exits by 3:45pm ET
- Momentum (swing, Sharpe 3.1): multi-day trend following
- MA+RSI (swing, Sharpe 1.4): moving average crossover with RSI filter
Plus a long-term Stage2 SEPA bucket ($60k) for weeks-to-months holds.
Risk per trade: $1,000 (1%). Daily stop: $3,000 (3%). 40/60 day-trading vs long-term split.

WEEKLY STATISTICS:
Period: {stats['period']}

Daily P&L breakdown:
{stats['daily_breakdown']}

Weekly totals:
  Net P&L: ${stats['total_pnl']:+.2f}
  Trades placed: {stats['total_trades_placed']} ({stats['closed_trades']} closed)
  Wins: {stats['wins']} | Losses: {stats['losses']} | Win rate: {stats['win_rate']}

Signal pipeline:
  Generated: {stats['signals_generated']} buy signals
  Acted: {stats['signals_acted']} | Skipped: {stats['signals_skipped']}
  Sentiment gate blocks: {stats['sentiment_blocks']}
  LLM filter: {stats['llm_approved']} approved, {stats['llm_rejected']} rejected

Top skip reasons:
{stats['top_skip_reasons']}

Best trades:
{stats['best_trades']}

Worst trades:
{stats['worst_trades']}

Strategy breakdown:
{stats['strategy_breakdown']}

Rolling edge score (mean_R / std_R, last 20 trades per strategy — 0.3+ is healthy, negative = edge gone):
{stats['rolling_edge_scores']}

KNOWLEDGE BASE EXCERPTS:
{risk_kb[:1200]}

{psych_kb[:800]}

Write a concise weekly review with these sections:
1. HEADLINE — one sentence summary of the week
2. WHAT WORKED — 2-3 bullet points
3. WHAT DIDN'T — 2-3 bullet points
4. SIGNAL PIPELINE HEALTH — is the filter chain working? Too many skips? Too many blocks?
5. ONE FOCUS FOR NEXT WEEK — the single most important thing to improve

Keep it under 400 words. Be direct. Use numbers from the data. No fluff."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        warning(f"Claude weekly review failed: {e}", source="weekly_review")
        return None


def run_weekly_review():
    """Main weekly review entry point."""
    info("Weekly review starting", source="weekly_review")

    init_schema()

    stats = _gather_weekly_stats()

    # Try Claude analysis first
    review_text = _ask_claude(stats)

    now = datetime.now().strftime("%Y-%m-%d")

    # Edge decay alert — fires regardless of whether Claude is available
    if stats["decaying_strategies"]:
        send_info(
            f"EDGE DECAY ALERT: {', '.join(stats['decaying_strategies'])} "
            f"showing negative rolling edge score over last 20 trades. "
            f"Review whether to reduce size or pause."
        )

    if review_text:
        message = (
            f"**Weekly Trading Review — {now}**\n\n"
            f"{review_text}\n\n"
            f"---\n"
            f"Raw stats: ${stats['total_pnl']:+.2f} P&L | "
            f"{stats['closed_trades']} closed trades | "
            f"Win rate {stats['win_rate']} | "
            f"{stats['signals_acted']}/{stats['signals_generated']} signals acted\n"
            f"**Rolling edge scores:**\n{stats['rolling_edge_scores']}"
        )
    else:
        # Fallback: send raw stats without Claude analysis
        message = (
            f"**Weekly Trading Review — {now}** _(Claude analysis unavailable)_\n\n"
            f"**Net P&L:** ${stats['total_pnl']:+.2f}\n"
            f"**Trades:** {stats['closed_trades']} closed, win rate {stats['win_rate']}\n"
            f"**Signals:** {stats['signals_acted']} acted / {stats['signals_generated']} generated\n"
            f"**Sentiment blocks:** {stats['sentiment_blocks']} | "
            f"**LLM rejected:** {stats['llm_rejected']}\n\n"
            f"**Daily breakdown:**\n{stats['daily_breakdown']}\n\n"
            f"**Best trades:**\n{stats['best_trades']}\n\n"
            f"**Worst trades:**\n{stats['worst_trades']}\n\n"
            f"**By strategy:**\n{stats['strategy_breakdown']}\n\n"
            f"**Rolling edge scores:**\n{stats['rolling_edge_scores']}"
        )

    send_info(message)
    info("Weekly review sent to Discord", source="weekly_review")


if __name__ == "__main__":
    try:
        run_weekly_review()
    except Exception as e:
        error(f"Weekly review crashed: {e}", source="weekly_review", exc=e)
        send_error(f"Weekly review crashed: {e}")
        sys.exit(1)

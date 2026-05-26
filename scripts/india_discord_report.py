"""
India Discord EOD report — replaces the GitHub Pages dashboard for India.

Reads docs/data.json (the persisted India trade log) and posts:
  1. A summary message with today's P&L, win rate, open/closed counts
  2. A line chart of last-30-day cumulative P&L (PNG attachment)
  3. A per-trade breakdown of today's activity

Run after market close via .github/workflows/india_discord_report.yml.
Discord is the source of truth — messages never disappear like dashboard data does.
"""

import json
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless backend for CI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.discord import send_info, send_image_message


DATA_PATH = Path(__file__).parent.parent / "docs" / "data.json"


def _fmt_inr(val: float) -> str:
    sign = "+" if val >= 0 else "-"
    val = abs(val)
    if val >= 100_000:
        return f"{sign}₹{val/100_000:.2f}L"
    if val >= 1_000:
        return f"{sign}₹{val/1_000:.1f}K"
    return f"{sign}₹{val:.0f}"


def _build_chart(india: dict, out_path: Path) -> bool:
    """Plot last-30-day cumulative P&L for India. Returns True if chart saved."""
    history = india.get("daily_history", [])
    # Some exporters write history under top-level india_daily_history
    if not history:
        return False

    # Newest-first → chronological
    history_sorted = sorted(history, key=lambda d: d.get("date", ""))
    dates = []
    cum_pnl = 0.0
    cum_series = []
    daily_series = []
    for d in history_sorted[-30:]:
        try:
            dt = datetime.fromisoformat(d["date"]).date()
        except (ValueError, KeyError):
            continue
        dates.append(dt)
        daily_series.append(float(d.get("pnl", 0)))
        cum_pnl += float(d.get("pnl", 0))
        cum_series.append(cum_pnl)

    if not dates:
        return False

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6),
                                   gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor("#0d1117")

    for ax in (ax1, ax2):
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.grid(True, color="#21262d", linewidth=0.5)

    # Cumulative P&L line
    line_color = "#3fb950" if cum_pnl >= 0 else "#f85149"
    ax1.plot(dates, cum_series, color=line_color, linewidth=2, marker="o", markersize=4)
    ax1.fill_between(dates, cum_series, 0, color=line_color, alpha=0.15)
    ax1.axhline(0, color="#484f58", linewidth=0.8, linestyle="--")
    ax1.set_title(f"India Paper Bot — Cumulative P&L  ({_fmt_inr(cum_pnl)})",
                  color="#c9d1d9", fontsize=13, pad=12)
    ax1.set_ylabel("Cumulative ₹", color="#8b949e")

    # Daily P&L bars
    bar_colors = ["#3fb950" if p >= 0 else "#f85149" for p in daily_series]
    ax2.bar(dates, daily_series, color=bar_colors, width=0.7)
    ax2.axhline(0, color="#484f58", linewidth=0.8)
    ax2.set_ylabel("Daily ₹", color="#8b949e")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    fig.autofmt_xdate()
    plt.tight_layout()
    plt.savefig(out_path, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return True


def _build_summary(india: dict, today_iso: str) -> str:
    summary = india.get("summary", {})
    trades = india.get("recent_trades", [])
    todays = [t for t in trades if str(t.get("ts", ""))[:10] == today_iso]

    pnl_today = sum(t["pnl"] for t in todays if t.get("pnl") is not None)
    closed_today = [t for t in todays if t.get("pnl") is not None]
    open_today = [t for t in todays if t.get("pnl") is None]
    wins = sum(1 for t in closed_today if t["pnl"] > 0)
    losses = sum(1 for t in closed_today if t["pnl"] < 0)

    arrow = "🟢" if pnl_today >= 0 else "🔴"
    cum = float(summary.get("total_pnl", 0))
    cum_arrow = "📈" if cum >= 0 else "📉"

    lines = [
        f"## {arrow} India Paper Bot — {today_iso}",
        f"**Today's P&L:** {_fmt_inr(pnl_today)}",
        f"**Trades:** {len(todays)} ({wins}W / {losses}L"
        + (f", {len(open_today)} still open" if open_today else "") + ")",
        f"{cum_arrow} **All-time P&L:** {_fmt_inr(cum)}  "
        f"|  {summary.get('total_trades', 0)} trades  "
        f"|  Win rate {summary.get('win_rate', 0)*100:.0f}%",
    ]

    if todays:
        lines.append("")
        lines.append("**Today's trades:**")
        for t in todays[:10]:
            tk = t.get("ticker", "")
            qty = int(t.get("qty", 0))
            price = float(t.get("price", 0))
            strat = t.get("strategy", "")
            pnl = t.get("pnl")
            side = t.get("side", "")
            if pnl is None:
                lines.append(f"  • {side.upper()} {tk} ×{qty} @ ₹{price:.2f} "
                             f"[{strat}] — open")
            else:
                ico = "✅" if pnl > 0 else "❌"
                lines.append(f"  {ico} {tk} ×{qty} @ ₹{price:.2f} "
                             f"[{strat}] → {_fmt_inr(pnl)}")

    return "\n".join(lines)


def main():
    if not DATA_PATH.exists():
        print(f"data.json not found at {DATA_PATH}")
        sys.exit(1)

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    india = data.get("india", {})
    # Pull daily history (top-level key) into the india block so chart helper has it
    india["daily_history"] = data.get("india_daily_history", [])

    today_iso = date.today().isoformat()
    summary_text = _build_summary(india, today_iso)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        chart_path = Path(tmp.name)

    has_chart = _build_chart(india, chart_path)

    if has_chart:
        ok = send_image_message(summary_text, str(chart_path))
        if ok:
            print(f"✓ Sent India EOD report to Discord with chart")
        else:
            print("✗ Discord send failed (chart). Falling back to text.")
            send_info(summary_text)
    else:
        send_info(summary_text)
        print(f"✓ Sent India EOD report to Discord (text only — no chart history yet)")

    try:
        chart_path.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()

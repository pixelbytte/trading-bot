"""
Discord notification helpers.
The bot pings you here for trades, errors, and daily summaries.
"""

import os
import sys
import requests
from dotenv import load_dotenv
load_dotenv()

_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

if not _WEBHOOK:
    print("WARNING: DISCORD_WEBHOOK not set — Discord notifications disabled.", file=sys.stderr)


def _post(content):
    """Internal: send a message to Discord. Never raises — failures are logged to stderr only."""
    if not _WEBHOOK:
        return False
    try:
        r = requests.post(_WEBHOOK, json={"content": content}, timeout=10)
        return r.status_code == 204
    except Exception as e:
        print(f"Discord post failed: {e}", file=sys.stderr)
        return False

def send_info(message):
    """General info message."""
    return _post(f"ℹ️  {message}")


def send_trade_alert(ticker, side, qty, price, strategy=""):
    """Notify on trade execution."""
    emoji = "📈" if side.lower() == "buy" else "📉"
    tag = f" [{strategy}]" if strategy else ""
    msg = f"{emoji} **{side.upper()} {ticker}** × {qty} @ ${price:.2f}{tag}"
    return _post(msg)


def send_error(message):
    """Critical error alert."""
    return _post(f"🚨 **ERROR:** {message}")


def send_daily_pnl(pnl, num_trades, win_rate):
    """End-of-day summary."""
    arrow = "🟢" if pnl >= 0 else "🔴"
    msg = (
        f"{arrow} **Daily P&L:** ${pnl:+.2f}\n"
        f"Trades: {num_trades}  |  Win rate: {win_rate:.0%}"
    )
    return _post(msg)


def send_halt(reason):
    """Trading halted by circuit breaker."""
    return _post(f"⛔ **TRADING HALTED:** {reason}")


def send_image_message(text: str, image_path: str) -> bool:
    """Post a text message with an image attachment (PNG/JPEG)."""
    if not _WEBHOOK:
        return False
    try:
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "image/png")}
            data = {"payload_json": '{"content": ' + _json_quote(text) + '}'}
            r = requests.post(_WEBHOOK, data=data, files=files, timeout=15)
            return r.status_code in (200, 204)
    except Exception as e:
        print(f"Discord image post failed: {e}", file=sys.stderr)
        return False


def _json_quote(s: str) -> str:
    import json as _json
    return _json.dumps(s)


def send_india_close_alert(ticker: str, qty: int, exit_price: float,
                           reason: str, pnl: float) -> bool:
    """Realtime alert when an India paper position closes (SL/TGT/squareoff)."""
    arrow = "🟢" if pnl >= 0 else "🔴"
    sign = "+" if pnl >= 0 else ""
    msg = (
        f"{arrow} **CLOSE {ticker}** × {qty} @ ₹{exit_price:.2f} "
        f"[{reason}]  P&L: ₹{sign}{pnl:,.0f}"
    )
    return _post(msg)

"""
System health check (Days 29-31).
Runs every morning in midnight.yml before market open.
Verifies DB, Alpaca API, and Discord webhook are all reachable.
Sends a Discord alert if anything is broken so you know before the market opens.
"""

import os
import sys
from data.db import init_schema
from brokers.alpaca import get_account
from utils.logger import info, warning
from utils.discord import send_error, send_info


def run_health_check():
    """
    Check all critical systems. Returns True if healthy, False if any check failed.
    Sends a Discord alert on failure so issues are caught before market open.
    """
    failures = []

    # DB reachable and schema intact
    try:
        init_schema()
        info("Health: DB OK", source="health")
    except Exception as e:
        failures.append(f"Database: {e}")

    # Alpaca API reachable and credentials valid
    try:
        account = get_account()
        equity = float(account.get("equity", 0))
        buying_power = float(account.get("buying_power", 0))
        info(
            f"Health: Alpaca OK — equity ${equity:,.2f}, "
            f"buying power ${buying_power:,.2f}",
            source="health",
        )
        # Sanity: if equity has dropped more than 20% something is very wrong
        from config.settings import ACCOUNT_SIZE_USD
        if equity < ACCOUNT_SIZE_USD * 0.80:
            failures.append(
                f"Alpaca: account equity ${equity:,.2f} is below 80% of "
                f"initial ${ACCOUNT_SIZE_USD:,.0f} — check positions"
            )
    except Exception as e:
        failures.append(f"Alpaca API: {e}")

    # Discord webhook configured
    if not os.getenv("DISCORD_WEBHOOK"):
        failures.append("Discord: DISCORD_WEBHOOK env var is missing")
    else:
        info("Health: Discord webhook configured", source="health")

    # Anthropic key present (LLM filter won't block trades, but good to know)
    if not os.getenv("ANTHROPIC_KEY"):
        warning("Health: ANTHROPIC_KEY missing — LLM filter will be skipped", source="health")

    if failures:
        msg = "**Health check FAILED** — fix before market open:\n" + \
              "\n".join(f"  - {f}" for f in failures)
        warning(msg, source="health")
        send_error(msg)
        return False

    send_info("Health check passed: DB, Alpaca, Discord all OK.")
    return True


if __name__ == "__main__":
    ok = run_health_check()
    sys.exit(0 if ok else 1)

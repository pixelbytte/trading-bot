"""
Reset the daily kill switch at pre-market (run via midnight.yml workflow).
Safe to run repeatedly — deletes today's kill_switch row if it exists.
"""

from data.db import init_schema, reset_kill_switch, is_trading_halted
from utils.logger import info

if __name__ == "__main__":
    init_schema()
    was_halted = is_trading_halted()
    reset_kill_switch()
    if was_halted:
        info("Kill switch was active — cleared for new trading day.", source="reset")
    else:
        info("Kill switch was clear — nothing to reset.", source="reset")

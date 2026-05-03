"""
Day 3 final test: full integration check.
Alpaca + Discord + DuckDB + logger all working together.
"""

from brokers.alpaca import (
    get_account,
    get_quote,
    place_market_order,
    cancel_all_orders,
)
from utils.discord import send_info, send_trade_alert
from utils.logger import info, warning, error
from data.db import get_trades, trade_count_today

info("Day 3 integration test starting", source="test")

# 1. Account
account = get_account()
info(f"Account active. Cash: ${account['cash']:,.2f}", source="test")

# 2. Get quote
quote = get_quote("SPY")
info(f"SPY mid price: ${quote['mid']:.2f}", source="test")

# 3. Place trade (auto-logs to DB and via logger)
trades_before = trade_count_today()
order = place_market_order(
    ticker="SPY",
    qty=1,
    side="buy",
    strategy="day3-final",
)
info(f"Order accepted: {order['id']}", source="test")

# 4. Discord alert
send_trade_alert("SPY", "buy", 1, quote["mid"], strategy="day3-final")

# 5. Verify DB has new row
trades_after = trade_count_today()
assert trades_after == trades_before + 1, "Trade was not logged"
info(f"DB confirmed: {trades_before} -> {trades_after} trades", source="test")

# 6. Pull most recent trade
recent = get_trades(limit=1)[0]
info(
    f"Latest trade: {recent['side'].upper()} {recent['ticker']} "
    f"x{recent['qty']} @ ${recent['price']:.2f} ({recent['strategy']})",
    source="test",
)

# 7. Test warning + error logging too
warning("This is a test warning - safe to ignore", source="test")
try:
    raise ValueError("Test exception - safe to ignore")
except ValueError as e:
    error(str(e), source="test", exc=e)

# 8. Cancel test order
cancel_all_orders()
info("Test order cancelled", source="test")

# 9. Final summary
info("Day 3 integration test PASSED", source="test")
send_info("Day 3 complete - bot has memory, logging, and audit trail.")

print("\n" + "=" * 50)
print("ALL DAY 3 SYSTEMS GREEN")
print("=" * 50)
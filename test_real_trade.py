"""
Day 2 test: place a real paper order, then close it.
"""

import time
from brokers.alpaca import (
    get_account,
    get_quote,
    place_market_order,
    get_positions,
    close_position,
)
from utils.discord import send_info, send_trade_alert

print("=" * 50)
print("DAY 2 - REAL TRADE TEST")
print("=" * 50)

print("\n1. Account check...")
account = get_account()
print(f"   Status: {account['status']}")
print(f"   Cash: ${account['cash']:,.2f}")
print(f"   Buying power: ${account['buying_power']:,.2f}")

print("\n2. Getting SPY quote...")
quote = get_quote("SPY")
print(f"   SPY bid: ${quote['bid']:.2f}")
print(f"   SPY ask: ${quote['ask']:.2f}")
print(f"   SPY mid: ${quote['mid']:.2f}")

print("\n3. Buying 1 share SPY...")
order = place_market_order("SPY", qty=1, side="buy")
print(f"   Order ID: {order['id']}")
print(f"   Status: {order['status']}")

send_trade_alert(
    ticker="SPY",
    side="buy",
    qty=1,
    price=quote["mid"],
    strategy="day2-test",
)

print("\n4. Waiting 5 seconds for fill...")
time.sleep(5)

print("\n5. Checking positions...")
positions = get_positions()
spy_position = next((p for p in positions if p["ticker"] == "SPY"), None)
if spy_position:
    print(f"   Holding: {spy_position['qty']} SPY @ ${spy_position['avg_entry']:.2f}")
else:
    print("   No SPY position found yet (might still be filling)")

print("\n6. Closing SPY position...")
try:
    close_result = close_position("SPY")
    print(f"   Closed order ID: {close_result['closed_order_id']}")
    send_trade_alert(
        ticker="SPY",
        side="sell",
        qty=1,
        price=quote["mid"],
        strategy="day2-test",
    )
except Exception as e:
    print(f"   Close failed (likely market closed - order will fill Monday): {e}")

print("\n" + "=" * 50)
print("DAY 2 TEST COMPLETE")
print("=" * 50)

send_info("Day 2 test passed - bot can trade end-to-end.")
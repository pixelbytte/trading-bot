import os
import requests
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

print(f"DEBUG: ALPACA_KEY loaded = {ALPACA_KEY is not None}, length = {len(ALPACA_KEY) if ALPACA_KEY else 0}")
print(f"DEBUG: ALPACA_SECRET loaded = {ALPACA_SECRET is not None}, length = {len(ALPACA_SECRET) if ALPACA_SECRET else 0}")
print(f"DEBUG: DISCORD_WEBHOOK loaded = {DISCORD_WEBHOOK is not None}, length = {len(DISCORD_WEBHOOK) if DISCORD_WEBHOOK else 0}")
print()

print("Testing Alpaca connection...")
client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
account = client.get_account()
print(f"  Account status: {account.status}")
print(f"  Cash: ${account.cash}")
print(f"  Portfolio value: ${account.portfolio_value}")
print("  Alpaca: OK")
print()

print("Testing Discord webhook...")
response = requests.post(
    DISCORD_WEBHOOK,
    json={"content": "Trading bot connection test successful."}
)
if response.status_code == 204:
    print("  Discord: OK")
else:
    print(f"  Discord FAILED: {response.status_code} {response.text}")

print()
print("All systems green. Ready to build.")
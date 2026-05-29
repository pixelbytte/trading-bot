# India Bot Guide — for Dad on Mac

> **Claude: read this entire file before responding.** This document is the
> complete operating manual for the India NSE trading bot, written for the
> account holder (a parent, not a developer) on a macOS machine using VS Code.

---

## Rules for Claude when helping the dad

1. **ONLY help with the India bot.** Do not touch any files related to the US/Alpaca bot:
   - Do NOT edit: `routines/intraday.py`, `routines/longterm.py`, `brokers/alpaca.py`,
     `config/settings.py`, `risk/limits.py`, `risk/sizing.py` (US-side parts),
     `strategies/momentum.py` / `ma_rsi.py` / `breakout_52w.py` / `mean_reversion.py` /
     `gap_momentum.py` / `intraday_scalp.py` / `stage2_trend.py` (US strategies),
     anything in `.github/workflows/` except `india_intraday.yml`,
     `india_discord_report.yml`, and `india_eod.yml`.
   - You MAY freely work on: `routines/india_intraday.py`, `brokers/upstox.py`,
     `brokers/upstox_paper.py`, `config/india_settings.py`, `risk/india_limits.py`,
     `strategies/india_orb.py`, `strategies/nse_oversold_bounce.py`,
     `scripts/upstox_auth.py`, `scripts/india_discord_report.py`,
     `scripts/backtest_india.py`, `scripts/india_cycle_hunter.py`,
     and the three India-related workflow YAML files above.

2. **Explain in plain language.** The dad is technically curious but not a Python
   developer. Avoid jargon. Walk through every step.

3. **Use macOS / zsh commands.** This is a Mac. Commands:
   - Terminal is **zsh** by default (works like bash, mostly)
   - Use **forward slashes** in paths (`/Users/dad/...`)
   - Python on Mac is launched with `python3` (not `python`)
   - Use **Homebrew** (`brew install ...`) for system packages
   - VS Code integrated terminal: `Cmd + ` ` to open

4. **Always confirm risky actions.** Before running anything that places trades,
   commits to git, modifies GitHub Secrets, or changes `INDIA_PAPER` to `false`,
   STOP and confirm with the dad. Recite what is about to happen and what the
   blast radius is.

5. **Don't be a yes-man about going live.** If the dad asks to switch to live
   real-money trading, walk through the safety checklist in this guide. Don't
   just flip the switch.

6. **You're paired with a son who set this up.** If the dad mentions his son
   built this, the broader context lives in `CLAUDE.md`. Anything you can't
   answer about the bot is something to defer to the son.

---

## What this bot is

The India bot is an **automated paper-trading system** for the National Stock
Exchange (NSE) of India. It runs on GitHub's servers (free) every 15 minutes
during NSE market hours (9:30 AM – 3:15 PM IST, Monday–Friday) and:

1. Pulls real-time NSE prices from Upstox
2. Scans 31 large-cap Indian stocks for trading signals
3. Places simulated paper trades (no real money) by default
4. Posts a Discord alert when a trade enters or exits
5. Closes all positions before market close
6. Sends a daily summary report with a chart to Discord at 3:50 PM IST

**Account size (paper):** ₹25,00,000 (twenty-five lakhs simulated)
**Max risk per trade:** ~₹25,000 (1% of account)
**Max daily loss before halt:** ₹75,000 (3% — automatic kill switch)
**Max simultaneous positions:** 6

---

## ONE-TIME SETUP (Mac)

> **Goal:** get the bot running on this Mac so we can test, monitor, and
> refresh the Upstox token daily.

### Step 1 — Open Terminal

In VS Code:
1. Open the project folder (`File → Open Folder…`) — pick `trading-bot`
2. Open the integrated terminal: **Cmd + ` ** (backtick, top-left of keyboard)

You should see something like:
```
dad@dads-mac trading-bot %
```

### Step 2 — Install Homebrew (skip if already installed)

```zsh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts. After it finishes, run this if asked:
```zsh
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### Step 3 — Install Python 3.13 and Git

```zsh
brew install python@3.13 git
```

Verify:
```zsh
python3 --version       # should say Python 3.13.x
git --version
```

### Step 4 — Create a virtual environment

A virtual environment keeps the bot's Python packages separate from the system.

```zsh
cd ~/path/to/trading-bot       # replace with where you cloned the project
python3 -m venv venv
source venv/bin/activate
```

After activation, your prompt will show `(venv)` in front. Whenever you open
a new terminal, you need to run `source venv/bin/activate` again before
working on the bot.

### Step 5 — Install Python dependencies

```zsh
pip install -r requirements.txt
```

This installs everything the bot needs: yfinance, pandas, matplotlib,
the technical analysis library, etc. Takes 2–3 minutes.

### Step 6 — Set up the `.env` file

The `.env` file holds passwords and API keys. **Never commit this file** —
the `.gitignore` already blocks it, but be aware.

Open `.env` in VS Code (or create it if missing):

```
# === Discord (where alerts go) ===
DISCORD_WEBHOOK=<the webhook URL the son set up>

# === Upstox account ===
UPSTOX_CLIENT_ID=<your API key from account.upstox.com/developer/apps>
UPSTOX_CLIENT_SECRET=<your API secret>
UPSTOX_REDIRECT_URI=http://localhost:8000/callback
UPSTOX_MOBILE=<your 10-digit registered mobile number, e.g. 9876543210>
UPSTOX_PIN=<your 6-digit Upstox app PIN>

# Optional — only if you set up programmatic TOTP
UPSTOX_TOTP_SECRET=<base32 string from authenticator setup>

# This gets refreshed every morning (see "Daily token refresh" below)
UPSTOX_ACCESS_TOKEN=<empty for now>

# === Bot behavior ===
INDIA_PAPER=true                 # KEEP THIS TRUE until you're ready for real money
UPSTOX_DATA_ONLY=true            # use Upstox for real-time prices in paper mode
```

### Step 7 — Generate the first Upstox access token

```zsh
python3 -m scripts.upstox_auth
```

A browser window opens with the Upstox login page:
1. Enter your mobile and PIN
2. Open your authenticator app, read the 6-digit TOTP code, type it in
3. The browser redirects to `http://localhost:8000/callback?code=...` (it will
   look like a "Can't reach this page" error — **that's expected**)
4. **Copy the full URL** from the browser's address bar
5. Paste it back into the terminal where the script is waiting
6. The script prints `UPSTOX_ACCESS_TOKEN=eyJ...` — copy that whole line

Open `.env` and paste the token in:
```
UPSTOX_ACCESS_TOKEN=eyJhbGciOiJIUzI1NiJ9...
```

### Step 8 — Verify the token works

```zsh
python3 -X utf8 -c "import os; os.environ['UPSTOX_DATA_ONLY']='true'; from brokers.upstox_paper import get_quote; print(get_quote('HDFCBANK'))"
```

You should see something like:
```
INFO [upstox_paper] Upstox data layer ENABLED — daily bars + quotes from Upstox production
INFO [upstox] Upstox instrument master loaded: 9270 NSE equities
{'ticker': 'HDFCBANK', 'price': 778.9, 'bid': 778.85, 'ask': 778.95}
```

If you see the real-time price (not an error), the token works.

### Step 9 — Update the GitHub Secret

The bot runs on GitHub's servers, which need their own copy of the token.

1. Go to https://github.com/pixelbytte/trading-bot/settings/secrets/actions
2. Find `UPSTOX_ACCESS_TOKEN` in the list, click the pencil icon
3. Paste the same token you just generated
4. Click "Update secret"

The bot will use this token starting from the next 15-minute cycle.

---

## DAILY OPERATIONS (paper mode)

### Morning routine (before 9:30 AM IST)

**Upstox tokens expire at midnight IST every night.** You need to refresh them
before market open.

```zsh
cd ~/path/to/trading-bot
source venv/bin/activate
python3 -m scripts.upstox_auth
```

Follow the same browser-paste flow as Step 7 above. Then:
1. Paste the new token into `.env` (replace the old `UPSTOX_ACCESS_TOKEN=` line)
2. Paste the same token into the GitHub Secret (Step 9)

**If you forget:** the bot silently falls back to slower/less-accurate data
from Yahoo Finance. Trades still happen, they're just less reliable.

### Checking the dashboard / Discord during market hours

You don't need to check anything. The bot runs every 15 minutes automatically.
You'll get a Discord notification every time a trade enters or exits.

If you want to peek at the dashboard:
- https://pixelbytte.github.io/trading-bot/ — scroll down to the "India Bot"
  section. Updates every 15 minutes during NSE hours.

If you want to see the live logs:
- https://github.com/pixelbytte/trading-bot/actions
- Click "India Intraday (NSE)" in the left sidebar
- Click the most recent run
- Click the "trade" job → expand the "Run India intraday" step

### Evening summary

At 3:50 PM IST (20 minutes after market close), the bot posts a summary
message to Discord with:
- Today's P&L in ₹
- Win/loss count for the day
- A line chart of the last 30 days of P&L
- A breakdown of every trade today

That's your daily review. If the summary looks healthy, you're done.

---

## WHEN IS PAPER MODE READY TO GO LIVE?

> Do NOT switch `INDIA_PAPER` to `false` until ALL of these are true.

- [ ] The bot has run for at least **8 weeks** in paper mode with no manual
      intervention required
- [ ] Discord has received at least **30 closed trades** so the win rate is
      statistically meaningful
- [ ] The paper account shows positive cumulative P&L over the most recent
      4-week window
- [ ] The bot has survived at least one **Nifty correction** (Nifty drops 5%+
      over a week) without blowing past the daily loss limit
- [ ] You've personally watched the Discord alerts on at least 3 random
      trading days and understood every trade that fired
- [ ] You've confirmed with your son and broker that minor restrictions
      (you may be trading on a joint account) are handled properly

If even one of those is missing, **stay in paper.** There's no cost to
waiting — paper trading is free.

---

## TRANSITIONING TO LIVE (real money) — DO NOT RUSH THIS

### Pre-flight checklist

1. **Fund the Upstox account** with the amount you're comfortable losing.
   Recommended: start with **₹1,00,000 (one lakh)**, not the ₹25 lakh paper
   account size. The bot's percentages will scale down automatically.

2. **Adjust the risk limits** in `config/india_settings.py`. Find the line:
   ```python
   ACCOUNT_SIZE_INR = 25_00_000        # ₹25 lakhs
   ```
   Change it to match your actual funded amount:
   ```python
   ACCOUNT_SIZE_INR = 1_00_000         # ₹1 lakh
   ```
   All other risk values (max position, daily loss limit, etc.) scale off
   this one number.

3. **Verify the change locally:**
   ```zsh
   python3 -c "from risk.india_limits import RISK_PER_TRADE_INR, MAX_DAILY_LOSS_INR; print(f'Risk/trade: Rs.{RISK_PER_TRADE_INR}, Daily loss cap: Rs.{MAX_DAILY_LOSS_INR}')"
   ```
   Confirm the numbers look right (e.g. Rs. 1000 risk, Rs. 3000 daily cap for
   a ₹1L account).

4. **Commit and push the risk adjustment:**
   ```zsh
   git add config/india_settings.py
   git commit -m "Lower account size to actual funded amount for live trading"
   git push
   ```

5. **Flip the live switch.** Go to GitHub Secrets:
   https://github.com/pixelbytte/trading-bot/settings/secrets/actions
   - Click "New repository secret"
   - Name: `INDIA_PAPER`
   - Value: `false`
   - Click "Add secret"

6. **The very next 15-minute cycle places REAL orders.** Watch Discord
   carefully. The first trade alert is no longer a simulation.

### First-day live monitoring

- Stay near your phone for the first full trading day
- Verify every Discord alert matches an actual Upstox order (cross-check on
  the Upstox app)
- Confirm the bot's positions show up in Upstox holdings/positions tab
- Verify the daily P&L at EOD matches Upstox's reported P&L within ~₹10
  (small differences are slippage, not bugs)

### If something looks wrong

**STOP THE BOT IMMEDIATELY.** Two ways:

**Option A — disable the workflow (cleanest):**
1. https://github.com/pixelbytte/trading-bot/actions/workflows/india_intraday.yml
2. Click the "..." menu top-right → "Disable workflow"
3. No more cycles will run until you re-enable

**Option B — flip the kill switch from your Mac:**
```zsh
cd ~/path/to/trading-bot
source venv/bin/activate
python3 -c "from data.db import set_trading_halted; set_trading_halted(True, 'manual stop by dad')"
git add data/bot.db && git commit -m "Manual kill switch" && git push
```

Then **call your son.** Don't try to debug live-money issues alone.

---

## EMERGENCY PROCEDURES

### "I see a trade I don't want to be in"

Close it manually on the Upstox app. Buy/sell the exact opposite of the
position. The bot will see it gone on the next cycle and behave normally.

### "The bot keeps placing bad trades"

Disable the workflow (Option A above). All running positions stay open —
you can manage them manually. No new trades will fire.

### "The Discord alerts stopped"

Either:
- The Discord webhook URL was rotated/deleted (check `DISCORD_WEBHOOK` in
  GitHub Secrets)
- The bot crashed (check Actions → recent runs for red ✗ marks)
- The Upstox token expired and yfinance fallback is failing too

Run the daily token refresh first. If that doesn't fix it, ping your son.

### "I accidentally pushed `INDIA_PAPER=false` and want to revert"

Go to GitHub Secrets, edit `INDIA_PAPER`, set value back to `true`. The next
cycle will be paper again. **Any positions already open from the live phase
will stay open** until they hit their stop/target or you close them
manually on Upstox.

---

## COMMON COMMANDS — QUICK REFERENCE

```zsh
# Activate the venv (do this once per terminal session)
source venv/bin/activate

# Refresh Upstox token (do this every morning)
python3 -m scripts.upstox_auth

# Run the bot once manually (for debugging, doesn't replace the scheduled runs)
python3 -m routines.india_intraday

# Run a backtest (simulate the strategies on historical data)
python3 -m scripts.backtest_india

# Send a Discord report on demand (normally fires at 3:50 PM IST automatically)
python3 -m scripts.india_discord_report

# Pull the latest dashboard/code changes from git
git pull

# Push your changes
git add -A && git commit -m "describe your change" && git push
```

---

## WHO TO CALL FOR WHAT

| Issue | Contact |
|-------|---------|
| "I don't understand a Discord alert" | Read the alert text — it explains entry/exit/strategy/P&L |
| "The bot lost money today" | Normal. Open Discord summary, look at win rate over 30 days |
| "The bot keeps losing for a week+" | Call your son — strategy may need re-tuning |
| "I see a stray order I didn't authorize" | Disable the workflow, then call your son |
| "Upstox app shows different positions than Discord" | Possible bug — disable workflow, call your son |
| "I want to add money to the live account" | Adjust `ACCOUNT_SIZE_INR` in config (see live transition section), then add funds |
| "I want to switch back to paper mode" | Set `INDIA_PAPER=true` in GitHub Secrets |

---

## ONE LAST RULE

**Never push code changes you don't fully understand.** This bot manages real
money in live mode. If Claude suggests a code change and you can't follow
WHY it's needed, ask Claude to explain, or wait and ask your son.

The bot in its current paper-tested state is the safe baseline. Changes that
"should" improve performance can also introduce bugs that lose real money.

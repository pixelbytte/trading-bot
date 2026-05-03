# Trading Bot — Claude Code Project Context

> **Claude Code: read this entire file before doing anything. It is the authoritative source of project state, conventions, hard rules, and roadmap. The user is mid-build, treat this as a handoff document, not background reading.**

---

## Immediate task (read this first)

We are mid-way through **Day 6** of a 35-day plan. Days 1–5 complete. Day 6 has two pieces remaining:

1. **Step 3** — Wire bracket orders into `routines/intraday.py`. Currently the routine calls `place_market_order`. Replace with `place_bracket_order`, computing entry/stop/target via `risk/sizing.py` (already written and tested). Position size comes from `compute_position_size`, not from the routine guessing.

2. **Step 4** — Add trailing stop logic for winning positions. When an open position reaches +1R profit (1× the original risk amount), trail the stop to breakeven. At +2R, trail behind by 1R. This needs a new routine or a function called by the existing intraday loop. Use Alpaca's order replacement to update the stop on the existing bracket order.

Before writing any code, confirm understanding by:
- Reading `brokers/alpaca.py` to see `place_bracket_order` signature
- Reading `risk/sizing.py` to see `compute_atr`, `compute_stop_target`, `compute_position_size`
- Reading `routines/intraday.py` to see what we're modifying
- Reading `strategies/base.py` to see the `Signal` shape
- Reading `risk/limits.py` and `config/settings.py` for constraints

After Step 3 and 4 are done, instruct the user to test locally with `python -m routines.intraday`, then commit + push, then we move to Day 7 (backtesting framework).

---

## What we're building

An autonomous AI-powered paper trading bot for US stocks, run on a parent's Alpaca paper account. Hosted on GitHub, scheduled via GitHub Actions + Claude Routines, alerts via Discord, all logged to DuckDB.

**Goal:** 10% return on $5,000 pretend capital over a 5-week build window, paper only.

**Owner:** A minor (under 18) building this with parent's permission. Paper account is in parent's name. Real money trading is NOT permitted under any circumstance.

---

## Hard rules — never violate

1. **Paper only.** `USE_PAPER = True` is locked in `config/settings.py`. Never use the live Alpaca endpoint. The constant `ALPACA_LIVE_URL` exists for future reference only — never call it.
2. **Risk limits are hardcoded** in `risk/limits.py`. Never let strategies, LLMs, or any automation override them. Pre-trade `check_order()` runs on every order via the Alpaca wrapper.
3. **Secrets stay in `.env` (local) and GitHub Secrets (cloud).** Never commit, paste in chat, or log them. `.env` is gitignored.
4. **`bot.db` and `logs/` are gitignored.** Trade history is private. Never push them.
5. **Wrapper pattern.** All Alpaca calls go through `brokers/alpaca.py`. All Discord calls through `utils/discord.py`. All DB calls through `data/db.py`. Strategies and routines never call APIs directly.
6. **Position sizing comes from `risk/sizing.py`.** Strategies don't pick qty.
7. **Every order auto-logs to DuckDB.** Done by the wrapper, not the caller.
8. **Bracket orders only for entries** (Day 6 onward). Stop + target attached at submission.
9. **No real-time SIP data.** Free Alpaca paper accounts are limited to IEX feed (~2-3% of market volume). Always pass `feed=DataFeed.IEX` on bar requests. Realtime SIP requires paid subscription.
10. **The user is using Git Bash on Windows**, which has bracketed-paste quirks. Multi-line `python -c` commands often fail. Prefer creating script files over inline commands.

---

## Tech stack

- **Python 3.13+** (Python 3.14 has issues with `pandas-ta` due to numba; we use `ta` instead)
- **Alpaca paper API** via `alpaca-py` (NOT the deprecated `alpaca-trade-api`)
- **DuckDB** as embedded local database
- **`ta` library** for technical indicators (NOT `pandas-ta`)
- **GitHub Actions** as scheduler (cron-based workflows in `.github/workflows/`)
- **Claude Routines** (Anthropic cloud) for LLM research, scheduled at claude.ai/code/scheduled
- **Discord webhooks** for alerts
- **`python-dotenv`** for loading `.env` locally; GitHub Actions uses `env:` block to inject secrets

---

## File structure (current state)

```
trading-bot/
├── .github/workflows/
│   ├── intraday.yml       # every 15 min during market hours, weekdays
│   └── eod.yml            # 4:30pm ET daily reconcile + Discord summary
├── brokers/
│   ├── __init__.py
│   └── alpaca.py          # Alpaca wrapper: get_account, get_quote, get_bars,
│                          #   get_positions, place_market_order,
│                          #   place_bracket_order (Day 6),
│                          #   close_position, cancel_all_orders
├── config/
│   ├── __init__.py
│   └── settings.py        # ACCOUNT_SIZE_USD=5000, WATCHLIST (10 tickers),
│                          #   risk constants, market hours, USE_PAPER=True
├── data/
│   ├── __init__.py
│   ├── db.py              # DuckDB wrapper: 7 tables, init_schema(),
│   │                      #   log_trade, log_signal, log_quote, log_error,
│   │                      #   get_trades, trade_count_today,
│   │                      #   is_trading_halted, set_trading_halted,
│   │                      #   reset_kill_switch, trades_in_last_hour,
│   │                      #   daily_pnl_so_far
│   └── bot.db             # GITIGNORED — local DB file
├── logs/
│   └── bot.log            # GITIGNORED — rotating text log
├── risk/
│   ├── __init__.py
│   ├── limits.py          # MAX_POSITION_USD=750, MAX_DAILY_LOSS_USD=150,
│   │                      #   RISK_PER_TRADE_USD=50, MAX_OPEN_POSITIONS=4,
│   │                      #   MAX_TRADES_PER_DAY=10, MIN_PRICE_USD=5, etc.
│   ├── check.py           # check_order() pre-trade safety gate
│   └── sizing.py          # compute_atr, compute_stop_target,
│                          #   compute_position_size (Day 6)
├── routines/
│   ├── __init__.py
│   ├── intraday.py        # main 15-min loop: scan watchlist, generate signals,
│   │                      #   place trades through risk-checked wrapper
│   └── eod.py             # end-of-day reconcile, P&L computation, Discord summary
├── strategies/
│   ├── __init__.py
│   ├── base.py            # abstract BaseStrategy + Signal dataclass
│   └── ma_rsi.py          # MA crossover (10/30) + RSI(14) filter (40-70 zone)
├── tests/
│   └── __init__.py        # placeholder, no real tests yet
├── utils/
│   ├── __init__.py
│   ├── discord.py         # send_info, send_trade_alert, send_error,
│   │                      #   send_daily_pnl, send_halt
│   └── logger.py          # central logger: console + bot.log + errors→DB
├── .env                   # GITIGNORED — secrets locally
├── .gitignore             # blocks .env, venv/, bot.db, logs/, __pycache__/
├── CLAUDE.md              # this file
├── README.md
├── requirements.txt       # alpaca-py, duckdb, pandas, ta, python-dotenv, requests
├── test_connection.py     # Day 1 sanity check
├── test_real_trade.py     # Day 2-3 manual trade test
├── test_strategy.py       # Day 4 walk-forward strategy validation
└── venv/                  # GITIGNORED — local virtualenv
```

---

## GitHub Secrets (already set)

- `ALPACA_KEY`
- `ALPACA_SECRET`
- `DISCORD_WEBHOOK`

Read in workflows via `${{ secrets.NAME }}`. Read locally via `os.getenv("NAME")` after `load_dotenv()`.

---

## DuckDB schema (7 tables)

| Table | Purpose |
|-------|---------|
| `trades` | every order placed (entry, qty, price, strategy, status, pnl, notes) |
| `signals` | every strategy signal (acted or skipped, with reason) |
| `quotes` | logged quote pulls |
| `llm_outputs` | research from Claude Routines (Day 15+) |
| `daily_pnl` | one row per day with P&L summary |
| `errors` | any caught exception |
| `kill_switch` | per-day halt flag (set when daily loss limit hit) |

Plus 5 sequences: `trades_seq`, `signals_seq`, `quotes_seq`, `llm_seq`, `errors_seq`.

---

## Risk limits ($5,000 pretend account)

```python
MAX_POSITION_USD = 750        # 15% per position
MIN_POSITION_USD = 25
RISK_PER_TRADE_USD = 50       # 1% risk per trade
MAX_DAILY_LOSS_USD = 150      # 3% daily stop -> kill switch
MAX_TRADES_PER_DAY = 10
MAX_TRADES_PER_HOUR = 5
MAX_OPEN_POSITIONS = 4
MAX_CONCENTRATION_PCT = 0.30
MIN_PRICE_USD = 5             # no penny stocks
MIN_AVG_VOLUME = 500_000
MAX_ORDER_QTY = 100           # sanity guard
```

These are non-negotiable. Don't suggest changes that loosen them.

---

## Watchlist (`config/settings.py`)

```
AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, JPM, SPY
```

Liquid US large caps only. Day-trading universe. Long-term universe (~30 names) will be added in Day 22.

---

## Strategy: MA + RSI

`strategies/ma_rsi.py`

- 10-day SMA crossing above 30-day SMA -> buy signal
- 10-day SMA crossing below 30-day SMA -> sell signal
- Filter: RSI(14) must be between 40 and 70 (skip extremes)
- Walk-forward validated: ~6.6 signals/ticker/year, healthy frequency

---

## Status: where we are RIGHT NOW

**Days 1–5 complete.** Day 6 in progress.

| Day | Status | Notes |
|-----|--------|-------|
| 1 | done | accounts, repo, secrets, .env, connection test |
| 2 | done | Alpaca wrapper, Discord wrapper, first paper order |
| 3 | done | DuckDB schema, central logger, auto-logging wrappers |
| 4 | done | Strategy base class, MA+RSI, watchlist scanner, walk-forward validation |
| 5 | done | Risk layer, intraday + EOD GitHub Actions workflows running on schedule |
| 6 | IN PROGRESS | ATR + position sizing built. Bracket orders just added to wrapper. Next: wire bracket orders into intraday routine, then trailing stops |
| 7 | next | Backtesting framework (`vectorbt`) + performance metrics |
| 8–10 | planned | Add 2 more strategies (mean reversion, momentum), comparison report |
| 11 | planned | Portfolio manager — handle multi-strategy conflicts |
| 12 | planned | Stops + take-profits in DB, position lifecycle tracking |
| 13 | planned | HTML dashboard via GitHub Pages |
| 14 | planned | Hardening checkpoint |
| 15 | planned | First Claude Routine — pre-market news scan |
| 16 | planned | Knowledge base — investing books + investor letters distilled to `/knowledge/` |
| 17–18 | planned | Claude analysis Routine + LLM filter on signals |
| 19 | planned | Sunday weekly review Routine |
| 20–21 | planned | Strategy adjustment + buffer |
| 22 | planned | Split portfolio: day_trading vs long_term (60/40) |
| 23 | planned | Fundamentals data via Financial Modeling Prep |
| 24 | planned | Thesis generation Routine (Claude reads earnings calls) |
| 25 | planned | Long-term screener + ranking |
| 26 | planned | DCA logic for long-term picks |
| 27 | planned | Long-term workflows in Actions |
| 28 | planned | Unified reporting (both portfolios) |
| 29–31 | planned | Error handling, circuit breakers, dashboard polish |
| 32 | planned | Stress test (replay a week with injected failures) |
| 33–34 | planned | Security audit + config freeze |
| 35 | planned | Launch |

---

## Day 6 — current detailed status

### Completed
- `risk/sizing.py` with `compute_atr()`, `compute_stop_target()`, `compute_position_size()`
- ATR(14) using `ta.volatility.AverageTrueRange`
- Stop = entry − (1.5 × ATR), Target = entry + (3.0 × ATR), 2:1 reward:risk
- Position sizing scaled to volatility, capped by `MAX_POSITION_USD`
- `place_bracket_order()` added to `brokers/alpaca.py` with auto risk-check + DB log

### In progress / next
- Test `place_bracket_order` end-to-end
- Update `routines/intraday.py` to use bracket orders instead of plain market orders
- Add trailing stop logic for winners
- Commit + push Day 6

---

## Conventions and rules to follow

### Code style
- Wrapper pattern: callers never touch APIs directly
- Every external call wrapped in try/except with `log_error()` to DuckDB
- All public functions have docstrings explaining args + return
- Constants in `config/settings.py` and `risk/limits.py` only
- `from utils.logger import info, warning, error` — use these instead of `print()`

### Discord notifications
- `send_trade_alert(ticker, side, qty, price, strategy)` for trades
- `send_daily_pnl(pnl, num_trades, win_rate)` for EOD summary
- `send_error(message)` for critical failures
- `send_halt(reason)` when circuit breaker trips
- `send_info(message)` for everything else

### Logging
- `info()` for routine events (signals, decisions, status)
- `warning()` for non-broken oddities
- `error()` for actual failures (also writes to errors table)
- All logs include a `source=` tag like `"alpaca"`, `"intraday"`, `"strategy"`

### Strategies
- All inherit from `BaseStrategy` (`strategies/base.py`)
- Implement `generate_signals(ticker, bars) -> List[Signal]`
- Return zero or more `Signal` objects with confidence 0.0–1.0
- Strategies decide direction. They DON'T decide qty (that's `risk/sizing.py`).

### Routines
- Live in `routines/`
- Each is runnable as `python -m routines.NAME`
- Catch top-level exceptions, log via `error()`, exit 1 on crash so Actions marks failure

### GitHub Actions
- All workflows in `.github/workflows/*.yml`
- All schedules use UTC cron, account for ET DST drift (use a 1-hour buffer window)
- Always include `workflow_dispatch:` for manual triggers
- Inject secrets via `env:` block under the run step

---

## Things to NOT do

- Don't suggest moving to live trading (the user is a minor)
- Don't loosen risk limits to "make more trades"
- Don't add a strategy without backtesting it first (Day 7 framework needed)
- Don't paste API keys anywhere
- Don't add full SIP data feed dependency (free tier is IEX only)
- Don't use `pandas-ta` (numba incompatibility with Python 3.14 in this env)
- Don't auto-retrain strategies on bot's own P&L. Self-learning is human-reviewed PRs only (per Day 19 plan).
- Don't use complex multi-line `python -c "..."` commands; create scripts instead — the user is on Git Bash with paste quirks
- Don't reproduce song lyrics, copyrighted text, or full investor letters in `/knowledge/` (Day 16) — only summarize principles in our own words

---

## Open issues / known quirks

1. **Order status string format:** Alpaca returns `OrderSide.BUY` / `OrderStatus.ACCEPTED` as the string repr of the enum. We log these as-is. Cleaner formatting could come later but works fine.
2. **Cron timing:** GitHub Actions cron can be delayed 5–10 min under load. Don't rely on exact-minute precision. Don't go below 5-min schedules.
3. **Saturday tests:** when markets are closed, orders sit queued. `place_market_order` succeeds (status=ACCEPTED) but no fills happen. Tests must account for this.
4. **DuckDB on multiple connections:** we use single connections with try/finally close. If we add concurrency later, add WAL mode or connection pooling.
5. **`bot.db` is local-only.** GitHub Actions creates a fresh DB on each run because the file isn't committed. This is fine for now (each run is stateless trading), but we'll need a real persistence story before backtesting/learning across runs (Day 7 will address by storing historical bars in repo or external).

---

## Resources

- Alpaca docs: https://docs.alpaca.markets/
- `alpaca-py` SDK: https://github.com/alpacahq/alpaca-py
- `ta` library: https://github.com/bukosabino/ta
- DuckDB Python: https://duckdb.org/docs/api/python/overview
- Claude Routines: https://claude.ai/code/scheduled

---

## How to ask Claude Code for help on this project

Good prompts:
- "Read CLAUDE.md, then implement Day 6 Step 3: wire bracket orders into routines/intraday.py"
- "Add a new strategy file at strategies/mean_reversion.py using the BaseStrategy pattern from base.py"
- "Update risk/check.py to also block orders when within 5 minutes of market close"

Bad prompts:
- "Make the bot trade more aggressively" (violates risk rules)
- "Switch to live trading" (forbidden — minor user)
- "Connect to my real bank account" (forbidden — paper only)

Claude Code should refuse anything that violates the hard rules.

---

## Final note for Claude Code

When you finish a task:
1. Tell the user exactly which files you changed.
2. Give them the exact commands to test it (they're on Git Bash on Windows; prefer `python -m routines.NAME` over multi-line inline commands).
3. Remind them to commit + push when satisfied (check that `.env`, `bot.db`, `logs/`, `venv/`, `__pycache__/` are NOT staged).
4. Tell them what Day/Step comes next so they can decide whether to keep going or stop.

Treat the user as a smart but young learner. Explain *why*, not just *what*. Don't dumb it down — they've shipped 5 days of real infrastructure already.

Now: read the rest of the repo files referenced above, then begin Day 6 Step 3.

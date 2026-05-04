# Additional Strategies — Validated Candidates

These two strategies have strong academic and practitioner backing.
Both use daily bars and fit within our existing framework.
Neither is live yet — each needs a backtest before deployment.

---

## 1. 52-Week High Breakout (O'Neil, Darvas, George)

**Core idea**: Stocks hitting new 52-week highs on above-average volume tend to continue higher.
This feels counter-intuitive (buy at the top?) but is one of the most replicated momentum anomalies in academic finance.

**Why it works**: New 52-week highs remove all overhead supply — every holder is in profit.
There is no resistance. Institutional investors use 52-week highs as a filter for their own
momentum screens, creating a self-reinforcing effect.

### Signal rules
- Price closes at a new 52-week high (today's close > max close of prior 251 sessions)
- Volume on breakout day is at least 1.5x the 50-day average (institutional participation)
- RSI(14) between 50 and 80 — already trending, not overbought
- SPY above its 50-day SMA (market regime check, same as MA+RSI)
- Price above SMA200 (SEPA Stage 2 requirement)

### Exit rules
- Stop: 1.5× ATR below entry (same as all our strategies)
- Target: 3.0× ATR above entry (2:1 R:R)
- Trail stop at +1R to breakeven, +2R to entry+1R (standard system)

### Expected characteristics (from academic literature)
- Win rate: ~45-55% (lower than you'd expect — many false breakouts)
- Avg winner: 3-5R (the winners run hard)
- Expectancy: positive due to asymmetric R:R
- Best in confirmed uptrends, worst in correction/bear markets

### Implementation file
`strategies/breakout_52w.py` — not yet created

---

## 2. Relative Strength Pullback (O'Neil, Livermore, Minervini)

**Core idea**: The stocks that fall least during a market correction are the ones institutions
are holding. When the market recovers, money floods back into these relative strength leaders first.
Buy the 5-10% pullback in the strongest names during a mild correction.

**Why it works**: Relative strength is a proxy for institutional accumulation.
If a stock drops only 3% when the market drops 8%, someone powerful is buying dips.
That same buyer will push the stock to new highs once the market stabilizes.

### Signal rules
- Calculate 3-month return for the stock vs. SPY 3-month return
- Relative strength = stock 3-month return minus SPY 3-month return
- Stock must have RS > +5% over the past 3 months (outperforming SPY by 5+ points)
- Current price is 5-10% below its recent 20-day high (the "pullback" condition)
- Price is still above SMA50 (the pullback is mild, not a breakdown)
- RSI(14) between 35 and 55 (pulled back but not crashed)
- SPY above its 50-day SMA (market in uptrend or recovering)

### Exit rules
- Stop: 1.5× ATR below entry
- Target: 3.0× ATR above entry
- Trail stop same as all strategies

### Expected characteristics
- Win rate: ~50-60% (setups are selective, quality is higher)
- Avg winner: 2-3R
- Works best during market recovery phases after mild corrections
- Fails in deep bear markets (even leaders eventually follow down)

### Implementation file
`strategies/rs_pullback.py` — not yet created

---

## Deployment Decision Process

Before adding either strategy to `routines/intraday.py`:

1. Add `strategies/breakout_52w.py` or `strategies/rs_pullback.py` implementing `BaseStrategy`
2. Run `python -m scripts.backtest` — the strategy comparison table will include the new strategy
3. Check expectancy > 0 and win rate in a reasonable range before enabling
4. Add to `STRATEGIES` list in `intraday.py` and `TREND_ONLY_STRATEGIES` if it is trend-dependent
5. Run one week of paper observation before fully trusting the live signals

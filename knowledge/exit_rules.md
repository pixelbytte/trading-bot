# Exit Rules

Synthesized from: O'Neil, Livermore, Lynch, Minervini, Van Tharp.
Exits are more important than entries. Most trading accounts fail here.

---

## The Only Non-Negotiable Rule (O'Neil)

1. Never let a loss exceed 7-8% from your entry. No exceptions. No "waiting for it to come back." This is the most important rule in trading.
2. Cut the loss automatically via a stop-loss order placed at the time of entry. Discretionary stop-moves are how good entries become disasters.

## Stop-Loss Placement (ATR-based, our system)

3. Initial stop = entry price minus 1.5× ATR(14). This adapts the stop to the stock's own volatility rather than using an arbitrary percentage.
4. A stop that is too tight gets hit by normal intraday noise. A stop that is too loose means your loss is already too large before it triggers.
5. Never widen a stop. You can only tighten it (trail it) as the trade works in your favor.

## Trailing to Protect Gains (our system)

6. At +1R (unrealized P&L equals your original risk), move the stop to breakeven. You now have a free trade — worst case is zero, not a loss.
7. At +2R, trail the stop to entry + 1R per share. You lock in at least 1R of profit.
8. Let the trade run from there. The biggest mistake active traders make is taking tiny profits and letting losses run — the exact opposite of what you should do.

## Taking Profits (O'Neil, Lynch)

9. Most stocks that break out from a proper base show their largest single-week move between weeks 3 and 8 of the move. That "climax run" is often the top — consider reducing into it.
10. A 20-25% gain from a pivot point is a reasonable profit target for swing trades. Take at least half off.
11. If a stock rises 20% in three weeks or less, it may be a leading stock showing unusual strength — hold it for eight weeks and reassess.

## When the Thesis Breaks (Lynch)

12. Know WHY you bought. Write it down (the signal log does this). If the reason no longer applies, sell — regardless of price.
13. A company downgrading its own guidance, losing market share, or entering regulatory trouble is not a dip to buy. It is a thesis invalidation.
14. Price action that contradicts the thesis (stock falling on strong earnings, sector leadership collapsing) is a warning to reduce, not add.

## Position Lifecycle in Our System

15. Entries are logged with bracket orders: entry market order + stop leg + target leg submitted simultaneously.
16. The stop leg is automatically modified when trailing conditions are met (update_stop_order).
17. If neither stop nor target fires within 3 trading days, the reconciler will flag the entry. Review it manually.
18. At EOD, any open position that is NOT in Alpaca is assumed closed — reconcile_exits() finds the fill and records P&L.

## What Never to Do (Livermore, hard lessons)

19. Never add to a losing position. "Adding to losers" is the most common way traders blow up their accounts.
20. Never take a loss personally. The market did not "do this to you." The setup was wrong or timing was bad. Move on.
21. Never revenge trade. After a loss, the next trade should be smaller, not larger.

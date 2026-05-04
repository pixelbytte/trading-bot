# Risk Management Principles

Synthesized from: Van Tharp (position sizing), O'Neil, Druckenmiller, Ed Seykota, Livermore.
Risk management is what separates traders who survive from those who do not.
Every other skill is irrelevant if you do not manage risk correctly.

---

## The Core Equation (Van Tharp)

1. **R = the dollar amount you are willing to lose on a single trade.** Every trade is sized so that your maximum loss = 1R.
2. Position size = R / (entry price - stop price). This is the only correct way to size positions. Gut feel is not a sizing method.
3. With R = $50 and a $750 max position, a trade with a wide stop will result in fewer shares, not a bigger loss.
4. Track every trade in R-multiples (+2R win, -1R loss). This tells you whether your system is actually profitable in expectancy terms.

## Account-Level Risk Rules (our system)

5. Max risk per trade: $50 (1% of $5,000 paper account). This is `RISK_PER_TRADE_USD` in `risk/limits.py`.
6. Max position size: $750 (15% of account). Prevents concentration in a single name.
7. Max daily loss: $150 (3% of account). When hit, kill switch activates and trading stops for the day.
8. Max open positions: 4. More than 4 simultaneous positions at this account size dilutes attention and increases correlation risk.
9. Max trades per day: 10. Overtrading is a symptom of poor discipline, not an edge.

## When to Reduce Size (Druckenmiller, Seykota)

10. When you have had two or three consecutive losing trades, cut your position size in half. Do not try to "make it back."
11. When overall account is down 5% from its equity high, reduce all new trade sizes by 50%.
12. When account is down 10% from its high, stop trading entirely for the day and review what is going wrong.
13. In uncertain market conditions (regime unclear), use half-size positions.

## When to Increase Size (Druckenmiller)

14. Druckenmiller's key insight: when you are right, bet big. Most traders do the opposite — small when winning, large when losing.
15. A setup that scores well on every filter (trend, sentiment, volume, base quality) earns a full-size position.
16. A marginal setup that passes by the minimum bar gets a half-size entry (or skip).
17. Do NOT increase size while you are in a losing streak. Increase only from a position of confidence backed by evidence.

## Diversification vs. Concentration

18. For active trading: concentrate in 3-5 high-conviction ideas rather than spreading thin across 20 mediocre ones. Lynch said to own what you understand deeply.
19. Across sectors: avoid having all 4 open positions in the same sector. Correlated positions do not reduce risk — they amplify it.
20. SPY is a hedge and a regime indicator, not a core trading vehicle.

## The Kill Switch (our system)

21. When daily loss reaches $150, trading halts for the day. The kill switch prevents the most dangerous trading behavior: revenge trading after a bad morning.
22. Kill switch resets at 9:00am ET the next morning via the midnight.yml workflow.
23. The kill switch can also be tripped manually if something looks wrong — `set_trading_halted()` is callable from anywhere.
24. Kill switch status is checked before every single order in `risk/check.py`.

## Expectancy vs. Win Rate (Ed Seykota, Van Tharp)

25. A system with a 40% win rate can be highly profitable if average winners are 3R and average losers are 1R. Expectancy = (0.4 × 3R) + (0.6 × -1R) = +0.6R per trade.
26. A system with a 60% win rate can destroy an account if average winners are 0.5R and average losers are 2R. Expectancy = (0.6 × 0.5R) + (0.4 × -2R) = -0.5R per trade.
27. Always calculate expectancy after at least 20 trades. Sample sizes below 20 are statistically meaningless.
28. Our MA+RSI backtest expectancy: +0.295R. Mean reversion: +0.210R. Both are positive expectancy systems.

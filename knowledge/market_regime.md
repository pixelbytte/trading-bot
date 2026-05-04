# Market Regime Assessment

Synthesized from: O'Neil (CAN SLIM "M" factor), Paul Tudor Jones, Druckenmiller, Dalio.
Before analyzing any individual stock, determine what the market is doing.
Individual stock analysis is wasted effort in a bear market.

---

## The Market Is the Boss (O'Neil, PTJ)

1. Three out of four stocks follow the direction of the general market. A great setup in a bad market is still likely to lose.
2. Paul Tudor Jones's personal rule: never hold a long position when the stock (or its index) is below the 200-day MA. Below it = the trend is down.
3. In corrections, reduce exposure aggressively. Cash is a position. Preserving capital in down markets is what creates the buying power for the next uptrend.

## Uptrend vs. Correction (O'Neil's Market Direction Model)

4. **Confirmed uptrend**: major indexes (SPY, QQQ) trending above their 50-day SMAs, making higher highs and higher lows. Aggressive buying is appropriate.
5. **Uptrend under pressure**: distribution days accumulating (4+ in 3 weeks), or index slicing through the 50-day SMA intraday. Raise caution, reduce new buys.
6. **Correction**: SPY below its 50-day SMA. Our system: disable trend-following strategies (MA+RSI), allow mean reversion only.
7. **Bear market**: SPY below 200-day SMA. Our system: no new buys at all. Capital preservation mode.

## How We Detect Regime (our system)

8. Every intraday cycle fetches 300 days of SPY bars. If SPY close < SMA50, regime = "correction."
9. In correction mode, MA+RSI strategy is disabled (it trend-follows, which fails in corrections).
10. Mean reversion strategy stays active in corrections — oversold bounces still work.
11. Momentum strategy is permanently disabled until it achieves >50% win rate in backtests.

## Distribution Days (O'Neil)

12. A distribution day = major index falls 0.2%+ on volume higher than the prior session. This signals institutional selling.
13. Four or more distribution days within a 25-session window is a serious warning sign.
14. Stalling days count too: index barely moves on unusually high volume — institutions absorbing shares rather than pushing price up.

## Sector Rotation (Druckenmiller, Dalio)

15. Money rotates: when defensive sectors (utilities, consumer staples) outperform growth sectors, risk appetite is shrinking — a leading indicator of market weakness.
16. Watch for sector leadership to shift before the index rolls over. Financials and semiconductors often lead the broader market.
17. When the sector your stocks are in starts distributing, your individual stocks will follow — sell into sector weakness, not after it.

## Macroeconomic Backdrop (Dalio)

18. Interest rate trends matter: rising rates are headwinds for growth stocks (compression of price-to-earnings multiples). During rate-hiking cycles, reduce exposure to high-multiple names.
19. Inflation above 4% historically compresses P/E multiples. Not a time to pay premium prices for growth.
20. A recession is a lagging indicator — by the time it's official, the market has already fallen 30-40%. Watch leading indicators instead.

## Practical Rules for This Bot

21. If SPY is below its 50-day SMA at cycle start: log "correction mode," disable MA+RSI, continue with mean reversion.
22. If SPY is below its 200-day SMA: log "bear market warning" to Discord, disable all buy signals until regime clears.
23. Regime check runs fresh every 15-minute cycle — no carrying state across cycles.

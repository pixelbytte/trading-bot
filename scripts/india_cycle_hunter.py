"""
India Cycle Hunter -- find the next supercycle sector EARLY.

Strategy: concentrated 3-stock portfolio, 60-100%/yr target.

The key insight: defence stocks (MAZDOCK, COCHINSHIP) ran 89-94%/yr because
someone bought them in 2022 at the START of the defence supercycle. By 2025,
that run is over. This script finds what 2022-MAZDOCK looks like TODAY --
stocks that have JUST entered Stage2 (price recently crossed SMA200), with
proven 2-3yr CAGR, pulled back 10-25% from peak, and structural tailwinds.

Allocation: Rs.2,00,000 per stock (20% each), 5 positions max.
Stop: 25% trailing below entry peak (wide enough to survive corrections).
Target: hold 12-18 months, exit only if Stage2 breaks.

Run:
    python scripts/india_cycle_hunter.py
"""

import sys
import os
import math
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ACCOUNT_INR       = 1_000_000
ALLOC_PER_STOCK   = 200_000     # 20% per position, max 5 positions
TRAILING_STOP_PCT = 0.25        # 25% -- wide enough to survive corrections
TOP_N             = 3           # final picks
FETCH_DAYS        = 520         # needs 220+ for Stage2 warmup

# Stage2 "early entry" window: if stock entered Stage2 within this many bars
# it's fresh. If it's been in Stage2 for 200+ bars it's late-cycle.
EARLY_STAGE2_MAX_BARS = 90      # entered Stage2 within last 90 bars (~4 months)
LATE_STAGE2_MIN_BARS  = 20      # must have held Stage2 for at least 20 bars (not a 1-day blip)

# ── Broad universe: 8 sectors, 40+ stocks ──────────────────────────────────
UNIVERSE = {
    # Defence -- already ran, include to show cycle age
    "MAZDOCK.NS":      ("Defence",    "Nuclear submarine builder, 8yr backlog"),
    "COCHINSHIP.NS":   ("Defence",    "Naval shipyard, aircraft carrier refit"),
    "BEL.NS":          ("Defence",    "Radar, electronic warfare systems"),
    "HAL.NS":          ("Defence",    "Fighter jets, helicopters"),

    # Power / Clean Energy -- possible next supercycle
    "NTPC.NS":         ("Power",      "India's largest power utility, 50GW RE plan"),
    "TATAPOWER.NS":    ("Power",      "Rooftop solar, EV charging network"),
    "SJVN.NS":         ("Power",      "Hydro + solar PSU, order book tripling"),
    "CESC.NS":         ("Power",      "Integrated utility, RE expansion"),
    "ADANIGREEN.NS":   ("Power",      "Largest renewable energy company in India"),

    # Manufacturing / Electronics -- structural China+1 beneficiary
    "DIXON.NS":        ("Mfg",        "India's Foxconn -- phones, appliances, LEDs"),
    "KAYNES.NS":       ("Mfg",        "Embedded electronics, PCB -- defence + EV"),
    "SYRMA.NS":        ("Mfg",        "Electronics mfg services, RFID, IoT"),
    "TATAELXSI.NS":    ("Mfg",        "Chip design, autonomous driving software"),
    "TITAGARH.NS":     ("Mfg",        "Railway wagons + metro coaches, huge order book"),

    # PSU Banks -- re-rating cycle, low valuations
    "SBIN.NS":         ("Banking",    "India's largest bank, credit growth 14%"),
    "CANBK.NS":        ("Banking",    "Rekha Jhunjhunwala pick, NPAs cleaned up"),
    "PFC.NS":          ("Banking",    "Power sector lender, Rs.9L cr order pipeline"),
    "RECLTD.NS":       ("Banking",    "RE infrastructure lender, 24% ROE"),

    # Consumption / Retail -- urban India discretionary
    "TRENT.NS":        ("Consumer",   "Zudio fast fashion, 500+ stores by 2026"),
    "DMART.NS":        ("Consumer",   "Everyday value retail, consistent compounder"),
    "TITAN.NS":        ("Consumer",   "Jewellery + watches, aspirational India"),
    "MUTHOOTFIN.NS":   ("Consumer",   "Gold loans, 5,700 branches, RBI-licensed"),

    # IT Services -- AI services wave
    "PERSISTENT.NS":   ("IT",         "AI + cloud transformation, 30%+ growth"),
    "COFORGE.NS":      ("IT",         "Insurance + travel IT, mid-cap compounder"),
    "LTIMINDTREE.NS":  ("IT",         "LTI Mindtree, digital transformation"),
    "INFY.NS":         ("IT",         "Large-cap IT, AI services re-rating"),

    # Pharma / Healthcare -- CRAMS + branded generics
    "SUNPHARMA.NS":    ("Pharma",     "Specialty generics + CRAMS, US market"),
    "DRREDDY.NS":      ("Pharma",     "Generic + biosimilar, global platform"),
    "CIPLA.NS":        ("Pharma",     "Branded generics, respiratory specialist"),
    "DIVISLAB.NS":     ("Pharma",     "CRAMS for global pharma -- quality moat"),

    # Telecom / Digital infra
    "BHARTIARTL.NS":   ("Telecom",    "India's best telco, ARPU rising"),
    "ROUTE.NS":        ("Telecom",    "Cloud CPaaS -- enterprise messaging infra"),
    "CDSL.NS":         ("Telecom",    "Demat repository monopoly, wealth mgmt boom"),

    # Auto / EV
    "TVSMOTOR.NS":     ("Auto",       "EV two-wheelers, 35%+ EV revenue by 2026"),
    "MARUTI.NS":       ("Auto",       "70% car market share, still growing"),
    "MOTHERSON.NS":    ("Auto",       "Auto components, global Tier-1 supplier"),
}


def _require_yfinance():
    try:
        import yfinance as yf
        return yf
    except ImportError:
        print("\n  ERROR: yfinance not installed. Run: pip install yfinance\n")
        sys.exit(1)


def fetch_bars(ticker, days=520):
    yf = _require_yfinance()
    for period in ("3y", "2y", "18mo"):
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if not df.empty and len(df) >= 120:
                df = df.tail(days)
                bars = []
                for date, row in df.iterrows():
                    ts = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]
                    bars.append({
                        "ts":     ts,
                        "close":  float(row["Close"]),
                        "high":   float(row["High"]),
                        "low":    float(row["Low"]),
                        "volume": int(row.get("Volume", 0) or 0),
                    })
                return bars
        except Exception:
            continue
    return []


def _sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _analyze(ticker, sector, desc, bars):
    closes = [b["close"] for b in bars]
    highs  = [b["high"]  for b in bars]
    n      = len(closes)

    if n < 240:
        return None

    price   = closes[-1]
    sma50   = _sma(closes, 50)
    sma150  = _sma(closes, 150)
    sma200  = _sma(closes, 200)

    if not all([sma50, sma150, sma200]):
        return None

    # ── CAGR ────────────────────────────────────────────────────────────
    years_avail = min(3.0, n / 252.0)
    if years_avail < 0.5:
        return None
    start_close = closes[max(0, n - int(years_avail * 252))]
    if start_close <= 0:
        return None
    cagr = (price / start_close) ** (1 / years_avail) - 1

    # ── 52-week high and pullback ────────────────────────────────────────
    high52   = max(highs[-252:]) if n >= 252 else max(highs)
    pct_down = (high52 - price) / high52 * 100   # % below 52w high

    # ── 6-month momentum ────────────────────────────────────────────────
    price_6m = closes[-126] if n >= 126 else closes[0]
    mom_6m   = (price - price_6m) / price_6m * 100

    # ── Stage2 detection: find how long stock has been in Stage2 ────────
    # Walk backward from today to find when Stage2 STARTED (most recent entry)
    stage2_bars_held = 0
    bars_since_entry = None

    for i in range(n - 1, max(n - 200, 220), -1):
        c = closes[:i+1]
        p   = c[-1]
        s50  = _sma(c, 50)
        s150 = _sma(c, 150)
        s200 = _sma(c, 200)
        if not all([s50, s150, s200]):
            break
        if p > s50 > s150 > s200:
            stage2_bars_held += 1
            bars_since_entry = n - 1 - i
        else:
            if stage2_bars_held > 0:
                break   # found the start of the current Stage2 run

    # Currently in Stage2?
    in_stage2 = (price > sma50 > sma150 > sma200)

    # ── Cycle age classification ─────────────────────────────────────────
    if not in_stage2:
        cycle_age = "broken"
    elif stage2_bars_held < LATE_STAGE2_MIN_BARS:
        cycle_age = "too_new"       # just crossed, might be a whipsaw
    elif stage2_bars_held <= EARLY_STAGE2_MAX_BARS:
        cycle_age = "early"         # sweet spot: confirmed but fresh
    elif stage2_bars_held <= 200:
        cycle_age = "mid"
    else:
        cycle_age = "late"          # ran for 200+ bars -- likely extended

    # ── Composite score (only for in-Stage2 stocks) ──────────────────────
    score = None
    if in_stage2 and cycle_age not in ("broken", "too_new"):
        # CAGR score: higher is better (0-50 pts)
        cagr_score = min(50, max(0, cagr * 100 * 0.5))

        # Cycle freshness score: earlier is better (0-30 pts)
        freshness = 1.0 - min(1.0, stage2_bars_held / 200)
        freshness_score = freshness * 30

        # Pullback score: 10-25% below peak is ideal (0-20 pts)
        # Too high (>30%) = damaged; too low (<5%) = extended
        if 10 <= pct_down <= 25:
            pullback_score = 20
        elif 5 <= pct_down < 10:
            pullback_score = 12
        elif 25 < pct_down <= 35:
            pullback_score = 8
        else:
            pullback_score = 0

        score = cagr_score + freshness_score + pullback_score

    return {
        "ticker":       ticker,
        "sector":       sector,
        "desc":         desc,
        "price":        round(price, 2),
        "sma200":       round(sma200, 2),
        "cagr":         round(cagr * 100, 1),
        "high52":       round(high52, 2),
        "pct_down":     round(pct_down, 1),
        "mom_6m":       round(mom_6m, 1),
        "in_stage2":    in_stage2,
        "cycle_age":    cycle_age,
        "stage2_held":  stage2_bars_held,
        "score":        round(score, 1) if score is not None else None,
    }


def _target_12m(price, cagr_pct):
    return round(price * (1 + cagr_pct / 100), 0)


def run():
    W = 78
    print(f"\n{'=' * W}")
    print("  INDIA CYCLE HUNTER  |  Find the next supercycle sector, early")
    print(f"  Target: 60-100%/yr via 3 concentrated positions, 12-18 month hold")
    print(f"  Universe: {len(UNIVERSE)} stocks across 8 sectors  |  Data: Yahoo Finance")
    print(f"{'=' * W}")
    print(f"\n  Fetching {FETCH_DAYS}-day bars for {len(UNIVERSE)} stocks...\n")

    results = []
    failed  = []

    for ticker, (sector, desc) in UNIVERSE.items():
        bars = fetch_bars(ticker, FETCH_DAYS)
        if len(bars) < 240:
            failed.append(ticker)
            continue
        r = _analyze(ticker, sector, desc, bars)
        if r:
            results.append(r)

    if failed:
        print(f"  Skipped (insufficient data): {', '.join(t.replace('.NS','') for t in failed)}\n")

    # ── Full scan table ───────────────────────────────────────────────────
    print(f"  {'Stock':<14} {'Sector':<10} {'Price':>8} {'CAGR':>7} "
          f"{'Down52w':>8} {'6mMom':>7} {'Stage2':>9} {'Age':>8}  {'Score':>6}")
    print("  " + "-" * 74)

    for r in sorted(results, key=lambda x: (x["score"] or -999), reverse=True):
        name  = r["ticker"].replace(".NS", "")
        age   = r["cycle_age"]
        s2    = "YES" if r["in_stage2"] else "no"
        score = f"{r['score']:.0f}" if r["score"] is not None else "--"
        held  = f"{r['stage2_held']}d" if r["in_stage2"] else ""
        print(f"  {name:<14} {r['sector']:<10} Rs.{r['price']:>7,.0f}  "
              f"{r['cagr']:>+5.0f}%  -{r['pct_down']:>4.0f}%  "
              f"{r['mom_6m']:>+5.0f}%  {s2:>5} {held:>5}  {age:>8}  {score:>6}")

    # ── Top picks ─────────────────────────────────────────────────────────
    candidates = [r for r in results if r["score"] is not None]
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top3 = candidates[:TOP_N]

    if not top3:
        print("\n  No stocks currently in early/mid Stage2 with positive score.")
        print("  Check again after the next market correction or new sector rotation.")
        return

    print(f"\n{'=' * W}")
    print(f"  TOP {TOP_N} CYCLE PICKS  --  Buy NOW, hold 12-18 months")
    print(f"{'=' * W}\n")

    total_expected_pnl = 0
    for rank, r in enumerate(top3, 1):
        name    = r["ticker"].replace(".NS", "")
        alloc   = ALLOC_PER_STOCK
        qty     = max(1, int(alloc / r["price"]))
        cost    = round(qty * r["price"], 0)
        stop    = round(r["price"] * (1 - TRAILING_STOP_PCT), 0)
        tgt_12m = _target_12m(r["price"], r["cagr"])
        exp_pnl = (tgt_12m - r["price"]) * qty
        total_expected_pnl += exp_pnl
        age_label = {"early": "EARLY STAGE2 -- ideal entry",
                     "mid":   "MID STAGE2 -- still room to run",
                     "late":  "LATE STAGE2 -- higher risk"}.get(r["cycle_age"], r["cycle_age"])

        print(f"  #{rank}  {name}  ({r['sector']})")
        print(f"      {r['desc']}")
        print(f"      Score:        {r['score']:.0f}/100  |  Cycle: {age_label}")
        print(f"      3yr CAGR:     {r['cagr']:+.0f}%/yr")
        print(f"      Pullback:     {r['pct_down']:.0f}% below 52w high of Rs.{r['high52']:,.0f}")
        print(f"      6-month mom:  {r['mom_6m']:+.0f}%")
        print()
        print(f"      ENTRY:        Rs.{r['price']:,.0f}  today")
        print(f"      ALLOCATE:     Rs.{cost:,.0f}  ({qty} shares at Rs.{alloc:,.0f} budget)")
        print(f"      STOP (25%):   Rs.{stop:,.0f}  -- set as trailing, wide enough for corrections")
        print(f"      TARGET (12m): Rs.{tgt_12m:,.0f}  (based on {r['cagr']:+.0f}%/yr CAGR)")
        print(f"      EXPECTED PnL: Rs.{exp_pnl:>+,.0f}  on Rs.{cost:,.0f} invested")
        print()

    total_roi = total_expected_pnl / ACCOUNT_INR * 100
    print(f"  {'-' * 60}")
    print(f"  Combined expected P&L:  Rs.{total_expected_pnl:>+,.0f}")
    print(f"  Capital deployed:       Rs.{TOP_N * ALLOC_PER_STOCK:,.0f} of Rs.{ACCOUNT_INR:,.0f}")
    print(f"  Expected portfolio ROI: {total_roi:+.0f}%  over 12 months")
    print()

    # ── Sector heat map ───────────────────────────────────────────────────
    print(f"{'=' * W}")
    print("  SECTOR CYCLE MAP  (all sectors, cycle age)")
    print(f"{'=' * W}\n")

    sectors = {}
    for r in results:
        s = r["sector"]
        if s not in sectors:
            sectors[s] = []
        sectors[s].append(r)

    for sector_name, stocks in sorted(sectors.items()):
        in_s2   = [s for s in stocks if s["in_stage2"]]
        broken  = [s for s in stocks if not s["in_stage2"]]
        early   = [s for s in in_s2 if s["cycle_age"] == "early"]
        mid     = [s for s in in_s2 if s["cycle_age"] == "mid"]
        late    = [s for s in in_s2 if s["cycle_age"] == "late"]

        if early:
            status = "EARLY  <-- opportunity"
        elif mid:
            status = "MID    -- still running"
        elif late:
            status = "LATE   -- extended, caution"
        elif broken:
            status = "BROKEN -- avoid"
        else:
            status = "MIXED"

        names = ", ".join(s["ticker"].replace(".NS","") for s in stocks[:4])
        print(f"  {sector_name:<12}  {status:<28}  [{names}]")

    # ── Rules ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print("  RULES FOR 60-100%/yr")
    print(f"{'=' * W}\n")
    print("  1. Buy all 3 picks TODAY at market price -- don't wait for a dip")
    print("  2. Allocate Rs.2,00,000 per stock (20% each, Rs.4L remaining as cash)")
    print("  3. Set a 25% trailing stop (mental or broker GTT) -- not the bot's ATR stop")
    print("  4. CHECK ONCE A WEEK. Not daily. Daily checking leads to panic selling.")
    print("  5. EXIT only when: (a) 25% trailing stop hit, OR (b) price falls below SMA200")
    print("  6. If a stock hits +50%, move stop to +25% (lock in profit, let rest run)")
    print("  7. Target: hold all 3 for 12-18 months unless a stop fires")
    print()
    print("  THE ONE RULE THAT KILLS RETURNS: selling a winner early.")
    print("  MAZDOCK gave 94%/yr because someone held through 3 corrections of 15-20%.")
    print("  The 25% stop is wide INTENTIONALLY. Tighter = shaken out = miss the run.")
    print(f"\n{'=' * W}\n")


if __name__ == "__main__":
    run()

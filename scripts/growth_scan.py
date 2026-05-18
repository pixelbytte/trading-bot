"""Detailed signal diagnostic for the high-growth cheap stocks."""
import os
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from brokers.alpaca import get_bars
from alpaca.data.models import Bar
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator

GROWTH_TICKERS = ["SOUN", "MRVL", "IONQ", "HIMS", "PLTR", "CRWD"]


def _to_dicts(bars):
    result = []
    for b in bars:
        if isinstance(b, Bar):
            result.append({"open": float(b.open), "high": float(b.high),
                           "low": float(b.low), "close": float(b.close),
                           "volume": float(b.volume)})
        elif isinstance(b, dict):
            result.append(b)
    return result


def diagnose(ticker, bar_dicts):
    df = pd.DataFrame(bar_dicts)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["volume"] = df["volume"].astype(float)

    df["sma200"] = SMAIndicator(close=df["close"], window=200).sma_indicator()
    df["sma50"] = SMAIndicator(close=df["close"], window=50).sma_indicator()
    df["sma10"] = SMAIndicator(close=df["close"], window=10).sma_indicator()
    df["sma30"] = SMAIndicator(close=df["close"], window=30).sma_indicator()
    df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()
    df["vol_avg50"] = df["volume"].rolling(50).mean()
    df["recent_high20"] = df["high"].rolling(20).max()
    df = df.dropna()

    last = df.iloc[-1]
    close = float(last["close"])
    rsi = float(last["rsi"])
    sma200 = float(last["sma200"])
    sma50 = float(last["sma50"])
    sma10 = float(last["sma10"])
    sma30 = float(last["sma30"])
    vol_today = float(last["volume"])
    vol_avg = float(last["vol_avg50"])
    recent_high = float(last["recent_high20"])

    # 52w high
    prior_52w = float(df["close"].iloc[:-1].max())
    new_high = close > prior_52w

    # Volume ratio
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0

    # 3-month return
    rs_price = float(df["close"].iloc[-64]) if len(df) >= 64 else float(df["close"].iloc[0])
    return_3m = (close - rs_price) / rs_price

    # Pullback from 20-day high
    pct_from_high = (recent_high - close) / recent_high

    print(f"\n{ticker} @ ${close:.2f}")
    print(f"  RSI={rsi:.1f}  (breakout_52w needs 50-80, rs_pullback needs 35-55)")
    print(f"  SMA200=${sma200:.2f}  {'OK above' if close > sma200 else 'BELOW — fails breakout_52w'}")
    print(f"  SMA50=${sma50:.2f}   {'OK above' if close > sma50 else 'BELOW — fails rs_pullback'}")
    print(f"  SMA10=${sma10:.2f} vs SMA30=${sma30:.2f}  ({'SMA10>SMA30' if sma10 > sma30 else 'SMA10<SMA30'} — needed for ma_rsi crossover)")
    print(f"  52W high: close {'IS' if new_high else 'is NOT'} a new high (prior max ${prior_52w:.2f})")
    print(f"  Volume ratio: {vol_ratio:.2f}x avg  (breakout_52w needs 1.5x)")
    print(f"  3M return: {return_3m*100:+.1f}%  (rs_pullback needs +12% min)")
    print(f"  Pullback from 20D high: {pct_from_high*100:.1f}%  (rs_pullback needs 5-15%)")

    # What would it take?
    blockers = []
    # breakout_52w
    if not new_high:
        blockers.append(f"breakout_52w: needs close > ${prior_52w:.2f} (currently ${prior_52w - close:.2f} away)")
    if vol_ratio < 1.5:
        blockers.append(f"breakout_52w: needs volume {1.5:.1f}x avg (currently {vol_ratio:.2f}x)")
    if not (50 <= rsi <= 80):
        blockers.append(f"breakout_52w: RSI {rsi:.1f} outside 50-80")
    # rs_pullback
    if return_3m < 0.12:
        blockers.append(f"rs_pullback: 3M return {return_3m*100:.1f}% < 12% min")
    if not (0.05 <= pct_from_high <= 0.15):
        blockers.append(f"rs_pullback: pullback {pct_from_high*100:.1f}% outside 5-15% window")
    if not (35 <= rsi <= 55):
        blockers.append(f"rs_pullback: RSI {rsi:.1f} outside 35-55")

    if blockers:
        print(f"  Blockers:")
        for b in blockers:
            print(f"    - {b}")
    else:
        print(f"  => SHOULD HAVE FIRED — check strategy logic")


print("=== GROWTH STOCK DETAILED DIAGNOSTIC ===")

for ticker in GROWTH_TICKERS:
    try:
        bars = get_bars(ticker, days=300)
        if not bars:
            print(f"\n{ticker}: no bar data")
            continue
        bar_dicts = _to_dicts(bars)
        diagnose(ticker, bar_dicts)
    except Exception as e:
        print(f"\n{ticker}: ERROR — {e}")

print("\nDone.")

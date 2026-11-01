"""Verification of downloaded cash equity market data."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/equity")

files = [
    ("prices_daily.csv",    "date"),
    ("dividends.csv",       "date"),
    ("splits.csv",          "date"),
    ("prices_adj_close.csv","date"),
]

print(f"{'File':<30} {'Rows':>8}  {'KB':>6}  Date range")
print("-" * 75)
for fname, dcol in files:
    f = base / fname
    if not f.exists():
        print(f"{fname:<30} MISSING")
        continue
    df = pd.read_csv(f, low_memory=False)
    kb = f.stat().st_size // 1024
    dr = f"{df[dcol].min()} -> {df[dcol].max()}" if dcol in df.columns else ""
    print(f"{fname:<30} {len(df):>8}  {kb:>6}  {dr}")

print()
print("Daily prices — rows per ticker:")
prices = pd.read_csv(base / "prices_daily.csv")
print(prices.groupby("ticker").agg(
    rows=("date","count"),
    first=("date","min"),
    last=("date","max"),
    latest_close=("close","last")
).to_string())

print()
print("Last 3 trading days (NVDA, AVGO):")
p = prices[prices["ticker"].isin(["NVDA","AVGO"])].tail(6)
print(p[["date","ticker","open","high","low","close","volume","adj_close"]].to_string(index=False))

print()
print("Dividends per ticker (total count + most recent):")
divs = pd.read_csv(base / "dividends.csv")
for ticker, grp in divs.groupby("ticker"):
    latest = grp.sort_values("date").iloc[-1]
    print(f"  {ticker}: {len(grp)} payments  last={latest['date']}  ${latest['amount']:.4f}")

print()
print("Split history:")
splits = pd.read_csv(base / "splits.csv")
print(splits[["date","ticker","numerator","denominator","ratio"]].to_string(index=False))

"""Verification of downloaded equity derivatives market data."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/options")

# Option chains
chains_dir = base / "chains"
print("Option chains (CBOE):")
print(f"{'Underlying':<8} {'Contracts':>10}  {'Expiries':>8}  {'Min DTE':>8}  {'Max DTE':>8}  {'KB':>6}")
print("-" * 60)
total_contracts = 0
for f in sorted(chains_dir.glob("*_chains.csv")):
    ticker = f.stem.replace("_chains", "")
    df = pd.read_csv(f)
    kb = f.stat().st_size // 1024
    n_exp = df["expiry_date"].nunique()
    min_dte = df["days_to_expiry"].min()
    max_dte = df["days_to_expiry"].max()
    total_contracts += len(df)
    print(f"{ticker:<8} {len(df):>10}  {n_exp:>8}  {min_dte:>8}  {max_dte:>8}  {kb:>6}")
print(f"{'TOTAL':<8} {total_contracts:>10}")

print()
print("Sample NVDA chain (ATM calls, nearest expiries):")
nvda = pd.read_csv(chains_dir / "NVDA_chains.csv")
nvda_calls = nvda[nvda["put_call"] == "call"].copy()
spot_approx = nvda_calls.loc[nvda_calls["delta"].sub(0.5).abs().idxmin(), "strike"] if "delta" in nvda_calls.columns else nvda_calls["strike"].median()
sample = nvda_calls[nvda_calls["days_to_expiry"] <= 30].sort_values("days_to_expiry")
nearest_exp = sample["expiry_date"].iloc[0] if not sample.empty else None
if nearest_exp:
    near_calls = nvda_calls[nvda_calls["expiry_date"] == nearest_exp].sort_values("strike")
    mid_idx = len(near_calls) // 2
    print(near_calls.iloc[max(0, mid_idx-2):mid_idx+3][
        ["expiry_date", "days_to_expiry", "strike", "bid", "ask", "iv", "delta", "vega"]
    ].to_string(index=False))

print()
print("Sample SPX puts (3M ~90 DTE, strikes near ATM):")
spx = pd.read_csv(chains_dir / "SPX_chains.csv")
spx_3m = spx[(spx["put_call"] == "put") &
              (spx["days_to_expiry"] >= 80) &
              (spx["days_to_expiry"] <= 100)].copy()
if not spx_3m.empty:
    mid_strike_idx = len(spx_3m) // 2
    print(spx_3m.sort_values("strike").iloc[
        max(0, mid_strike_idx-2):mid_strike_idx+3
    ][["expiry_date", "days_to_expiry", "strike", "bid", "ask", "iv", "delta"]].to_string(index=False))

print()
print("VIX term structure (latest close):")
vol_dir = base / "vol_indices"
vix_indices = ["VIX9D", "VIX", "VIX3M", "VIX6M", "VVIX", "SKEW"]
for name in vix_indices:
    f = vol_dir / f"{name}_History.csv"
    if f.exists():
        df = pd.read_csv(f)
        latest = df.iloc[-1]
        print(f"  {name:<8} close={latest['close']:.2f}  date={latest['date']}  rows={len(df)}")

print()
single_name = ["VXAPL", "VXAZN", "VXGOG"]
print("Single-stock vol indices (latest close):")
for name in single_name:
    f = vol_dir / f"{name}_History.csv"
    if f.exists():
        df = pd.read_csv(f)
        latest = df.iloc[-1]
        print(f"  {name:<8} close={latest['close']:.2f}  date={latest['date']}")

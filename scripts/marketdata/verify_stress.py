"""Verification of downloaded stress-scenario calibration data (§2.8, §4.9, Methodology B)."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/stress")


def _span(df, col="date"):
    return f"{df[col].min()} -> {df[col].max()}"


print("=== fred_stress_macro.csv - IG/HY OAS + VIX ===")
fm = base / "fred_stress_macro.csv"
if not fm.exists():
    print("  MISSING")
else:
    df = pd.read_csv(fm)
    print(f"  {len(df)} rows, {fm.stat().st_size // 1024} KB, period {_span(df)}")
    for col in [c for c in df.columns if c != "date"]:
        s = df[col].dropna()
        if s.empty:
            print(f"    {col:<14} (no values)")
            continue
        last = df.dropna(subset=[col]).iloc[-1]
        print(f"    {col:<14} {len(s):>5} obs  min={s.min():>7.2f}  max={s.max():>7.2f}  "
              f"last={last[col]:>7.2f} ({last['date']})")

print()
print("=== move_index.csv - ICE BofAML MOVE (rates vol) ===")
mv = base / "move_index.csv"
if not mv.exists():
    print("  MISSING")
else:
    df = pd.read_csv(mv)
    s = df["close"].dropna()
    print(f"  {len(df)} rows, {mv.stat().st_size // 1024} KB, period {_span(df)}")
    print(f"    close   min={s.min():.2f}  max={s.max():.2f}  last={df.iloc[-1]['close']:.2f} "
          f"({df.iloc[-1]['date']})")

print()
print("=== eia_power_demand.csv - datacenter-grid demand ===")
ep = base / "eia_power_demand.csv"
if not ep.exists():
    print("  MISSING (needs free EIA_API_KEY in .env - see config note)")
else:
    df = pd.read_csv(ep)
    print(f"  {len(df)} rows, {ep.stat().st_size // 1024} KB, period {_span(df)}")
    print("  Rows + latest daily demand per balancing authority:")
    for resp, g in df.groupby("respondent"):
        last = g.sort_values("date").iloc[-1]
        print(f"    {resp:<6} {len(g):>5} rows  last={last['demand_mwh']:>12,.0f} MWh ({last['date']})")

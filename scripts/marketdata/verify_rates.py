"""Quick verification of downloaded rates market data."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/rates")

files = [
    "nyfed_sofr_rates.csv",
    "fred_rates.csv",
    "pensford_sofr_forward.csv",
    "pensford_historical_sofr.csv",
    "dtcc_sdr_irs.csv",
    "dtcc_sdr_ir_options.csv",
]

print(f"{'File':<45} {'Rows':>8}  {'KB':>6}  Date range")
print("-" * 90)
for fname in files:
    f = base / fname
    if not f.exists():
        print(f"{fname:<45} MISSING")
        continue
    df = pd.read_csv(f)
    kb = f.stat().st_size // 1024
    date_col = "as_of_date" if "as_of_date" in df.columns else "date"
    if date_col in df.columns:
        dr = f"{df[date_col].min()} -> {df[date_col].max()}"
    else:
        dr = "(no date col)"
    print(f"{fname:<45} {len(df):>8}  {kb:>6}  {dr}")

print()
print("NY Fed SOFR — last 5 rows:")
nyfed = pd.read_csv(base / "nyfed_sofr_rates.csv")
print(nyfed.tail(5)[["date", "sofr_rate", "effr_rate"]].to_string(index=False))

print()
print("FRED rates — last 5 rows (key cols):")
fred = pd.read_csv(base / "fred_rates.csv")
print(fred.tail(5)[["date", "SOFR", "DGS2", "DGS5", "DGS10", "DGS30"]].to_string(index=False))

print()
print("Pensford forward curve — latest snapshot (first 8 months):")
pens = pd.read_csv(base / "pensford_sofr_forward.csv")
latest = pens[pens["as_of_date"] == pens["as_of_date"].max()]
print(latest[["as_of_date", "settlement_date", "forward_sofr", "forward_sofr_upper"]].head(8).to_string(index=False))

irs = base / "dtcc_sdr_irs.csv"
if irs.exists():
    print()
    print("DTCC IRS — sample (5 rows):")
    df_irs = pd.read_csv(irs, dtype=str)  # 19-digit identifiers: never parse as numbers
    print(df_irs.head(5)[["date", "Dissemination Identifier", "Action type", "UPI FISN",
                           "Fixed rate-Leg 1", "Expiration Date"]].to_string(index=False))

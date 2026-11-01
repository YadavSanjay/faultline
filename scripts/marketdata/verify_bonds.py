"""Verification of downloaded bonds market data."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/bonds")

files = [
    ("treasury_par_yields.csv",        "date"),
    ("treasurydirect_auctioned.csv",   "auctionDate"),
    ("treasurydirect_announced.csv",   "announcementDate"),
    ("edgar_bond_filings.csv",         "filing_date"),
    ("finra_treasury_daily.csv",       "date"),
]

print(f"{'File':<40} {'Rows':>8}  {'KB':>6}  Date range")
print("-" * 85)
for fname, dcol in files:
    f = base / fname
    if not f.exists():
        print(f"{fname:<40} MISSING")
        continue
    df = pd.read_csv(f, low_memory=False)
    kb = f.stat().st_size // 1024
    if dcol in df.columns:
        dr = f"{df[dcol].dropna().min()} -> {df[dcol].dropna().max()}"
    else:
        dr = f"(no {dcol} col)"
    print(f"{fname:<40} {len(df):>8}  {kb:>6}  {dr}")

print()
print("UST Par Curve — last 3 rows (key tenors):")
tc = pd.read_csv(base / "treasury_par_yields.csv")
print(tc.tail(3)[["date", "t2yr", "t5yr", "t10yr", "t30yr"]].to_string(index=False))

print()
print("On-the-run UST (most recent non-reopening per term):")
aucted = pd.read_csv(base / "treasurydirect_auctioned.csv")
for term in ["2-Year", "10-Year"]:
    sub = aucted[(aucted["securityTerm"] == term) & (aucted["reopening"] == "No")]
    if not sub.empty:
        r = sub.sort_values("auctionDate", ascending=False).iloc[0]
        print(f"  {term}: CUSIP={r['cusip']}  coupon={r['interestRate']}%  matures={r['maturityDate']}")

print()
print("EDGAR filings by issuer+form:")
edgar = pd.read_csv(base / "edgar_bond_filings.csv")
print(edgar.groupby(["issuer", "form_type"]).size().to_string())

print()
print("FINRA Treasury Daily — last 3 rows:")
fin = pd.read_csv(base / "finra_treasury_daily.csv") if (base / "finra_treasury_daily.csv").exists() else pd.DataFrame()
if not fin.empty:
    print(fin.tail(3).to_string(index=False))

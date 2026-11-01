"""Verification of downloaded credit derivatives market data (trades #28-30)."""
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/credit")

files = [
    ("dtcc_single_name_cds.csv", "date"),
    ("dtcc_index_cds.csv",       "date"),
]

print(f"{'File':<32} {'Rows':>8}  {'KB':>6}  Date range")
print("-" * 75)
for fname, dcol in files:
    f = base / fname
    if not f.exists():
        print(f"{fname:<32} MISSING")
        continue
    df = pd.read_csv(f, low_memory=False)
    kb = f.stat().st_size // 1024
    dr = f"{df[dcol].dropna().min()} -> {df[dcol].dropna().max()}" if dcol in df.columns else f"(no {dcol})"
    print(f"{fname:<32} {len(df):>8}  {kb:>6}  {dr}")


def _spread_bp(series: pd.Series) -> pd.Series:
    """Spread-Leg 1 is a decimal fraction; convert to basis points."""
    return pd.to_numeric(series, errors="coerce") * 1e4


print()
print("Single-name CDS (trades #28 ORCL, #29 META) — reference entities found:")
snf = base / "dtcc_single_name_cds.csv"
if snf.exists():
    sn = pd.read_csv(snf, low_memory=False)
    print(sn["Underlying Asset Name"].value_counts().head(12).to_string())
    print()
    print("  Spread (bp) by entity — count / median / min / max (non-null spreads):")
    sn["spread_bp"] = _spread_bp(sn["Spread-Leg 1"])
    sn["entity"] = sn["Underlying Asset Name"].astype(str).str.upper().str.extract(
        r"(ORACLE|META)", expand=False
    )
    g = sn.dropna(subset=["spread_bp"]).groupby("entity")["spread_bp"]
    if len(g):
        stats = g.agg(["count", "median", "min", "max"]).round(1)
        print(stats.to_string())
    else:
        print("  (no non-null Spread-Leg 1 prints — economics carried as upfront Price)")

print()
print("Index CDS (trade #30 CDX.NA.IG) — indices found:")
idxf = base / "dtcc_index_cds.csv"
if idxf.exists():
    idx = pd.read_csv(idxf, low_memory=False)
    print(idx["UPI Underlier Name"].value_counts().head(8).to_string())
    print()
    print("  CDX.NA.IG spread (bp) by Expiration Date bucket (tenor) — latest snapshot:")
    ig = idx[idx["UPI Underlier Name"].astype(str) == "CDX.NA.IG"].copy()
    if not ig.empty:
        ig["spread_bp"] = _spread_bp(ig["Spread-Leg 1"])
        latest = ig["date"].max()
        snap = ig[ig["date"] == latest].dropna(subset=["spread_bp"])
        if not snap.empty:
            by_exp = snap.groupby("Expiration Date")["spread_bp"].agg(["count", "median"]).round(2)
            print(f"  (snapshot date {latest})")
            print(by_exp.sort_values("count", ascending=False).head(6).to_string())
        else:
            print(f"  (no non-null spreads on {latest})")

print()
print("CDX annex (composition reference, semi-annual — manual download if blocked):")
annex_dir = base / "cdx_annex"
if annex_dir.exists():
    pdfs = sorted(annex_dir.glob("*.pdf"))
    if pdfs:
        for p in pdfs:
            print(f"  {p.name}  ({p.stat().st_size // 1024} KB)")
    else:
        print("  (no annex PDF — source disabled; download manually per config note)")
else:
    print("  (no annex dir — source disabled; download manually per config note)")

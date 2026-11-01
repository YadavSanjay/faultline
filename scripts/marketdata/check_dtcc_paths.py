"""Inspect DTCC RATES CSV structure in detail."""
import io, requests, zipfile, pandas as pd

h = {"User-Agent": "Faultline/1.0"}
url = "https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_2026_06_13.zip"
r = requests.get(url, headers=h, timeout=60)
with zipfile.ZipFile(io.BytesIO(r.content)) as z:
    df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)

# Show non-null columns only
non_null_cols = [c for c in df.columns if df[c].notna().any()]
print(f"Non-null columns ({len(non_null_cols)} of {len(df.columns)}):")
for c in non_null_cols:
    sample = df[c].dropna().iloc[0] if df[c].notna().any() else "(empty)"
    print(f"  {c}: {sample}")

print()
print("First 3 rows of non-null columns:")
print(df[non_null_cols].head(3).to_string())

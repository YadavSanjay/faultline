"""Check DTCC SEC SDR CREDITS path for single-name US CDS."""
import io, requests, zipfile, pandas as pd

h = {"User-Agent": "Faultline/1.0"}

# Test SEC path variations
paths = [
    "https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_2026_06_16.zip",
    "https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_2026_06_13.zip",
    "https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_2026_06_12.zip",
]
for url in paths:
    r = requests.head(url, headers=h, timeout=15)
    print(f"  {r.status_code}  {url.split('/')[-1]}")

# Fetch one that's 200
for url in paths:
    r = requests.get(url, headers=h, timeout=60)
    if r.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
        print(f"\nSEC Credits {url.split('_')[-1]}: {len(df)} rows, {len(r.content)//1024} KB")
        name_col = "Underlying Asset Name"
        upi_col  = "UPI Underlier Name"
        print(f"'{name_col}' non-null: {df[name_col].notna().sum() if name_col in df.columns else 'MISSING'}")
        print(f"'{upi_col}' non-null: {df[upi_col].notna().sum() if upi_col in df.columns else 'MISSING'}")
        if upi_col in df.columns and df[upi_col].notna().any():
            print(f"\nTop 20 '{upi_col}':")
            print(df[upi_col].value_counts().head(20).to_string())
        for kw in ["ORACLE", "META", "APPLE", "MICROSOFT", "AMAZON"]:
            for col in [name_col, upi_col]:
                if col in df.columns:
                    m = df[df[col].astype(str).str.upper().str.contains(kw, na=False)]
                    if len(m) > 0:
                        print(f"\n{kw} in {col}: {m[col].unique().tolist()[:3]}")
        break

"""Verification of downloaded FX derivatives market data (trades #26-27)."""
import json
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/fx")

files = [
    "ecb_fx_rates.csv",
    "fed_h10_fx_rates.csv",
    "boj_tonar.csv",
    "cbc_twd_ndf_fix.csv",
]

print(f"{'File':<28} {'Rows':>8}  {'KB':>6}  Date range")
print("-" * 72)
for fname in files:
    f = base / fname
    if not f.exists():
        print(f"{fname:<28} MISSING")
        continue
    df = pd.read_csv(f)
    kb = f.stat().st_size // 1024
    dr = f"{df['date'].min()} -> {df['date'].max()}" if "date" in df.columns else "(no date)"
    print(f"{fname:<28} {len(df):>8}  {kb:>6}  {dr}")


def _tail(fname, cols, n=5):
    f = base / fname
    if not f.exists():
        print(f"  {fname} MISSING")
        return None
    df = pd.read_csv(f)
    have = [c for c in cols if c in df.columns]
    print(df.tail(n)[have].to_string(index=False))
    return df


print()
print("USD/JPY — trade #27 (ECB cross, primary):")
ecb = _tail("ecb_fx_rates.csv", ["date", "usd_eur", "jpy_eur", "usdjpy"])

print()
print("Fed H.10 — USD/TWD (trade #26 primary) + USD/JPY (backup):")
h10 = _tail("fed_h10_fx_rates.csv", ["date", "usdtwd", "usdjpy"])

print()
print("BOJ TONAR — JPY OIS anchor (CIP r_JPY for trade #27):")
_tail("boj_tonar.csv", ["date", "tonar_rate"])

print()
print("CBC USD/TWD NDF fixing — trade #26 settlement reference:")
cbc = _tail("cbc_twd_ndf_fix.csv", ["date", "usdtwd_cbc"])

# Cross-source sanity: ECB-derived USD/JPY vs Fed H.10 USD/JPY should agree closely;
# Fed H.10 USD/TWD vs CBC fixing should agree closely.
print()
print("Cross-source consistency (latest overlapping date):")
if ecb is not None and h10 is not None:
    m = ecb[["date", "usdjpy"]].merge(
        h10[["date", "usdjpy"]], on="date", suffixes=("_ecb", "_h10")
    ).dropna()
    if not m.empty:
        r = m.iloc[-1]
        diff = abs(r["usdjpy_ecb"] - r["usdjpy_h10"])
        print(f"  USD/JPY {r['date']}: ECB={r['usdjpy_ecb']:.3f}  H10={r['usdjpy_h10']:.3f}  abs_diff={diff:.3f}")
if h10 is not None and cbc is not None:
    m = h10[["date", "usdtwd"]].merge(cbc, on="date").dropna()
    if not m.empty:
        r = m.iloc[-1]
        diff = abs(r["usdtwd"] - r["usdtwd_cbc"])
        print(f"  USD/TWD {r['date']}: H10={r['usdtwd']:.4f}  CBC={r['usdtwd_cbc']:.4f}  abs_diff={diff:.4f}")

# Pricing constants surfaced from config (TWD discount rate is a manual constant).
print()
cfg_path = Path("configs/marketdata/fx/fx_marketdata.json")
if cfg_path.exists():
    pc = json.loads(cfg_path.read_text(encoding="utf-8")).get("pricing_constants", {})
    print(f"TWD_DISCOUNT_RATE (config constant, manual): {pc.get('TWD_DISCOUNT_RATE')}")

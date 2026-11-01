"""Verification of downloaded reference / static market data (§2.7, §4.8)."""
import json
import pandas as pd
from pathlib import Path

base = Path("data/marketdata/reference")

print("=== instruments.json — canonical identifier map ===")
ij = base / "instruments.json"
if not ij.exists():
    print("  MISSING")
else:
    doc = json.loads(ij.read_text(encoding="utf-8"))
    insts = doc.get("instruments", [])
    resolved = [i for i in insts if i.get("figi")]
    print(f"  {len(insts)} trades total, {len(resolved)} resolved to a FIGI")
    print()
    print(f"  {'#':>2}  {'display':<20} {'asset':<9} {'FIGI':<14} {'ticker':<18} exch")
    print("  " + "-" * 78)
    for i in insts:
        figi = i.get("figi") or "-"
        tkr = (i.get("figi_ticker") or i.get("underlier") or i.get("pair") or "-")
        exch = i.get("exchange") or "-"
        print(f"  {i.get('trade'):>2}  {i.get('display',''):<20} {i.get('asset_class',''):<9} "
              f"{figi:<14} {str(tkr):<18} {exch}")

print()
print("=== instruments_figi.csv — resolved flat table ===")
fc = base / "instruments_figi.csv"
if fc.exists():
    df = pd.read_csv(fc)
    print(f"  {len(df)} rows, {fc.stat().st_size // 1024} KB")
    print(df[["trade", "display", "figi", "figi_ticker", "market_sector"]].to_string(index=False))
else:
    print("  MISSING")

print()
print("=== edgar_xbrl_facts.csv — fundamentals + capex ===")
ef = base / "edgar_xbrl_facts.csv"
if not ef.exists():
    print("  MISSING")
else:
    df = pd.read_csv(ef)
    print(f"  {len(df)} facts, {ef.stat().st_size // 1024} KB, "
          f"period {df['end'].min()} -> {df['end'].max()}")
    print()
    print("  Facts per issuer x tag:")
    piv = df.pivot_table(index="ticker", columns="tag", values="val", aggfunc="count", fill_value=0)
    # shorten tag column names for display
    piv.columns = [c[:22] for c in piv.columns]
    print(piv.to_string())

    print()
    print("  Latest hyperscaler quarterly capex (PP&E + productive-assets tags):")
    capex_tags = ["PaymentsToAcquirePropertyPlantAndEquipment",
                  "PaymentsToAcquireProductiveAssets"]
    capex = df[df["tag"].isin(capex_tags)].copy()
    # quarterly facts only (period ~ <=100 days)
    capex["span"] = (pd.to_datetime(capex["end"]) - pd.to_datetime(capex["start"])).dt.days
    q = capex[capex["span"] <= 100]
    latest = q.sort_values("end").groupby("ticker").tail(1)
    for _, r in latest.sort_values("val", ascending=False).iterrows():
        print(f"    {r['ticker']:<6} {r['end']}  ${r['val']/1e9:>7.2f} B  ({r['fp']} {int(r['fy'])})")

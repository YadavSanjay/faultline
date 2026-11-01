// Instrument-adaptive measure presentation (PRICING_UI §5). Each trade_type has an
// ordered set of headline measures with plain-English captions and units; any measure not
// in the registry still renders (generic fallback) so nothing is hidden.

import type { Metric } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";

type Fmt = "usd" | "pct" | "price" | "num" | "raw";

interface Spec {
  key: string;
  label: string;
  fmt: Fmt;
  caption?: string;
}

const REGISTRY: Record<string, Spec[]> = {
  ois_swap: [
    { key: "fair_rate", label: "Fair rate", fmt: "pct", caption: "fixed rate that zeroes PV today" },
    { key: "fixed_rate", label: "Fixed rate", fmt: "pct", caption: "your contract rate" },
    { key: "dv01", label: "DV01", fmt: "usd", caption: "P&L per 1bp rate move" },
    { key: "notional", label: "Notional", fmt: "usd" },
  ],
  european_swaption: [
    { key: "atm_or_strike", label: "Strike / ATM", fmt: "pct" },
    { key: "underlying_swap_fair_rate", label: "Underlying fair rate", fmt: "pct" },
    { key: "normal_vol_bp", label: "Normal vol", fmt: "num", caption: "bp (Bachelier)" },
    { key: "vega_1bp", label: "Vega", fmt: "usd", caption: "per +1bp vol" },
  ],
  bermudan_swaption: [
    { key: "normal_vol_bp", label: "Normal vol", fmt: "num", caption: "bp (Bachelier)" },
    { key: "vega_1bp", label: "Vega", fmt: "usd", caption: "per +1bp vol" },
  ],
  cap: [
    { key: "atm_or_strike", label: "Strike", fmt: "pct" },
    { key: "normal_vol_bp", label: "Normal vol", fmt: "num", caption: "bp" },
    { key: "vega_1bp", label: "Vega", fmt: "usd", caption: "per +1bp vol" },
  ],
  corp_bond: [
    { key: "clean_price", label: "Clean price", fmt: "price" },
    { key: "dirty_price", label: "Dirty price", fmt: "price" },
    { key: "accrued", label: "Accrued", fmt: "price" },
    { key: "ytm", label: "YTM", fmt: "pct" },
    { key: "modified_duration", label: "Mod duration", fmt: "num" },
    { key: "dv01", label: "DV01", fmt: "usd", caption: "P&L per 1bp" },
  ],
  ust_bond: [
    { key: "clean_price", label: "Clean price", fmt: "price" },
    { key: "dirty_price", label: "Dirty price", fmt: "price" },
    { key: "accrued", label: "Accrued", fmt: "price" },
    { key: "ytm", label: "YTM", fmt: "pct" },
    { key: "modified_duration", label: "Mod duration", fmt: "num" },
    { key: "dv01", label: "DV01", fmt: "usd" },
  ],
  stock: [
    { key: "spot", label: "Spot", fmt: "price", caption: "latest close" },
    { key: "quantity", label: "Quantity", fmt: "num", caption: "shares (sign = direction)" },
    { key: "cash_delta", label: "Market value", fmt: "usd", caption: "quantity × spot" },
    { key: "dividend_yield", label: "Dividend yield", fmt: "pct", caption: "trailing" },
  ],
  equity_option: [
    { key: "delta_shares", label: "Delta", fmt: "num", caption: "d(PV)/d(spot), shares" },
    { key: "delta_cash", label: "Cash delta", fmt: "usd" },
    { key: "gamma", label: "Gamma", fmt: "num" },
    { key: "vega_1volpt", label: "Vega", fmt: "usd", caption: "per +1 vol point" },
    { key: "spot", label: "Spot", fmt: "price" },
    { key: "dividend_yield", label: "Div yield", fmt: "pct" },
  ],
  cds: [
    { key: "par_spread_bp", label: "Par spread", fmt: "num", caption: "bp (quoted)" },
    { key: "fair_spread_bp", label: "Fair spread", fmt: "num", caption: "bp (calibrated)" },
    { key: "cs01", label: "CS01", fmt: "usd", caption: "P&L per 1bp spread" },
    { key: "hazard", label: "Hazard", fmt: "pct", caption: "flat, calibrated to par" },
    { key: "recovery", label: "Recovery", fmt: "pct" },
  ],
  fx_forward: [
    { key: "spot", label: "Spot", fmt: "num" },
    { key: "strike", label: "Strike", fmt: "num", caption: "contract forward" },
    { key: "fair_forward", label: "Fair forward", fmt: "num", caption: "covered-interest-parity" },
    { key: "fx_delta_1pct", label: "FX delta", fmt: "usd", caption: "per +1% spot" },
    { key: "usd_notional", label: "USD notional", fmt: "usd" },
  ],
  xccy_swap: [
    { key: "spot", label: "Spot", fmt: "num" },
    { key: "usd_notional", label: "USD notional", fmt: "usd" },
    { key: "foreign_notional", label: "Foreign notional", fmt: "num" },
    { key: "fx_delta", label: "FX delta", fmt: "usd", caption: "per +1% spot" },
  ],
};

// One-line "how it's valued" caption per instrument (for the card / lineage).
export const PLAIN_ENGLISH: Record<string, string> = {
  ois_swap: "PV = discounted fixed coupons − projected floating coupons, both off the SOFR curve.",
  european_swaption: "Bachelier (normal-vol) option on the underlying swap; PV is the premium.",
  bermudan_swaption: "Multi-exercise option on the underlying swap, priced on a tree.",
  cap: "Sum of caplets, each a Bachelier option on the projected SOFR rate.",
  corp_bond: "Discounted cashflows on the SOFR curve plus the issuer credit spread.",
  ust_bond: "Discounted cashflows on the bootstrapped UST par curve.",
  stock: "Value = your share quantity × the latest close. Nothing else moves it.",
  equity_option:
    "Each leg is Black-Scholes priced off the chain's spot, dividend, and the implied vol at its strike/expiry; the trade value is the signed sum.",
  cds: "We solved for the hazard rate that reprices the quoted 5Y spread, then valued the premium vs protection legs off it.",
  fx_forward: "Covered-interest-parity: discount the USD and foreign legs, compare to the contract strike.",
  xccy_swap: "Fixed-for-fixed cross-currency swap with notional exchange; the foreign leg is converted at spot.",
};

function fmtValue(fmt: Fmt, v: number): { value: string; title?: string } {
  switch (fmt) {
    case "usd":
      return { value: humanizeUsd(v), title: exactUsd(v) };
    case "pct":
      return { value: `${(v * 100).toFixed(3)}%`, title: String(v) };
    case "price":
      return { value: v.toFixed(2), title: String(v) };
    case "num":
      return { value: v.toLocaleString("en-US", { maximumFractionDigits: 4 }), title: String(v) };
    default:
      return { value: String(v) };
  }
}

// Heuristic for measures not in the registry (so nothing is ever hidden).
function genericMetric(key: string, v: number): Metric {
  let fmt: Fmt = "num";
  if (Math.abs(v) >= 1e5) fmt = "usd";
  else if (v !== 0 && Math.abs(v) < 1) fmt = "pct";
  const f = fmtValue(fmt, v);
  return { label: prettyKey(key), value: f.value, title: f.title };
}

function prettyKey(key: string): string {
  return key.replace(/_/g, " ");
}

export function buildMeasures(tradeType: string, measures: Record<string, number>): Metric[] {
  const specs = REGISTRY[tradeType] ?? [];
  const used = new Set<string>();
  const out: Metric[] = [];

  for (const spec of specs) {
    if (!(spec.key in measures)) continue;
    used.add(spec.key);
    const f = fmtValue(spec.fmt, measures[spec.key]);
    out.push({ label: spec.label, value: f.value, title: f.title, caption: spec.caption });
  }
  // Remaining measures, in their natural order.
  for (const [key, v] of Object.entries(measures)) {
    if (used.has(key) || typeof v !== "number") continue;
    out.push(genericMetric(key, v));
  }
  return out;
}

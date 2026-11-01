// Risk-specific display helpers: plain-English captions per factor, loss/sensitivity
// formatting. VaR/ES are shown as positive losses; sensitivities keep their natural sign.

import { humanizeUsd } from "@/lib/format";

const CAPTION: Record<string, string> = {
  IR_PARALLEL: "P&L per +1bp parallel rate move",
  CS01: "P&L per +1bp credit-spread widening",
  VEGA_RATES: "per +1bp normal (rates) vol",
  VEGA_RATES_TERM: "per +1bp rates-vol term slope",
  VEGA_EQUITY: "per +1 equity vol point",
  VEGA_EQ_TERM: "per +1 vol-pt term slope",
  VEGA_EQ_SKEW: "per +1 unit put-skew tilt",
  EQUITY_DELTA: "per +1% underlier move",
  EQUITY_GAMMA: "convexity, per (1% move)²",
  FX_DELTA: "per +1% FX-spot move",
  FX_RATE: "per +1bp foreign rate",
};

export function factorCaption(factor: string): string {
  if (CAPTION[factor]) return CAPTION[factor];
  if (factor.startsWith("CS01:")) return `P&L per +1bp ${factor.slice(5)} credit-spread widening`;
  if (factor.startsWith("IR_KR:")) return `P&L per +1bp key-rate at ${factor.slice(6)}`;
  if (factor.startsWith("EQUITY_DELTA:")) return `per +1% ${factor.slice(13)} move`;
  if (factor.startsWith("VEGA_EQUITY:")) return `per +1 vol pt, ${factor.slice(12)}`;
  if (factor.startsWith("FX_DELTA:")) return `per +1% ${factor.slice(9)} spot`;
  if (factor.startsWith("FX_RATE:")) return `per +1bp ${factor.slice(8)} rate`;
  return "PV change per one bump unit";
}

// Headline book factors for the dashboard (shown if present). CS01 is kept for older reports;
// CS01:IG / CS01:HY are the per-rating-bucket keys once the book splits credit (Tier 3.9).
export const HEADLINE_FACTORS = ["IR_PARALLEL", "CS01", "CS01:IG", "CS01:HY", "VEGA_RATES", "EQUITY_DELTA", "FX_DELTA"];

// Risk-factor-class display labels for the VaR decomposition (Tier 1.3).
export const CLASS_LABEL: Record<string, string> = {
  rates: "Rates (IR)",
  credit: "Credit",
  equity: "Equity",
  equity_vol: "Equity vol",
  rates_vol: "Rates vol",
  fx: "FX",
  other: "Other",
};

export function classLabel(c: string): string {
  return CLASS_LABEL[c] ?? c;
}

export function sensTone(value: number): "gain" | "loss" | "neutral" {
  if (value > 0) return "gain";
  if (value < 0) return "loss";
  return "neutral";
}

export function lossText(v: number | undefined | null): string {
  if (v == null) return "—";
  return humanizeUsd(Math.abs(v));
}

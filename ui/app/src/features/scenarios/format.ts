import type { ZoneStatus } from "@/components/shared/Zone";
import type { DisplayMove } from "@/lib/api";

// Scenario display helpers: signed move with unit, origin styling, status mapping.

export function fmtMove(m: DisplayMove): string {
  const sign = m.value > 0 ? "+" : "";
  return `${sign}${m.value}${m.unit}`;
}

// Format a raw canonical (decimal) move by factor class: bp for rates/credit, % for equity/FX.
export function fmtRawMove(factor: string, move: number | null | undefined): string {
  if (move == null) return "—";
  if (factor.startsWith("ir:") || factor === "credit" || factor.startsWith("fxrate:")) {
    const bp = move * 1e4;
    return `${bp > 0 ? "+" : ""}${bp.toFixed(0)}bp`;
  }
  const pct = move * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

// A scaling multiplier for display: ×1.69 (two decimals), or "×—" when missing.
export function fmtMultiplier(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "×—";
  return `×${x.toFixed(2)}`;
}

export const ORIGIN_STYLE: Record<string, { bg: string; label: string }> = {
  driver: { bg: "bg-indigo-100 text-indigo-700", label: "driver" },
  overlay: { bg: "bg-amber-100 text-amber-700", label: "overlay" },
  derived: { bg: "bg-slate-100 text-slate-600", label: "derived" },
  macro: { bg: "bg-teal-100 text-teal-700", label: "macro" },
  arag: { bg: "bg-violet-100 text-violet-700", label: "arag" },
};

export function plausibilityZone(status: string | null | undefined): ZoneStatus {
  if (status === "pass") return "green";
  if (status === "flag") return "amber";
  if (status === "fail") return "red";
  return "unknown";
}

export function signoffZone(status: string | null | undefined): ZoneStatus {
  if (status === "approved") return "green";
  if (status === "rejected") return "red";
  return "unknown"; // pending
}

// Plausibility gate statuses as readers see them. Only a hard-bound "fail" makes a tier not
// plausible (it is held out); "flag" findings are notes on a plausible, promotable tier.
export function plausibilityLabel(status: string | null | undefined): string {
  if (status === "pass") return "plausible";
  if (status === "flag") return "plausible · notes";
  if (status === "fail") return "not plausible";
  return "unchecked";
}

export interface PlausibilityCounts {
  plausible: number; // pass or flag
  withNotes: number; // flag
  failed: number;
  total: number;
}

// Counts the tiers by gate status; an unchecked tier counts toward the total only.
export function plausibilityCounts(statuses: (string | null | undefined)[]): PlausibilityCounts {
  const n = (s: string) => statuses.filter((x) => x === s).length;
  const withNotes = n("flag");
  return { plausible: n("pass") + withNotes, withNotes, failed: n("fail"), total: statuses.length };
}

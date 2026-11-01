import type { ZoneStatus } from "@/components/shared/Zone";
import type { CoverageView } from "@/lib/api";

// Measurement page helpers (coverage, decision impact, event replay): number formats, the
// participation-ratio position against its target band, and labels for missing scenarios.

/** A share in [0, 1] as a percent with one decimal; "—" when absent. */
export function fmtShare(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/** A cosine with three decimals; "—" when absent (e.g. the nearest scenario was not scored). */
export function fmtCosine(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(3);
}

/** Signed cosine gain (enriched − authored) with three decimals; "—" when either cosine is absent. */
export function fmtGain(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(3)}`;
}

/** Text token for a gain: closer to the episode (positive) reads as a gain, further as a loss. */
export function gainTone(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v) || v === 0) return "text-fg-secondary";
  return v > 0 ? "text-success-fg" : "text-danger-fg";
}

/** Mean size multiplier ratio as "×0.964"; "—" when absent. */
export function fmtSizeRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `×${v.toFixed(3)}`;
}

/** Text token for a size ratio: smaller sizes (< 1) warn, larger sizes (> 1) inform, 1 is muted. */
export function sizeRatioTone(v: number | null | undefined, eps = 5e-4): string {
  if (v == null || !Number.isFinite(v) || Math.abs(v - 1) < eps) return "text-fg-muted";
  return v < 1 ? "text-warning-fg" : "text-info-fg";
}

export type TargetSide = "below" | "in" | "above" | "unknown";

export interface TargetPosition {
  side: TargetSide;
  lo: number | null;
  hi: number | null;
  /** Marker position of the value on a scale from 0 to max(hi, value) × 1.25, in percent (0–100). */
  valuePct: number | null;
  /** The target band on the same scale, in percent. */
  bandPct: { from: number; to: number } | null;
}

/** Where the participation ratio sits against its ``[lo, hi]`` target band (for the diversity meter). */
export function targetPosition(diversity: CoverageView["diversity"]): TargetPosition {
  const v = diversity.participation_ratio;
  const t = diversity.target;
  const lo = t && t.length >= 2 && Number.isFinite(t[0]) ? t[0] : null;
  const hi = t && t.length >= 2 && Number.isFinite(t[1]) ? t[1] : null;
  if (v == null || !Number.isFinite(v) || lo == null || hi == null) {
    return { side: "unknown", lo, hi, valuePct: null, bandPct: null };
  }
  const side: TargetSide = v < lo ? "below" : v > hi ? "above" : "in";
  const scale = Math.max(hi, v) * 1.25;
  const pct = (x: number) => (scale > 0 ? Math.min(100, Math.max(0, (x / scale) * 100)) : 0);
  return { side, lo, hi, valuePct: pct(v), bandPct: { from: pct(lo), to: pct(hi) } };
}

/** Diversity traffic light: in the target band green, outside amber, unknown grey. */
export function diversityZone(side: TargetSide): ZoneStatus {
  if (side === "in") return "green";
  if (side === "unknown") return "unknown";
  return "amber";
}

/** A ``missing`` entry (a name, or a ``{scenario, reason}`` record) as display text. */
export function missingLabel(m: CoverageView["missing"][number]): string {
  if (typeof m === "string") return m;
  const name = m.scenario ?? "?";
  return m.reason ? `${name} — ${m.reason}` : name;
}

import type { ScenarioWaterfall, WaterfallStep, WaterfallStepId } from "@/lib/api";
import type { ZoneStatus } from "@/components/shared/Zone";

// Pure helpers for the layer-ablation waterfall (step 15f) and the signal-health view — kept out of the
// components so they are unit-tested with vitest.

export const STEP_LABEL: Record<WaterfallStepId, string> = {
  macrorag: "+ MacroRAG",
  xbrlrag: "+ XbrlRag",
  events: "+ key events",
  market_intelligence: "+ market intelligence",
};

export type BarKind = "authored" | "step" | "final";
export type BarTone = "neutral" | "pos" | "neg" | "flat";

export interface WaterfallBar {
  key: string;
  label: string;
  kind: BarKind;
  /** Invisible stack base (the lower end of the floating bar). */
  base: number;
  /** Visible bar length (always >= 0). */
  height: number;
  /** Signed value shown in the label: the total for authored/final, the delta for a step. */
  value: number;
  tone: BarTone;
  /** Step bars only: floor hits mean the step's contribution is understated. */
  floorHits: string[];
  /** Step bars only: no layer of the step was in the run's order. */
  empty: boolean;
}

/** The authored bar, one floating bar per step (coloured by the sign of its delta) and the final bar. */
export function waterfallBars(w: ScenarioWaterfall): WaterfallBar[] {
  const total = (key: string, label: string, kind: BarKind, v: number, hits: string[]): WaterfallBar => ({
    key, label, kind, base: Math.min(0, v), height: Math.abs(v), value: v, tone: "neutral", floorHits: hits,
    empty: false,
  });
  const steps = w.steps.map((s: WaterfallStep): WaterfallBar => ({
    key: s.step,
    label: STEP_LABEL[s.step] ?? s.step,
    kind: "step",
    base: Math.min(s.pnl_before, s.pnl_after),
    height: Math.abs(s.delta),
    value: s.delta,
    tone: s.delta > 0 ? "pos" : s.delta < 0 ? "neg" : "flat",
    floorHits: s.floor_hits,
    empty: s.layers.length === 0,
  }));
  return [
    total("authored", "authored", "authored", w.authored_pnl, w.authored_floor_hits),
    ...steps,
    total("final", "final", "final", w.final_pnl, []),
  ];
}

/** Sum of the step deltas (equals final − authored by construction of the ladder). */
export function sumDeltas(w: ScenarioWaterfall): number {
  return w.steps.reduce((acc, s) => acc + s.delta, 0);
}

/** Cost per step: "not reported" when the run recorded none (None cost), else $ with 4 decimals. */
export function fmtStepCost(cost: number | null | undefined): string {
  if (cost == null) return "not reported";
  return `$${cost.toFixed(4)}`;
}

/** Contribution ratio (|Shapley| per second or per $): "—" when undefined. */
export function fmtRatio(v: number | null | undefined, unit: string): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const txt = abs >= 1e6 ? `${(v / 1e6).toFixed(2)}mm` : abs >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toFixed(2);
  return `$${txt}/${unit}`;
}

/** Signal-health status → traffic light (down red, degraded amber, ok green, unused grey). */
export function healthZone(status: string | null | undefined): ZoneStatus {
  if (status === "down") return "red";
  if (status === "degraded") return "amber";
  if (status === "ok") return "green";
  if (status === "unused") return "not_run";
  return "unknown";
}

/** Catalog-cell text for a tier that did not run. */
export function outcomeLabel(outcome: string | null | undefined): string {
  if (outcome === "held_out") return "held out";
  if (outcome === "stopped") return "stopped";
  return "not run";
}

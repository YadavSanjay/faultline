import { describe, it, expect } from "vitest";
import type { ScenarioWaterfall } from "@/lib/api";
import { fmtRatio, fmtStepCost, healthZone, outcomeLabel, sumDeltas, waterfallBars } from "./waterfall";

const W: ScenarioWaterfall = {
  scenario: "taiwan",
  severity: "severe",
  composition_rule: "dualrag-v3",
  authored_pnl: -10,
  final_pnl: -18.5,
  authored_floor_hits: ["AMD"],
  steps: [
    { step: "macrorag", layers: ["macrorag"], pnl_before: -10, pnl_after: -14, delta: -4, shapley: -4,
      floor_hits: ["TSM"], n_calls: 3, latency_s: 1.5, cost_usd: null, contribution_per_s: 4 / 1.5,
      contribution_per_usd: null },
    { step: "xbrlrag", layers: ["xbrlrag_narrative"], pnl_before: -14, pnl_after: -15, delta: -1, shapley: -1,
      floor_hits: [], n_calls: 0, latency_s: 0, cost_usd: 0.25, contribution_per_s: null, contribution_per_usd: 4 },
    { step: "market_intelligence", layers: ["crowding"], pnl_before: -15, pnl_after: -18.5, delta: -3.5,
      shapley: -3, floor_hits: [], n_calls: null, latency_s: null, cost_usd: null, contribution_per_s: null,
      contribution_per_usd: null },
    { step: "events", layers: [], pnl_before: -18.5, pnl_after: -18.5, delta: 0, shapley: 0.5, floor_hits: [],
      n_calls: null, latency_s: null, cost_usd: null, contribution_per_s: null, contribution_per_usd: null },
  ],
};

describe("waterfallBars", () => {
  it("frames the steps with the authored and final totals", () => {
    const bars = waterfallBars(W);
    expect(bars.map((b) => b.key)).toEqual(["authored", "macrorag", "xbrlrag", "market_intelligence", "events", "final"]);
    expect(bars[0]).toMatchObject({ kind: "authored", base: -10, height: 10, value: -10, floorHits: ["AMD"] });
    expect(bars[5]).toMatchObject({ kind: "final", base: -18.5, height: 18.5, value: -18.5 });
  });

  it("floats each step between its before and after P&L, coloured by the delta sign", () => {
    const [, macro, , mi, events] = waterfallBars(W);
    expect(macro).toMatchObject({ base: -14, height: 4, value: -4, tone: "neg", floorHits: ["TSM"], empty: false });
    expect(mi).toMatchObject({ base: -18.5, height: 3.5, tone: "neg" });
    expect(events).toMatchObject({ height: 0, tone: "flat", empty: true });
  });

  it("marks a positive delta and a positive total", () => {
    const up: ScenarioWaterfall = { ...W, authored_pnl: 2, final_pnl: 5, steps: [
      { ...W.steps[0], pnl_before: 2, pnl_after: 5, delta: 3 }] };
    const bars = waterfallBars(up);
    expect(bars[0]).toMatchObject({ base: 0, height: 2 });
    expect(bars[1]).toMatchObject({ base: 2, height: 3, tone: "pos" });
  });

  it("step deltas telescope to final minus authored", () => {
    expect(sumDeltas(W)).toBeCloseTo(W.final_pnl - W.authored_pnl, 12);
  });
});

describe("formatters", () => {
  it("says 'not reported' for a None cost", () => {
    expect(fmtStepCost(null)).toBe("not reported");
    expect(fmtStepCost(0.125)).toBe("$0.1250");
  });

  it("formats ratios and hides undefined ones", () => {
    expect(fmtRatio(null, "s")).toBe("—");
    expect(fmtRatio(2500, "s")).toBe("$2.5k/s");
    expect(fmtRatio(4, "$")).toBe("$4.00/$");
  });

  it("maps source status and tier outcome", () => {
    expect(healthZone("down")).toBe("red");
    expect(healthZone("degraded")).toBe("amber");
    expect(healthZone("ok")).toBe("green");
    expect(healthZone("unused")).toBe("not_run");
    expect(healthZone(null)).toBe("unknown");
    expect(outcomeLabel("held_out")).toBe("held out");
    expect(outcomeLabel("stopped")).toBe("stopped");
    expect(outcomeLabel("not_run")).toBe("not run");
  });
});

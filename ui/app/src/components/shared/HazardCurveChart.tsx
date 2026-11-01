import type { EChartsOption } from "echarts";
import type { HazardCurvePoint } from "@/lib/api";
import { EChart } from "./EChart";

// HazardCurveChart (Pricing lineage, single-name CDS): the bootstrapped issuer credit
// term structure. Left axis (bp): the market par CDS spread and the piecewise-flat forward
// hazard solved for each tenor. Right axis (0–1): the survival probability to each tenor.
// Shows visually why a steep curve (low near-term, high forward hazard) differs from a
// single flat hazard.

export function HazardCurveChart({ points, height = 300 }: { points: HazardCurvePoint[]; height?: number }) {
  const option: EChartsOption = {
    grid: { left: 52, right: 56, top: 36, bottom: 28 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: ["par spread", "forward hazard", "survival prob"] },
    xAxis: { type: "category", data: points.map((p) => p.tenor), axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: "value", name: "bp", scale: true, axisLabel: { fontSize: 11 } },
      { type: "value", name: "survival", min: 0, max: 1, axisLabel: { fontSize: 11 } },
    ],
    series: [
      { name: "par spread", type: "line", smooth: false, symbolSize: 7,
        data: points.map((p) => p.par_spread_bp) },
      { name: "forward hazard", type: "line", smooth: false, symbolSize: 7,
        data: points.map((p) => p.forward_hazard_bp), lineStyle: { type: "dashed" } },
      { name: "survival prob", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 6,
        data: points.map((p) => +p.survival_prob.toFixed(5)) },
    ],
  };
  return <EChart option={option} height={height} />;
}

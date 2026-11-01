import type { EChartsOption } from "echarts";
import type { CurvePoints } from "@/lib/api";
import { EChart } from "./EChart";

// CurveChart (shared by Pricing lineage and Market Data): par / zero / forward rates on the
// left axis (%), discount factor on the right axis (0–1), by tenor. Toggling series teaches
// the par → bootstrap → zero → DF → forward relationship.

const PCT = (v: number | null) => (v == null ? null : +(v * 100).toFixed(4));

export function CurveChart({ curve, height = 300 }: { curve: CurvePoints; height?: number }) {
  const option: EChartsOption = {
    grid: { left: 48, right: 52, top: 36, bottom: 28 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: ["par", "zero", "forward", "discount factor"] },
    xAxis: { type: "category", data: curve.tenors, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: "value", name: "rate %", scale: true, axisLabel: { fontSize: 11, formatter: "{value}%" } },
      { type: "value", name: "DF", min: 0, max: 1, axisLabel: { fontSize: 11 } },
    ],
    series: [
      { name: "par", type: "line", smooth: true, data: curve.par_rates.map(PCT), symbolSize: 6 },
      { name: "zero", type: "line", smooth: true, data: curve.zero_rates.map(PCT), symbolSize: 6 },
      { name: "forward", type: "line", smooth: true, data: curve.forward_rates.map(PCT), symbolSize: 6 },
      {
        name: "discount factor",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        data: curve.discount_factors.map((v) => (v == null ? null : +v.toFixed(5))),
        symbolSize: 6,
        lineStyle: { type: "dashed" },
      },
    ],
  };
  return <EChart option={option} height={height} />;
}

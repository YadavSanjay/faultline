import type { EChartsOption } from "echarts";
import type { CreditCurvePoint } from "@/lib/api";
import { EChart } from "./EChart";

// SpreadCurveChart (Pricing lineage, corporate bond): the issuer credit curve — the
// Z-spread over SOFR at each quoted tenor. An upward slope means near-term cashflows
// carry less credit risk than long ones; the bond discounts each cashflow on this curve
// (flat-extrapolated past the last quoted tenor) rather than one flat 5Y spread.

export function SpreadCurveChart({ points, height = 260 }: { points: CreditCurvePoint[]; height?: number }) {
  const option: EChartsOption = {
    grid: { left: 52, right: 24, top: 24, bottom: 28 },
    tooltip: { trigger: "axis", valueFormatter: (v) => `${v} bp` },
    xAxis: { type: "category", data: points.map((p) => p.tenor), axisLabel: { fontSize: 11 } },
    yAxis: { type: "value", name: "Z-spread (bp)", scale: true, axisLabel: { fontSize: 11 } },
    series: [
      { name: "Z-spread", type: "line", smooth: false, symbolSize: 7, areaStyle: { opacity: 0.08 },
        data: points.map((p) => p.z_spread_bp) },
    ],
  };
  return <EChart option={option} height={height} />;
}

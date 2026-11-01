import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "./EChart";
import { useTheme } from "@/lib/theme";
import { chartColors, divergingScale } from "@/lib/chartTheme";

// Correlation heatmap (SC-B2): factors × factors, colored on [-1, 1]. Driver factors are
// emphasized in the axis labels so the propagation is legible.

export function Heatmap({ labels, matrix, highlight = [], height = 420 }: {
  labels: string[];
  matrix: number[][];
  highlight?: string[];
  height?: number;
}) {
  const hi = useMemo(() => new Set(highlight), [highlight]);
  const { theme } = useTheme();
  const c = chartColors(theme);

  const option = useMemo<EChartsOption>(() => {
    const data: [number, number, number][] = [];
    for (let i = 0; i < matrix.length; i++) {
      for (let j = 0; j < matrix[i].length; j++) {
        data.push([j, i, matrix[i][j]]);
      }
    }
    const axisLabel = {
      fontSize: 9,
      formatter: (v: string | number) => (hi.has(String(v)) ? `▸ ${v}` : String(v)),
      color: (v?: string | number) => (hi.has(String(v)) ? c.ink : c.neutral),
    };
    return {
      grid: { left: 110, right: 16, top: 16, bottom: 90 },
      tooltip: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (p: any) => {
          const v = p.value as [number, number, number];
          return `${labels[v[1]]} · ${labels[v[0]]}<br/>ρ = ${v[2].toFixed(3)}`;
        },
      },
      xAxis: { type: "category", data: labels, axisLabel: { ...axisLabel, rotate: 60 } },
      yAxis: { type: "category", data: labels, axisLabel },
      visualMap: {
        min: -1,
        max: 1,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 6,
        itemHeight: 80,
        inRange: { color: divergingScale(theme) },
        textStyle: { fontSize: 10, color: c.neutral },
      },
      series: [{ type: "heatmap", data, progressive: 0, itemStyle: { borderColor: c.surface, borderWidth: 0.5 } }],
    };
  }, [labels, matrix, hi, theme, c]);

  return <EChart option={option} height={height} />;
}

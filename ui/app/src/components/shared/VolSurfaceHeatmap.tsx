import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import type { VolSurface } from "@/lib/api";
import { EChart } from "./EChart";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";

// VolSurfaceHeatmap (Pricing lineage, equity options): the chain's implied-vol surface as
// strike (y) x expiry (x), coloured by IV%. Reads down a column to see the put skew (lower
// strikes richer), across a row to see the term structure. The trade's legs are marked with
// diamonds so you can see exactly which smile point each leg is priced off.

export function VolSurfaceHeatmap({ surface, height = 380 }: { surface: VolSurface; height?: number }) {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const option = useMemo<EChartsOption>(() => {
    const xLabels = surface.expiries_days.map((d) => `${d}d`);
    const yLabels = surface.strikes.map((k) => String(k));
    const data: [number, number, number][] = [];
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 0; i < surface.strikes.length; i++) {
      for (let j = 0; j < surface.expiries_days.length; j++) {
        const v = surface.iv_pct[i][j];
        data.push([j, i, v]);
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
    const nearest = (arr: number[], v: number) =>
      arr.reduce((best, x, k) => (Math.abs(x - v) < Math.abs(arr[best] - v) ? k : best), 0);
    const legs = surface.leg_points.map((p) => [
      nearest(surface.expiries_days, p.expiry_days),
      nearest(surface.strikes, p.strike),
    ]);
    return {
      grid: { left: 56, right: 16, top: 16, bottom: 74 },
      tooltip: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (p: any) => {
          const v = p.value as [number, number, number];
          return `strike ${yLabels[v[1]]} · ${xLabels[v[0]]}<br/>IV = ${v[2].toFixed(1)}%`;
        },
      },
      xAxis: { type: "category", data: xLabels, name: "expiry", nameLocation: "middle", nameGap: 26,
        axisLabel: { fontSize: 10 } },
      yAxis: { type: "category", data: yLabels, name: "strike", axisLabel: { fontSize: 10 } },
      visualMap: {
        min: Math.floor(lo), max: Math.ceil(hi), calculable: true, orient: "horizontal",
        left: "center", bottom: 4, itemHeight: 80, text: ["IV%", ""],
        inRange: { color: [c.info, c.surface, c.neg] }, textStyle: { fontSize: 10, color: c.neutral },
      },
      series: [
        { type: "heatmap", data, progressive: 0, itemStyle: { borderColor: c.surface, borderWidth: 0.5 } },
        { type: "scatter", data: legs, symbol: "diamond", symbolSize: 11,
          itemStyle: { color: "transparent", borderColor: c.ink, borderWidth: 1.5 },
          tooltip: { show: false }, z: 5 },
      ],
    };
  }, [surface, theme, c]);

  return <EChart option={option} height={height} />;
}

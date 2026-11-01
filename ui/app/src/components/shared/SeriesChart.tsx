import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "./EChart";

// Generic time-series line chart: date on x (or row index if no date column), one line per
// selected numeric column. Shared by the Market Data viewer and PV history.

export function SeriesChart({ rows, dateCol, columns, height = 320 }: {
  rows: Record<string, unknown>[];
  dateCol: string | null;
  columns: string[];
  height?: number;
}) {
  const option = useMemo<EChartsOption>(() => {
    const x = rows.map((r, i) => (dateCol ? String(r[dateCol]) : String(i)));
    return {
      grid: { left: 56, right: 20, top: 30, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { top: 0, data: columns },
      xAxis: { type: "category", data: x, axisLabel: { fontSize: 10, hideOverlap: true } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10 } },
      series: columns.map((col) => ({
        name: col,
        type: "line",
        showSymbol: false,
        smooth: true,
        data: rows.map((r) => {
          const v = r[col];
          return typeof v === "number" ? v : v == null ? null : Number(v);
        }),
      })),
    };
  }, [rows, dateCol, columns]);

  return <EChart option={option} height={height} />;
}

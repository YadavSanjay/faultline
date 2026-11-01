import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/shared/EChart";
import type { RndCurve } from "@/lib/api";

// The Breeden-Litzenberger risk-neutral density: the option market's implied probability
// distribution of the underlier at the front expiry. The pdf is drawn as an area over the
// strike grid, with the spot marked and the ±expected-move band shaded — so a desk can read
// off where the market puts the tail mass a scenario should be graded against.

export function RndChart({ curve, height = 320 }: { curve: RndCurve; height?: number }) {
  const option = useMemo<EChartsOption>(() => {
    const data = curve.strike.map((k, i) => [k, curve.pdf[i]]);
    const spot = curve.rnd_spot ?? null;
    const em = curve.expected_move_front ?? null;
    const marks: { xAxis: number; name?: string; lineStyle?: object; label?: object }[] = [];
    if (spot != null) marks.push({ xAxis: spot, name: "spot" });
    if (spot != null && em != null) {
      marks.push({ xAxis: spot * (1 - em) });
      marks.push({ xAxis: spot * (1 + em) });
    }
    return {
      grid: { left: 48, right: 20, top: 24, bottom: 30 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v) => (typeof v === "number" ? v.toExponential(2) : String(v)),
      },
      xAxis: { type: "value", scale: true, name: "strike", nameGap: 22, nameLocation: "middle",
               axisLabel: { fontSize: 10 } },
      yAxis: { type: "value", name: "density", axisLabel: { show: false }, splitLine: { show: false } },
      series: [
        {
          name: "RND",
          type: "line",
          data,
          showSymbol: false,
          smooth: true,
          areaStyle: { opacity: 0.18 },
          lineStyle: { width: 2 },
          markLine: marks.length
            ? {
                symbol: "none",
                data: marks,
                label: { fontSize: 10, formatter: (p: { name?: string }) => p.name ?? "" },
                lineStyle: { type: "dashed" },
              }
            : undefined,
        },
      ],
    };
  }, [curve]);
  return <EChart option={option} height={height} />;
}

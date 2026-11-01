import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { useTheme } from "@/lib/theme";
import { chartTheme } from "@/lib/chartTheme";

// Minimal ECharts wrapper: init on mount (with the token-derived theme so axes,
// labels, legend and tooltip follow light/dark), update option on change, resize
// with the container, dispose on unmount. Re-inits when the app theme flips.

export function EChart({ option, height = 280 }: { option: echarts.EChartsOption; height?: number }) {
  const { theme } = useTheme();
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    echarts.registerTheme("qlp", chartTheme(theme));
    // SVG renderer → crisp vector text/labels at any DPI or browser zoom (canvas
    // rasterizes and blurs small labels on HiDPI displays).
    const chart = echarts.init(ref.current, "qlp", { renderer: "svg" });
    chartRef.current = chart;
    chart.setOption(option, true);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} style={{ height }} />;
}

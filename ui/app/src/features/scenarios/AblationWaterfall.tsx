import type { EChartsOption } from "echarts";
import type { ScenarioWaterfall } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EChart } from "@/components/shared/EChart";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { humanizeUsd, exactUsd, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/cn";
import { STEP_LABEL, fmtRatio, fmtStepCost, waterfallBars } from "./waterfall";

// One scenario tier's layer-ablation waterfall (design §7.1): authored → + MacroRAG → + XbrlRag →
// + key events → + market intelligence → final, with the grouped (order-free) Shapley bar per step
// beside it and calls / latency / cost per step. Bars are the order-dependent ladder.

export const READING_RULE =
  "Measures influence, not value: a bar is how far the layer moved this scenario's P&L, not whether it made the scenario better.";

function WaterfallChart({ w }: { w: ScenarioWaterfall }) {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const bars = waterfallBars(w);
  const color = (tone: string) => (tone === "pos" ? c.pos : tone === "neg" ? c.neg : c.neutral);
  const option: EChartsOption = {
    grid: { left: 70, right: 16, top: 24, bottom: 48 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0]?.dataIndex ?? 0;
        const b = bars[i];
        if (!b) return "";
        const head = b.kind === "step" ? `${b.label}<br/>Δ ${exactUsd(b.value)}` : `${b.label}<br/>${exactUsd(b.value)}`;
        const hits = b.floorHits.length ? `<br/>floor hits: ${b.floorHits.join(", ")} (contribution understated)` : "";
        return `${head}${b.empty ? "<br/>layer not in this run" : ""}${hits}`;
      },
    },
    xAxis: {
      type: "category",
      data: bars.map((b) => (b.floorHits.length ? `${b.label} ⚑` : b.label)),
      axisLabel: { fontSize: 10, interval: 0, rotate: 20 },
    },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (v: number) => humanizeUsd(v) } },
    series: [
      {
        name: "base",
        type: "bar",
        stack: "wf",
        stackStrategy: "all",
        silent: true,
        itemStyle: { color: "transparent" },
        data: bars.map((b) => b.base),
      },
      {
        name: "P&L",
        type: "bar",
        stack: "wf",
        stackStrategy: "all",
        barMaxWidth: 44,
        label: {
          show: true,
          position: "top",
          fontSize: 10,
          formatter: (p: unknown) => {
            const b = bars[(p as { dataIndex: number }).dataIndex];
            if (!b) return "";
            return b.kind === "step" ? `${b.value > 0 ? "+" : ""}${humanizeUsd(b.value)}` : humanizeUsd(b.value);
          },
        },
        data: bars.map((b) => ({
          value: b.height,
          itemStyle: {
            color: b.kind === "step" ? color(b.tone) : c.info,
            opacity: b.empty ? 0.35 : 1,
            borderColor: b.floorHits.length ? c.warning : undefined,
            borderWidth: b.floorHits.length ? 2 : 0,
          },
        })),
      },
    ],
  };
  return <EChart option={option} height={300} />;
}

function ShapleyChart({ w }: { w: ScenarioWaterfall }) {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const steps = [...w.steps].reverse(); // top-to-bottom in waterfall order
  const option: EChartsOption = {
    // The card is narrow: few ticks, overlapping tick labels hidden, and room on the right for bar labels.
    grid: { left: 130, right: 72, top: 24, bottom: 32 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => exactUsd(Number(v)) },
    xAxis: {
      type: "value",
      splitNumber: 3,
      axisLabel: { fontSize: 10, hideOverlap: true, formatter: (v: number) => humanizeUsd(v) },
    },
    yAxis: { type: "category", data: steps.map((s) => STEP_LABEL[s.step] ?? s.step), axisLabel: { fontSize: 10 } },
    series: [
      {
        name: "Shapley",
        type: "bar",
        barMaxWidth: 22,
        data: steps.map((s) => ({ value: s.shapley, itemStyle: { color: s.shapley >= 0 ? c.pos : c.neg } })),
        label: { show: true, position: "right", fontSize: 10, formatter: (p: unknown) => humanizeUsd((p as { value: number }).value) },
      },
    ],
  };
  return <EChart option={option} height={300} />;
}

export function AblationWaterfall({ w }: { w: ScenarioWaterfall }) {
  const anyCost = w.steps.some((s) => s.n_calls != null);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader
            title="Ladder waterfall"
            subtitle={`authored ${humanizeUsd(w.authored_pnl)} → final ${humanizeUsd(w.final_pnl)} · ${w.composition_rule} · bars depend on the layer order`}
          />
          <CardBody>
            <WaterfallChart w={w} />
          </CardBody>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Shapley per step" subtitle="order-free share of final − authored (sums to it)" />
          <CardBody>
            <ShapleyChart w={w} />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Steps" subtitle="P&L moved, Shapley share and what each step cost to produce" />
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
              <tr>
                <th className="px-4 py-2 font-medium">Step</th>
                <th className="px-4 py-2 font-medium">Layers</th>
                <th className="px-4 py-2 text-right font-medium">Δ P&amp;L</th>
                <th className="px-4 py-2 text-right font-medium">Shapley</th>
                {anyCost && (
                  <>
                    <th className="px-4 py-2 text-right font-medium">Calls</th>
                    <th className="px-4 py-2 text-right font-medium">Latency</th>
                    <th className="px-4 py-2 text-right font-medium">Cost</th>
                    <th className="px-4 py-2 text-right font-medium">|Shapley| / s</th>
                    <th className="px-4 py-2 text-right font-medium">|Shapley| / $</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {w.steps.map((s) => (
                <tr key={s.step} className={cn("hover:bg-surface-2", s.layers.length === 0 && "text-fg-muted")}>
                  <td className="px-4 py-1.5">
                    <span className="font-medium text-fg">{STEP_LABEL[s.step] ?? s.step}</span>
                    {s.floor_hits.length > 0 && (
                      <span
                        className="ml-2 rounded bg-warning-tint px-1 py-0.5 text-nano font-semibold text-warning-fg"
                        title={`floor hits: ${s.floor_hits.join(", ")}`}
                      >
                        ⚑ contribution understated ({s.floor_hits.length})
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-1.5 font-mono text-xs text-fg-secondary">
                    {s.layers.length ? s.layers.join(", ") : "not in this run"}
                  </td>
                  <td className={cn("px-4 py-1.5 text-right tnum", s.delta > 0 ? "text-success-fg" : s.delta < 0 ? "text-danger-fg" : "text-fg-muted")} title={exactUsd(s.delta)}>
                    {s.delta > 0 ? "+" : ""}{humanizeUsd(s.delta)}
                  </td>
                  <td className="px-4 py-1.5 text-right tnum text-fg" title={exactUsd(s.shapley)}>{humanizeUsd(s.shapley)}</td>
                  {anyCost && (
                    <>
                      <td className="px-4 py-1.5 text-right tnum text-xs">{s.n_calls ?? "—"}</td>
                      <td className="px-4 py-1.5 text-right tnum text-xs">{fmtDuration(s.latency_s)}</td>
                      <td className={cn("px-4 py-1.5 text-right tnum text-xs", s.cost_usd == null && "text-fg-muted")}>{fmtStepCost(s.cost_usd)}</td>
                      <td className="px-4 py-1.5 text-right tnum text-xs">{fmtRatio(s.contribution_per_s, "s")}</td>
                      <td className="px-4 py-1.5 text-right tnum text-xs">{fmtRatio(s.contribution_per_usd, "$")}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <CardBody className="space-y-1 border-t border-subtle text-micro text-fg-muted">
          <div>{READING_RULE}</div>
          {w.authored_floor_hits.length > 0 && (
            <div>Authored scenario already hits the floor for {w.authored_floor_hits.join(", ")}.</div>
          )}
          {!anyCost && <div>This run recorded no per-layer cost (before step 15e).</div>}
        </CardBody>
      </Card>
    </div>
  );
}

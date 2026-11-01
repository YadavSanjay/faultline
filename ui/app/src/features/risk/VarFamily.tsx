import { useState } from "react";
import type { EChartsOption } from "echarts";
import { useRiskReport, type VarBlock, type FullRevalVar } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { EChart } from "@/components/shared/EChart";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { classLabel } from "./format";
import { cn } from "@/lib/cn";

// RK VaR Family: the five VaR estimates the engine produces — sensitivity historical-sim,
// filtered HS, stressed, parametric, full-reval — side by side, plus the component-ES /
// diversification decomposition. Surfaces blocks that were computed but never shown.

const LEVELS = ["0.95", "0.975", "0.99"];

interface MethodRow {
  name: string;
  detail: string;
  var: Record<string, number>;
  es: Record<string, number>;
}

function buildMethods(v: VarBlock, fr: FullRevalVar | null | undefined): MethodRow[] {
  const rows: MethodRow[] = [
    { name: "Sensitivity HS", detail: `${v.n_scenarios} scenarios · ${v.lookback_days}d`, var: v.var, es: v.es },
  ];
  if (v.fhs) rows.push({ name: "Filtered HS", detail: `EWMA λ=${v.fhs.lambda}`, var: v.fhs.var, es: v.fhs.es });
  if (v.stressed)
    rows.push({
      name: "Stressed",
      detail: `${v.stressed.window_start} → ${v.stressed.window_end}`,
      var: v.stressed.var,
      es: v.stressed.es,
    });
  if (v.parametric)
    rows.push({ name: "Parametric", detail: v.parametric.method, var: v.parametric.var, es: v.parametric.es });
  if (fr) rows.push({ name: "Full-reval", detail: `${fr.n_scenarios} scenarios`, var: fr.var, es: fr.es });
  return rows;
}

export function VarFamily() {
  const query = useRiskReport();
  const [level, setLevel] = useState("0.99");
  const { theme } = useTheme();
  const c = chartColors(theme);

  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(report) => {
        const v = report.var;
        if (!v) {
          return (
            <WarningBanner tone="info" title="No VaR in this run">
              The sensitivity VaR block wasn't produced for this run.
            </WarningBanner>
          );
        }
        const methods = buildMethods(v, report.full_reval_var);
        const levels = LEVELS.filter((l) => l in v.var);
        // most conservative VaR per level (for highlighting)
        const maxByLevel: Record<string, number> = {};
        for (const l of levels) maxByLevel[l] = Math.max(...methods.map((m) => m.var[l] ?? 0));

        const chart: EChartsOption = {
          grid: { left: 56, right: 16, top: 30, bottom: 24 },
          tooltip: { trigger: "axis", valueFormatter: (x) => humanizeUsd(x as number) },
          legend: { top: 0, data: [`VaR ${level}`, `ES ${level}`], textStyle: { fontSize: 11 } },
          xAxis: { type: "category", data: methods.map((m) => m.name), axisLabel: { fontSize: 10, interval: 0 } },
          yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (x: number) => humanizeUsd(x) } },
          series: [
            { name: `VaR ${level}`, type: "bar", data: methods.map((m) => m.var[level] ?? null), itemStyle: { color: c.info }, barMaxWidth: 28 },
            { name: `ES ${level}`, type: "bar", data: methods.map((m) => m.es[level] ?? null), itemStyle: { color: c.neg }, barMaxWidth: 28 },
          ],
        };

        const dec = v.decomposition;
        const decLevel = dec?.by_level[level];
        const maxComp = decLevel ? Math.max(1, ...decLevel.components.map((c) => Math.abs(c.component_es))) : 1;

        return (
          <div className="space-y-5">
            {/* level selector */}
            <div className="flex items-center gap-2 text-xs">
              <span className="text-fg-secondary">Confidence</span>
              {levels.map((l) => (
                <button
                  key={l}
                  type="button"
                  onClick={() => setLevel(l)}
                  className={cn(
                    "rounded px-2 py-0.5 font-medium",
                    level === l ? "bg-primary text-primary-fg" : "text-fg-secondary hover:bg-surface-0",
                  )}
                >
                  {(Number(l) * 100).toFixed(1)}%
                </button>
              ))}
            </div>

            {/* comparison table */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                VaR family — one-day loss estimates
              </div>
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-4 py-2 font-medium">Method</th>
                      {levels.map((l) => (
                        <th key={l} className="px-4 py-2 text-right font-medium">VaR {(Number(l) * 100).toFixed(1)}%</th>
                      ))}
                      <th className="px-4 py-2 text-right font-medium">ES {(Number(level) * 100).toFixed(1)}%</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {methods.map((m) => (
                      <tr key={m.name} className="hover:bg-surface-2">
                        <td className="px-4 py-2">
                          <div className="font-medium text-fg">{m.name}</div>
                          <div className="text-micro text-fg-muted">{m.detail}</div>
                        </td>
                        {levels.map((l) => {
                          const val = m.var[l];
                          const isMax = val != null && val === maxByLevel[l];
                          return (
                            <td
                              key={l}
                              className={cn("px-4 py-2 text-right tnum", isMax ? "font-semibold text-danger-fg" : "text-fg-secondary")}
                              title={val != null ? `${exactUsd(val)}${isMax ? " · most conservative" : ""}` : undefined}
                            >
                              {humanizeUsd(val)}
                            </td>
                          );
                        })}
                        <td className="px-4 py-2 text-right tnum text-fg-secondary" title={m.es[level] != null ? exactUsd(m.es[level]) : undefined}>
                          {humanizeUsd(m.es[level])}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="px-4 py-2 text-micro text-fg-muted">
                Bold = most conservative VaR at that level. Filtered HS scales history by recent (EWMA) vol; Stressed
                re-runs over a crisis window; Parametric assumes normal P&L; Full-reval reprices every trade per scenario.
              </div>
            </Card>

            {/* comparison chart */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                VaR vs ES at {(Number(level) * 100).toFixed(1)}%
              </div>
              <CardBody>
                <EChart option={chart} height={240} />
              </CardBody>
            </Card>

            {/* decomposition */}
            {decLevel && (
              <Card>
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-2">
                  <h3 className="text-sm font-semibold text-fg">Risk allocation — component ES</h3>
                  <div className="text-xs text-fg-secondary">
                    diversified ES <span className="tnum font-medium text-fg" title={exactUsd(decLevel.diversified_es)}>{humanizeUsd(decLevel.diversified_es)}</span>
                    <span className="mx-1 text-fg-muted">·</span>
                    Σ standalone <span className="tnum" title={exactUsd(decLevel.sum_standalone_var)}>{humanizeUsd(decLevel.sum_standalone_var)}</span>
                    <span className="mx-1 text-fg-muted">·</span>
                    benefit <span className="tnum font-medium text-success-fg" title={exactUsd(decLevel.diversification_benefit)}>{humanizeUsd(decLevel.diversification_benefit)}</span>
                  </div>
                </div>
                <CardBody className="space-y-2">
                  {decLevel.components.map((c) => {
                    const neg = c.component_es < 0;
                    return (
                      <div key={c.class} className="flex items-center gap-3">
                        <div className="w-24 shrink-0 truncate text-xs text-fg-secondary">{classLabel(c.class)}</div>
                        <div className="relative h-3 flex-1 overflow-hidden rounded bg-surface-0">
                          <div
                            className={cn("h-full rounded", neg ? "bg-info" : "bg-success")}
                            style={{ width: `${(Math.abs(c.component_es) / maxComp) * 100}%` }}
                          />
                        </div>
                        <div className={cn("tnum w-20 shrink-0 text-right text-xs", neg ? "text-info-fg" : "text-fg")} title={exactUsd(c.component_es)}>
                          {humanizeUsd(c.component_es)}
                        </div>
                        <div className="w-12 shrink-0 text-right text-micro text-fg-muted">
                          {(c.es_share * 100).toFixed(0)}%
                        </div>
                      </div>
                    );
                  })}
                  <div className="pt-1 text-micro text-fg-muted">
                    Euler component ES — each class's marginal contribution to the book's diversified ES (sums to it).
                    Blue bars are net diversifiers (negative contribution). Diversification benefit = Σ standalone − diversified.
                  </div>
                </CardBody>
              </Card>
            )}
          </div>
        );
      }}
    </QueryState>
  );
}

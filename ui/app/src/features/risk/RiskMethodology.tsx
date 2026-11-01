import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { useRiskReport, useVarConstruction, useLineage, type VarConstruction } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { EChart } from "@/components/shared/EChart";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { factorCaption, lossText } from "./format";
import { cn } from "@/lib/cn";

// RK-B3a — sensitivity bump-and-revalue, reusing the lineage explain (base/+bump/-bump).
function SensitivityLineage() {
  const report = useRiskReport();
  // V1 exposes bump-and-revalue lineage for swaps + cash equity; default to the 10Y swap.
  const [tradeId, setTradeId] = useState("TRADE_22");
  const lineage = useLineage(tradeId);
  const candidates = (report.data?.sensitivities ?? []).map((t) => t.trade_id);

  return (
    <Card>
      <CardHeader
        title="Sensitivity lineage — bump-and-revalue"
        subtitle="a sensitivity is (PV(+bump) − PV(−bump)) / 2 from the same pricer"
        right={
          <select className="rounded border border-strong px-2 py-1 text-xs" value={tradeId} onChange={(e) => setTradeId(e.target.value)}>
            {candidates.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        }
      />
      <CardBody>
        {lineage.isLoading && <div className="text-sm text-fg-muted">loading…</div>}
        {lineage.data && !(lineage.data.sensitivity_lineage?.length) && (
          <WarningBanner tone="info" title="No bump-and-revalue lineage for this trade">
            The sensitivity couldn't be reconstructed for this trade — open its full Calculation Lineage instead.
          </WarningBanner>
        )}
        <div className="space-y-3">
          {(lineage.data?.sensitivity_lineage ?? []).map((s) => {
            const reconc = Math.abs((s.up_pv - s.down_pv) / 2 - s.value) < 1;
            return (
              <div key={s.factor} className="rounded-md border border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-mono text-sm text-fg">{s.factor}</span>
                  <span className="text-xs text-fg-muted">{s.bump_unit} · {s.method}</span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  <div>
                    <div className="text-micro uppercase text-fg-muted">base PV</div>
                    <div className="tnum font-medium text-fg" title={exactUsd(s.base_pv)}>{humanizeUsd(s.base_pv)}</div>
                  </div>
                  <div>
                    <div className="text-micro uppercase text-fg-muted">+bump PV</div>
                    <div className="tnum text-fg" title={exactUsd(s.up_pv)}>{humanizeUsd(s.up_pv)}</div>
                  </div>
                  <div>
                    <div className="text-micro uppercase text-fg-muted">−bump PV</div>
                    <div className="tnum text-fg" title={exactUsd(s.down_pv)}>{humanizeUsd(s.down_pv)}</div>
                  </div>
                  <div>
                    <div className="text-micro uppercase text-fg-muted">value (central diff)</div>
                    <div className={cn("tnum font-semibold", s.value >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(s.value)}>
                      {humanizeUsd(s.value)} {reconc && <span className="text-success-fg">✓</span>}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
}

// RK-B3b — VaR construction histogram from the persisted daily P&L vector.
function histogram(pnl: number[], bins = 31) {
  if (pnl.length === 0) return { centers: [] as number[], counts: [] as number[], width: 0 };
  const min = Math.min(...pnl);
  const max = Math.max(...pnl);
  const width = (max - min) / bins || 1;
  const counts = new Array(bins).fill(0) as number[];
  for (const x of pnl) {
    const i = Math.min(bins - 1, Math.max(0, Math.floor((x - min) / width)));
    counts[i] += 1;
  }
  const centers = counts.map((_, i) => min + (i + 0.5) * width);
  return { centers, counts, width };
}

function VarHistogram({ vc }: { vc: VarConstruction }) {
  const { theme } = useTheme();
  const col = chartColors(theme);
  const { centers, counts } = useMemo(() => histogram(vc.pnl_vector), [vc.pnl_vector]);
  const var99 = vc.var["0.99"];
  const es99 = vc.es["0.99"];

  const nearest = (target: number) => {
    let best = 0;
    let bd = Infinity;
    centers.forEach((c, i) => {
      const d = Math.abs(c - target);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    return best;
  };

  const option: EChartsOption = {
    grid: { left: 40, right: 20, top: 30, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: centers.map((c) => humanizeUsd(c)),
      axisLabel: { fontSize: 10, interval: 5 },
    },
    yAxis: { type: "value", name: "days", axisLabel: { fontSize: 10 } },
    series: [
      {
        type: "bar",
        data: counts,
        itemStyle: { color: col.neutral },
        markLine: {
          symbol: "none",
          label: { fontSize: 10, position: "insideEndTop" },
          data: [
            { xAxis: nearest(-var99), name: "VaR 99%", lineStyle: { color: col.neg } },
            { xAxis: nearest(-es99), name: "ES 99%", lineStyle: { color: col.accent, type: "dashed" } },
          ],
        },
      },
    ],
  };

  return (
    <Card>
      <CardHeader
        title="VaR construction — historical P&L distribution"
        subtitle={`${vc.n_scenarios} historical days · book sensitivity × factor moves · VaR is the loss quantile, ES the mean tail beyond it`}
      />
      <CardBody>
        <EChart option={option} height={300} />
        <div className="mt-2 flex gap-4 text-xs">
          <span><span className="text-danger-fg">VaR 99%</span> <span className="tnum font-medium">{lossText(var99)}</span></span>
          <span><span className="text-danger-fg">ES 99%</span> <span className="tnum font-medium">{lossText(es99)}</span></span>
        </div>

        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">Factors</div>
          <table className="w-full text-xs">
            <thead className="text-left text-fg-muted">
              <tr>
                <th className="py-1 font-medium">Factor</th>
                <th className="py-1 text-right font-medium">book sensitivity</th>
                <th className="py-1 text-right font-medium">daily σ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {Object.entries(vc.book_sensitivities)
                .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                .slice(0, 12)
                .map(([f, v]) => (
                  <tr key={f}>
                    <td className="py-1">
                      <span className="font-mono text-fg-secondary">{f}</span>{" "}
                      <span className="text-fg-muted">·</span> <span className="text-fg-muted">{factorCaption(f)}</span>
                    </td>
                    <td className="py-1 text-right tnum text-fg-secondary" title={exactUsd(v)}>{humanizeUsd(v)}</td>
                    <td className="py-1 text-right tnum text-fg-secondary">{vc.factor_stdev[f] != null ? vc.factor_stdev[f].toExponential(2) : "—"}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

export function RiskMethodology() {
  const vc = useVarConstruction();
  return (
    <div className="space-y-6">
      <SensitivityLineage />
      <QueryState query={vc} empty={(d) => !d.available}>
        {(data) => <VarHistogram vc={data} />}
      </QueryState>
    </div>
  );
}

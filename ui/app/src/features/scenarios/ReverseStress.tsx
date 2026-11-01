import { useState } from "react";
import {
  useReverseStress,
  useReverseDetail,
  type ReverseResult,
} from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// S5 Reverse stress: "what would break us?" — per scenario, the severity λ (fraction of the
// adverse driver move) whose full-reval loss hits the loss limit. Ranked most-fragile-first;
// drilldown shows the exact breaking-shock vector.

function fmtShock(factor: string, v: number): string {
  if (factor.startsWith("ir:") || factor.startsWith("cs")) return `${(v * 10000).toFixed(1)}bp`;
  return `${(v * 100).toFixed(1)}%`;
}

function lambdaTone(l: number | null): string {
  if (l == null) return "text-fg-muted";
  if (l <= 0.5) return "text-danger-fg"; // breaches at <½ an adverse move — fragile
  if (l <= 1) return "text-warning-fg";
  return "text-fg";
}

function Detail({ name }: { name: string }) {
  const q = useReverseDetail(name);
  return (
    <QueryState query={q} empty={(d) => !d.available}>
      {(d) => {
        const rows = Object.entries(d.vector)
          .map(([f, v]) => ({ factor: f, shock: v, isDriver: d.drivers.includes(f) }))
          .sort((a, b) => Math.abs(b.shock) - Math.abs(a.shock));
        const overshoot = d.achieved_loss != null && d.target_loss != null;
        return (
          <div className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="text-sm font-semibold text-fg">{d.scenario}</div>
              <Zone status={d.reachable ? "red" : "green"} label={d.reachable ? "breaches limit" : "not reachable"} />
            </div>
            {d.description && <div className="text-xs text-fg-secondary">{d.description}</div>}
            <MetricGrid
              columns={3}
              metrics={[
                {
                  label: "Severity λ",
                  value: d.severity_scale != null ? `${d.severity_scale.toFixed(2)}×` : "—",
                  tone: (d.severity_scale ?? 1) <= 0.5 ? "loss" : "neutral",
                  caption: "of the adverse driver move",
                },
                {
                  label: "Loss at λ",
                  value: humanizeUsd(d.achieved_loss),
                  tone: "loss",
                  title: overshoot ? exactUsd(d.achieved_loss) : undefined,
                  caption: `target ${humanizeUsd(d.target_loss)}`,
                },
                { label: "Solve iterations", value: d.iterations ?? "—", caption: "bisection steps" },
              ]}
            />
            <div className="border-t border-subtle pt-2 text-xs font-semibold text-fg-secondary">
              Breaking-shock vector <span className="font-normal text-fg-muted">· {rows.length} factors · drivers flagged</span>
            </div>
            <div className="max-h-80 overflow-auto">
              <table className="w-full text-sm">
                <tbody className="divide-y divide-border-subtle">
                  {rows.map((r) => (
                    <tr key={r.factor} className={cn(r.isDriver && "bg-warning-tint/50")}>
                      <td className="py-1 pr-2">
                        <span className="font-mono text-xs text-fg-secondary">{r.factor}</span>
                        {r.isDriver && <span className="ml-2 rounded bg-warning-tint px-1 py-0.5 text-nano font-bold text-warning-fg">DRIVER</span>}
                      </td>
                      <td className={cn("py-1 text-right tnum text-xs", r.shock >= 0 ? "text-success-fg" : "text-danger-fg")}>
                        {r.shock >= 0 ? "+" : ""}{fmtShock(r.factor, r.shock)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-micro text-fg-muted">
              The driver legs are the scenario's seeded narrative moves; the rest are propagated through the
              conditional covariance. This is the exact shock at the severity that first breaches the loss limit.
            </p>
          </div>
        );
      }}
    </QueryState>
  );
}

export function ReverseStress() {
  const query = useReverseStress();
  const [sel, setSel] = useState<string | null>(null);

  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(data) => {
        if (!data.available) {
          return (
            <WarningBanner tone="info" title="No reverse-stress run yet">
              {data.reason ?? "Run the reverse-stress solver to populate this view."}
            </WarningBanner>
          );
        }
        const results = data.results ?? [];
        const selected = sel ?? results[0]?.scenario ?? null;
        const metrics: Metric[] = [
          { label: "Loss limit", value: humanizeUsd(data.target_loss), caption: "the target we solve against" },
          { label: "Scenarios tested", value: data.n_scenarios ?? results.length },
          {
            label: "Breach the limit",
            value: `${data.n_reachable ?? 0} / ${data.n_scenarios ?? results.length}`,
            tone: (data.n_reachable ?? 0) > 0 ? "loss" : "gain",
            caption: "reachable within max severity",
          },
          { label: "Valuation", value: data.valuation_date ?? "—", caption: `run ${(data.run_id ?? "").slice(0, 8)}` },
        ];

        return (
          <div className="space-y-5">
            <Card>
              <CardBody>
                <MetricGrid metrics={metrics} columns={4} />
              </CardBody>
            </Card>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* fragility ranking */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Fragility ranking <span className="font-normal text-fg-muted">· lowest λ = most fragile</span>
                </div>
                <div className="divide-y divide-border-subtle">
                  {results.map((r: ReverseResult) => {
                    const barPct = r.severity_scale != null ? Math.min(100, (r.severity_scale / 1) * 100) : 100;
                    return (
                      <button
                        key={r.scenario}
                        type="button"
                        onClick={() => setSel(r.scenario)}
                        className={cn(
                          "w-full px-4 py-2.5 text-left hover:bg-surface-2",
                          selected === r.scenario && "bg-surface-2",
                        )}
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-sm font-medium text-fg">{r.scenario}</span>
                          <span className={cn("tnum text-sm font-semibold", lambdaTone(r.severity_scale))}>
                            {r.severity_scale != null ? `${r.severity_scale.toFixed(2)}×` : "—"}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <div className="h-2 flex-1 overflow-hidden rounded bg-surface-0">
                            <div
                              className={cn("h-full rounded", r.reachable ? "bg-danger" : "bg-border-strong")}
                              style={{ width: `${barPct}%` }}
                            />
                          </div>
                          <span className="w-24 shrink-0 text-right text-micro text-fg-muted">
                            {r.reachable ? `loss ${humanizeUsd(r.achieved_loss)}` : "not reachable"}
                          </span>
                        </div>
                        {r.description && <div className="mt-0.5 max-w-full truncate text-micro text-fg-muted">{r.description}</div>}
                      </button>
                    );
                  })}
                </div>
                <div className="px-4 py-2 text-micro text-fg-muted">
                  λ is the fraction of the adverse driver move needed to reach the loss limit. λ &lt; 1 means even a
                  sub-adverse version of the scenario breaks the book on this axis.
                </div>
              </Card>

              {/* detail */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Breaking shock
                </div>
                <CardBody>
                  {selected ? <Detail name={selected} /> : <div className="text-xs text-fg-secondary">Select a scenario.</div>}
                </CardBody>
              </Card>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}

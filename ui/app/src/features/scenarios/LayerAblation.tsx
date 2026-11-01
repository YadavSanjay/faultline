import { useState } from "react";
import { Link } from "react-router-dom";
import { useAblation, type ScenarioWaterfall } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd, fmtTimestamp, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/cn";
import { AblationWaterfall, READING_RULE } from "./AblationWaterfall";

// Layer Ablation (step 15f, design §7.1): per scenario tier, how far each enrichment layer moved the
// P&L from the authored scenario (ladder bars) and its order-free Shapley share, plus calls / latency /
// cost per step. Reads GET /api/scenarios/ablation.

const key = (w: ScenarioWaterfall) => `${w.scenario}|${w.severity}`;

export function LayerAblation() {
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [valuationDate, setValuationDate] = useState<string | undefined>(undefined);
  const query = useAblation({ valuationDate, runId });
  const [sel, setSel] = useState<string | null>(null);

  return (
    <QueryState query={query}>
      {(data) => {
        if (!data.available) {
          return (
            <WarningBanner tone="info" title="No layer-ablation run to show">
              {data.reason ?? "Run python -m src.scenariogen.ablation_runner to populate this view."}
            </WarningBanner>
          );
        }
        const scenarios = data.scenarios ?? [];
        const selected = scenarios.find((w) => key(w) === sel) ?? scenarios[0];
        const metrics: Metric[] = [
          { label: "Valuation", value: data.valuation_date ?? "—", caption: `run ${(data.run_id ?? "").slice(0, 8)} · ${fmtTimestamp(data.generated_at)}` },
          { label: "Severity", value: data.severity ?? "—", caption: "tier ablated in this run" },
          { label: "Scenarios", value: scenarios.length, caption: `${(data.stopped ?? []).length} stopped` },
          {
            label: "Run overhead",
            value: data.overhead ? `${data.overhead.n_calls ?? 0} calls` : "—",
            caption: data.overhead
              ? `${fmtDuration(data.overhead.latency_s)} · ${data.overhead.cost_usd != null ? `$${data.overhead.cost_usd.toFixed(4)}` : "cost not reported"}`
              : "no per-layer cost in this run",
          },
        ];
        return (
          <div className="space-y-5">
            <Card>
              <CardBody className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  {(data.available_dates ?? []).length > 1 && (
                    <select
                      className="rounded border border-strong bg-surface-1 px-2 py-1"
                      value={data.valuation_date ?? ""}
                      onChange={(e) => { setValuationDate(e.target.value); setRunId(undefined); }}
                    >
                      {(data.available_dates ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  )}
                  {(data.runs ?? []).length > 1 && (
                    <select
                      className="rounded border border-strong bg-surface-1 px-2 py-1"
                      value={data.run_id ?? ""}
                      onChange={(e) => setRunId(e.target.value || undefined)}
                    >
                      {(data.runs ?? []).map((r) => (
                        <option key={r.run_id ?? ""} value={r.run_id ?? ""}>
                          {(r.run_id ?? "").slice(0, 8)} · {r.severity} · {fmtTimestamp(r.generated_at)}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                <MetricGrid metrics={metrics} columns={4} />
              </CardBody>
            </Card>

            {data.mixed_rules && (
              <WarningBanner tone="warn" title="Mixed composition rules">
                This run holds scenarios composed under {(data.composition_rules ?? []).join(" and ")}. Compare
                waterfalls only within one rule.
              </WarningBanner>
            )}
            {(data.stopped ?? []).length > 0 && (
              <WarningBanner tone="error" title={`${(data.stopped ?? []).length} scenario(s) stopped`}>
                {(data.stopped ?? []).map((s) => `${s.scenario}: ${s.reason}`).join(" · ")}
              </WarningBanner>
            )}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-4">
              <Card className="lg:col-span-1">
                <CardHeader title="Scenarios" subtitle="final P&L after every layer" />
                <div className="divide-y divide-border-subtle">
                  {scenarios.map((w) => (
                    <button
                      key={key(w)}
                      type="button"
                      onClick={() => setSel(key(w))}
                      className={cn("flex w-full items-baseline justify-between gap-2 px-4 py-2 text-left hover:bg-surface-2", selected && key(selected) === key(w) && "bg-surface-2")}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-fg">{w.scenario}</span>
                        <span className="text-micro text-fg-muted">{w.severity} · {w.composition_rule}</span>
                      </span>
                      <span className={cn("tnum text-sm", w.final_pnl >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(w.final_pnl)}>
                        {humanizeUsd(w.final_pnl)}
                      </span>
                    </button>
                  ))}
                </div>
              </Card>
              <div className="space-y-2 lg:col-span-3">
                {selected ? (
                  <>
                    <div className="flex items-baseline justify-between">
                      <h2 className="text-sm font-semibold text-fg">{selected.scenario} · {selected.severity}</h2>
                      <Link to={`/scenarios/${selected.scenario}?severity=${selected.severity}`} className="text-xs text-fg-secondary hover:text-fg">
                        scenario detail ▸
                      </Link>
                    </div>
                    <AblationWaterfall w={selected} />
                  </>
                ) : (
                  <WarningBanner tone="info" title="No scenario in this run" />
                )}
              </div>
            </div>
            <p className="text-micro text-fg-muted">{data.note ?? READING_RULE}</p>
          </div>
        );
      }}
    </QueryState>
  );
}

import type { EChartsOption } from "echarts";
import { useLlmSummary, type LlmOpsSummary } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { EChart } from "@/components/shared/EChart";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { fmtCost, fmtTokens, fmtLatency, serviceColor } from "./format";
import { cn } from "@/lib/cn";

// Operations landing: total LLM/RAG spend, budget headroom, per-service and per-stage
// breakdowns, spend-over-time. Works today off the dual-RAG call traces; the LLM per-stage
// panels populate once a scenario-gen LLM run is executed.

function BudgetGauge({ s }: { s: LlmOpsSummary }) {
  const ceiling = s.budget.max_total_cost_usd;
  const peak = s.llm.max_run_cost_usd;
  const pct = ceiling > 0 ? Math.min(100, (peak / ceiling) * 100) : 0;
  const tone = pct > 100 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-success";
  return (
    <Card>
      <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
        Per-run cost budget
      </div>
      <CardBody className="space-y-2">
        <div className="flex items-baseline justify-between text-xs text-fg-secondary">
          <span>peak run {fmtCost(peak)}</span>
          <span>ceiling {fmtCost(ceiling)} / run</span>
        </div>
        <div className="relative h-3 overflow-hidden rounded bg-surface-0">
          <div className={cn("h-full rounded", tone)} style={{ width: `${pct}%` }} />
          <div className="absolute top-0 h-full border-l border-strong" style={{ left: "75%" }} />
        </div>
        <div className="text-micro text-fg-muted">
          {s.llm.n_runs === 0
            ? "No LLM generation runs yet — the budget applies per scenario-gen run (BudgetExceededError stops a run at the ceiling)."
            : `${s.llm.n_over_budget} of ${s.llm.n_runs} runs over budget · ${pct.toFixed(0)}% of ceiling`}
        </div>
      </CardBody>
    </Card>
  );
}

export function LlmCostOverview() {
  const query = useLlmSummary();
  const { theme } = useTheme();
  const c = chartColors(theme);

  return (
    <QueryState query={query}>
      {(s) => {
        const services = Object.entries(s.rag.by_service).sort((a, b) => b[1].cost_usd - a[1].cost_usd);
        const totalCalls = s.rag.n_calls;

        const metrics: Metric[] = [
          { label: "Total spend", value: fmtCost(s.totals.total_cost_usd), caption: `${s.totals.n_runs} LLM runs · ${s.totals.n_traces} traces` },
          { label: "LLM calls", value: s.llm.n_llm_calls + (s.rag.by_service.llm?.n_calls ?? 0), caption: `${fmtTokens(s.llm.total_tokens + (s.rag.by_service.llm?.total_tokens ?? 0))} tokens` },
          { label: "RAG calls", value: totalCalls, caption: `${s.rag.n_traces} trace(s) · ${fmtLatency(s.rag.total_latency_ms)} total` },
          { label: "Budget / run", value: fmtCost(s.budget.max_total_cost_usd), tone: s.llm.n_over_budget ? "loss" : "neutral", caption: `${s.llm.n_over_budget} over budget` },
        ];

        const spend = s.spend_over_time;
        const spendChart: EChartsOption = {
          grid: { left: 60, right: 16, top: 16, bottom: 28 },
          tooltip: { trigger: "axis", valueFormatter: (v) => fmtCost(v as number) },
          xAxis: { type: "category", data: spend.map((p) => p.label), axisLabel: { fontSize: 10 } },
          yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (v: number) => fmtCost(v) } },
          series: [{ name: "Cost", type: "bar", data: spend.map((p) => p.cost_usd), itemStyle: { color: c.accent }, barMaxWidth: 40 }],
        };

        return (
          <div className="space-y-5">
            <Card>
              <CardBody>
                <MetricGrid metrics={metrics} columns={4} />
              </CardBody>
            </Card>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <BudgetGauge s={s} />

              {/* per-service breakdown */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Cost &amp; activity by service
                </div>
                <CardBody className="space-y-2">
                  {services.length === 0 ? (
                    <div className="text-xs text-fg-secondary">No call traces recorded yet.</div>
                  ) : (
                    services.map(([svc, v]) => (
                      <div key={svc} className="flex items-center gap-3">
                        <div className="flex w-24 shrink-0 items-center gap-1.5">
                          <span className="h-2.5 w-2.5 rounded-full" style={{ background: serviceColor(svc) }} />
                          <span className="text-xs text-fg-secondary">{svc}</span>
                        </div>
                        <div className="h-3 flex-1 overflow-hidden rounded bg-surface-0">
                          <div className="h-full rounded" style={{ width: `${(v.n_calls / Math.max(1, totalCalls)) * 100}%`, background: serviceColor(svc) }} />
                        </div>
                        <div className="w-14 shrink-0 text-right text-xs tabular-nums text-fg-secondary">{v.n_calls} calls</div>
                        <div className="w-16 shrink-0 text-right text-xs tabular-nums text-fg">{fmtCost(v.cost_usd)}</div>
                        <div className="w-14 shrink-0 text-right text-micro tabular-nums text-fg-muted">{fmtLatency(v.latency_ms)}</div>
                      </div>
                    ))
                  )}
                  {services.some(([, v]) => v.n_errors > 0) && (
                    <div className="text-micro text-danger-fg">
                      {services.reduce((n, [, v]) => n + v.n_errors, 0)} call error(s) across services.
                    </div>
                  )}
                </CardBody>
              </Card>
            </div>

            {/* per-stage LLM breakdown */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                LLM cost by stage
              </div>
              <CardBody>
                {Object.keys(s.llm.per_stage).length === 0 ? (
                  <div className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-secondary">
                    No per-stage LLM breakdown yet — this populates from scenario-gen LLM run manifests
                    (<span className="font-mono">data/pricer/scenario_llm/</span>). The model tier per stage is shown below.
                  </div>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs text-fg-secondary">
                      <tr>
                        <th className="py-1 font-medium">Stage</th>
                        <th className="py-1 text-right font-medium">Calls</th>
                        <th className="py-1 text-right font-medium">Tokens</th>
                        <th className="py-1 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {Object.entries(s.llm.per_stage).map(([stage, v]) => (
                        <tr key={stage}>
                          <td className="py-1 font-mono text-xs text-fg">{stage}</td>
                          <td className="py-1 text-right tabular-nums text-fg-secondary">{v.n_calls}</td>
                          <td className="py-1 text-right tabular-nums text-fg-secondary">{fmtTokens(v.prompt_tokens + v.completion_tokens)}</td>
                          <td className="py-1 text-right tabular-nums text-fg">{fmtCost(v.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {/* cost by provider */}
                {Object.keys(s.llm.per_provider).length > 0 && (
                  <table className="mt-3 w-full border-t border-subtle pt-3 text-sm">
                    <thead className="text-left text-xs text-fg-secondary">
                      <tr>
                        <th className="py-1 font-medium">Provider</th>
                        <th className="py-1 text-right font-medium">Calls</th>
                        <th className="py-1 text-right font-medium">Tokens</th>
                        <th className="py-1 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {Object.entries(s.llm.per_provider).map(([provider, v]) => (
                        <tr key={provider}>
                          <td className="py-1 text-xs text-fg">{provider}</td>
                          <td className="py-1 text-right tabular-nums text-fg-secondary">{v.n_calls}</td>
                          <td className="py-1 text-right tabular-nums text-fg-secondary">{fmtTokens(v.prompt_tokens + v.completion_tokens)}</td>
                          <td className="py-1 text-right tabular-nums text-fg">{fmtCost(v.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {/* model tiers of the active profile */}
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-subtle pt-3">
                  <span className="rounded bg-surface-2 px-2 py-0.5 text-micro text-fg-secondary">
                    profile: <span className="font-mono text-fg">{s.budget.active_profile}</span>
                  </span>
                  {Object.entries(s.budget.model_tiers).map(([stage, model]) => (
                    <span key={stage} className="rounded bg-surface-0 px-2 py-0.5 text-micro text-fg-secondary">
                      {stage}: <span className="font-mono text-fg">{model}</span>
                    </span>
                  ))}
                </div>
              </CardBody>
            </Card>

            {/* spend over time */}
            {spend.length > 0 && (
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Spend over time (per run)
                </div>
                <CardBody>
                  <EChart option={spendChart} height={200} />
                </CardBody>
              </Card>
            )}
          </div>
        );
      }}
    </QueryState>
  );
}

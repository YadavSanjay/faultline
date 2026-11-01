import { useState } from "react";
import {
  useLlmRuns,
  useLlmTraces,
  useLlmRunDetail,
  useLlmTraceDetail,
} from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { fmtCost, fmtTokens, fmtLatency, serviceColor } from "./format";
import { cn } from "@/lib/cn";

// Run & trace explorer: the cross-run index the platform lacked. LLM generation runs (from
// scenario_llm manifests) drill into per-stage cost; dual-RAG call traces drill into the
// per-call list (service / purpose / cost / latency).

type Sel = { kind: "run" | "trace"; id: string } | null;

function RunDetail({ id }: { id: string }) {
  const q = useLlmRunDetail(id);
  return (
    <QueryState query={q} empty={(d) => !d.available}>
      {(d) => (
        <div className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div className="font-mono text-xs text-fg-secondary">{d.run_id}</div>
            <Zone status={d.budget.over_budget ? "red" : "green"} label={d.budget.over_budget ? "over budget" : "within budget"} />
          </div>
          <div className="text-xs text-fg-secondary">
            {d.mode} · profile <span className="font-mono">{d.profile}</span> · {d.n_scenarios} scenarios ·{" "}
            {fmtCost(d.cost.total_cost_usd)} · {fmtTokens(d.total_tokens)} tokens ·
            world-view <span className="font-mono">{d.world_view_id}</span>
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-fg-secondary">
              <tr>
                <th className="py-1 font-medium">Stage</th>
                <th className="py-1 font-medium">Provider</th>
                <th className="py-1 font-medium">Model</th>
                <th className="py-1 text-right font-medium">Calls</th>
                <th className="py-1 text-right font-medium">Tokens</th>
                <th className="py-1 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {Object.entries(d.cost.per_stage).map(([stage, v]) => (
                <tr key={stage}>
                  <td className="py-1 font-mono text-xs text-fg">{stage}</td>
                  <td className="py-1 text-micro text-fg-secondary">{d.stage_providers[stage] ?? "—"}</td>
                  <td className="py-1 font-mono text-micro text-fg-secondary">{d.models[stage] ?? "—"}</td>
                  <td className="py-1 text-right tabular-nums text-fg-secondary">{v.n_calls}</td>
                  <td className="py-1 text-right tabular-nums text-fg-secondary">{fmtTokens(v.prompt_tokens + v.completion_tokens)}</td>
                  <td className="py-1 text-right tabular-nums text-fg">{fmtCost(v.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.scenarios.length > 0 && (
            <div className="flex flex-wrap gap-1 border-t border-subtle pt-2">
              {d.scenarios.map((sc) => (
                <span key={sc} className="rounded border border px-1.5 py-0.5 text-micro text-fg-secondary">{sc}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </QueryState>
  );
}

function TraceDetail({ id }: { id: string }) {
  const q = useLlmTraceDetail(id);
  return (
    <QueryState query={q} empty={(d) => !d.available}>
      {(d) => (
        <div className="space-y-3">
          <div className="font-mono text-xs text-fg-secondary">{d.run_id}</div>
          <div className="text-xs text-fg-secondary">
            {d.valuation_date} · {d.n_calls} calls · {fmtCost(d.total_cost_usd)} · {fmtLatency(d.total_latency_ms)} total
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(d.by_service ?? {}).map(([svc, v]) => (
              <span key={svc} className="flex items-center gap-1.5 rounded bg-surface-2 px-2 py-1 text-micro text-fg-secondary">
                <span className="h-2 w-2 rounded-full" style={{ background: serviceColor(svc) }} />
                {svc}: {v.n_calls} · {fmtCost(v.cost_usd)} · {fmtLatency(v.latency_ms)}
                {v.n_errors > 0 && <span className="text-danger-fg"> · {v.n_errors} err</span>}
              </span>
            ))}
          </div>
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-fg-secondary">
                <tr>
                  <th className="py-1 font-medium">#</th>
                  <th className="py-1 font-medium">Service</th>
                  <th className="py-1 font-medium">Purpose</th>
                  <th className="py-1 text-right font-medium">Tokens</th>
                  <th className="py-1 text-right font-medium">Cost</th>
                  <th className="py-1 text-right font-medium">Latency</th>
                  <th className="py-1 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {(d.calls ?? []).map((c) => (
                  <tr key={c.seq} className={cn(!c.ok && "bg-danger-tint/50")}>
                    <td className="py-1 tabular-nums text-fg-muted">{c.seq}</td>
                    <td className="py-1">
                      <span className="inline-flex items-center gap-1">
                        <span className="h-2 w-2 rounded-full" style={{ background: serviceColor(c.service) }} />
                        <span className="text-xs text-fg-secondary">{c.service}</span>
                      </span>
                    </td>
                    <td className="py-1 font-mono text-micro text-fg-secondary">
                      {c.purpose}
                      {c.model && <span className="ml-1 text-fg-muted">· {c.model}</span>}
                    </td>
                    <td className="py-1 text-right tabular-nums text-fg-secondary">{c.total_tokens || "—"}</td>
                    <td className="py-1 text-right tabular-nums text-fg">{c.cost_usd ? fmtCost(c.cost_usd) : "—"}</td>
                    <td className="py-1 text-right tabular-nums text-fg-secondary">{fmtLatency(c.latency_ms)}</td>
                    <td className="py-1">{!c.ok && <span className="text-micro text-danger-fg">error</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </QueryState>
  );
}

export function LlmRunExplorer() {
  const runs = useLlmRuns();
  const traces = useLlmTraces();
  const [sel, setSel] = useState<Sel>(null);

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <div className="space-y-5">
        {/* LLM generation runs */}
        <Card>
          <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
            <h3 className="text-sm font-semibold text-fg">LLM generation runs</h3>
            <span className="text-xs text-fg-muted">{runs.data?.count ?? 0}</span>
          </div>
          <QueryState query={runs}>
            {(d) =>
              d.runs.length === 0 ? (
                <CardBody className="text-xs text-fg-secondary">
                  No LLM scenario-generation runs recorded yet. When a run executes, its per-stage cost and
                  token usage appear here (from <span className="font-mono">data/pricer/scenario_llm/</span>).
                </CardBody>
              ) : (
                <div className="divide-y divide-border-subtle">
                  {d.runs.map((r) => (
                    <button
                      key={r.run_id}
                      type="button"
                      onClick={() => setSel({ kind: "run", id: r.run_id })}
                      className={cn(
                        "flex w-full items-center justify-between px-4 py-2 text-left hover:bg-surface-2",
                        sel?.kind === "run" && sel.id === r.run_id && "bg-surface-2",
                      )}
                    >
                      <div>
                        <div className="font-mono text-xs text-fg">{r.run_id.slice(0, 12)}…</div>
                        <div className="text-micro text-fg-muted">{r.mode} · {r.profile} · {r.created_at}</div>
                      </div>
                      <div className="text-right">
                        <div className="text-xs tabular-nums text-fg">{fmtCost(r.total_cost_usd)}</div>
                        <div className="text-micro text-fg-muted">{r.n_scenarios} scen</div>
                      </div>
                    </button>
                  ))}
                </div>
              )
            }
          </QueryState>
        </Card>

        {/* dual-RAG call traces */}
        <Card>
          <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
            <h3 className="text-sm font-semibold text-fg">Dual-RAG call traces</h3>
            <span className="text-xs text-fg-muted">{traces.data?.count ?? 0}</span>
          </div>
          <QueryState query={traces}>
            {(d) =>
              d.traces.length === 0 ? (
                <CardBody className="text-xs text-fg-secondary">No call traces recorded yet.</CardBody>
              ) : (
                <div className="divide-y divide-border-subtle">
                  {d.traces.map((t) => (
                    <button
                      key={t.run_id}
                      type="button"
                      onClick={() => setSel({ kind: "trace", id: t.run_id })}
                      className={cn(
                        "flex w-full items-center justify-between px-4 py-2 text-left hover:bg-surface-2",
                        sel?.kind === "trace" && sel.id === t.run_id && "bg-surface-2",
                      )}
                    >
                      <div>
                        <div className="font-mono text-xs text-fg">{t.run_id.slice(0, 12)}…</div>
                        <div className="text-micro text-fg-muted">{t.valuation_date} · {t.services.join(", ")}</div>
                      </div>
                      <div className="text-right">
                        <div className="text-xs tabular-nums text-fg">{fmtCost(t.total_cost_usd)}</div>
                        <div className="text-micro text-fg-muted">{t.n_calls} calls{t.n_errors ? ` · ${t.n_errors} err` : ""}</div>
                      </div>
                    </button>
                  ))}
                </div>
              )
            }
          </QueryState>
        </Card>
      </div>

      {/* detail */}
      <Card>
        <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
          {sel ? (sel.kind === "run" ? "Run detail" : "Trace detail") : "Detail"}
        </div>
        <CardBody>
          {!sel ? (
            <div className="text-xs text-fg-secondary">Select a run or trace on the left to see its per-stage / per-call breakdown.</div>
          ) : sel.kind === "run" ? (
            <RunDetail id={sel.id} />
          ) : (
            <TraceDetail id={sel.id} />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

import { Link } from "react-router-dom";
import { useScenarioCatalog, type ScenarioCell } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { plausibilityZone, signoffZone } from "./format";
import { outcomeLabel } from "./waterfall";
import { cn } from "@/lib/cn";

// SC-B1 Scenario Catalog: the standing set (scenario × tier) and which are concerning.

function Cell({ cell }: { cell: ScenarioCell }) {
  if (!cell.ran) {
    return (
      <Link
        to={`/scenarios/${cell.scenario}?severity=${cell.severity}`}
        className={cn(
          "block rounded-md border border-dashed border bg-surface-2 p-2 text-center text-xs hover:bg-surface-0",
          cell.outcome === "held_out" ? "text-warning-fg" : cell.outcome === "stopped" ? "text-danger-fg" : "text-fg-muted",
        )}
        title={cell.horizon_days != null ? `horizon ${cell.horizon_days}d` : undefined}
      >
        {outcomeLabel(cell.outcome)}
      </Link>
    );
  }
  return (
    <Link
      to={`/scenarios/${cell.scenario}?severity=${cell.severity}`}
      className="block rounded-md border border bg-surface-1 p-2 hover:border-strong hover:shadow-sm"
    >
      <div className={cn("tnum text-sm font-semibold", (cell.total_pnl ?? 0) >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(cell.total_pnl ?? 0)}>
        {humanizeUsd(cell.total_pnl)}
      </div>
      {cell.capital_delta != null && cell.capital_delta !== 0 && (
        <div className="tnum text-micro text-fg-secondary" title={`capital Δ ${exactUsd(cell.capital_delta)}`}>
          cap {humanizeUsd(cell.capital_delta)}
        </div>
      )}
      <div className="mt-1 flex items-center gap-1">
        <span className={cn("inline-block h-2 w-2 rounded-full", plausibilityZone(cell.plausibility_status) === "green" ? "bg-success" : plausibilityZone(cell.plausibility_status) === "amber" ? "bg-warning" : plausibilityZone(cell.plausibility_status) === "red" ? "bg-danger" : "bg-border-strong")} title={`plausibility: ${cell.plausibility_status}`} />
        <span className={cn("inline-block h-2 w-2 rounded-full", signoffZone(cell.signoff) === "green" ? "bg-success" : signoffZone(cell.signoff) === "red" ? "bg-danger" : "bg-border-strong")} title={`sign-off: ${cell.signoff}`} />
        {cell.used_macro && <span className="rounded bg-cat-teal-tint px-1 text-nano text-cat-teal-fg">macro</span>}
      </div>
    </Link>
  );
}

export function ScenarioCatalog() {
  const query = useScenarioCatalog();

  return (
    <QueryState query={query}>
      {(data) => {
        const byScenario = (name: string, tier: string) =>
          data.cells.find((c) => c.scenario === name && c.severity === tier);
        const worst = [...data.cells].filter((c) => c.ran).sort((a, b) => (a.total_pnl ?? 0) - (b.total_pnl ?? 0)).slice(0, 5);

        return (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="text-xs text-fg-secondary">val {data.valuation_date} · Σ {data.covariance.window} · {data.covariance.n_obs}d</div>
              <ProvenanceBadge items={{ run_id: data.run_id, snapshot_id: data.snapshot_id, cov_id: data.covariance.cov_id }} />
            </div>

            {/* scenario × tier grid */}
            <Card>
              <div className="overflow-auto p-4">
                <table className="w-full border-separate border-spacing-1">
                  <thead>
                    <tr>
                      <th className="w-48 text-left text-xs font-medium text-fg-muted">scenario</th>
                      {data.tiers.map((t) => (
                        <th key={t} className="text-center text-xs font-medium capitalize text-fg-secondary">{t}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.scenarios.map((name) => (
                      <tr key={name}>
                        <td className="pr-3 align-top">
                          <Link to={`/scenarios/${name}`} className="text-sm font-medium text-fg hover:underline">{name}</Link>
                          <div className="max-w-[180px] text-micro text-fg-muted">{byScenario(name, data.tiers[0])?.narrative}</div>
                        </td>
                        {data.tiers.map((t) => {
                          const cell = byScenario(name, t);
                          return <td key={t} className="w-36">{cell && <Cell cell={cell} />}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* worst-first ranking */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">Worst by loss</div>
              <CardBody>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
                  {worst.map((c) => (
                    <Link key={`${c.scenario}-${c.severity}`} to={`/scenarios/${c.scenario}?severity=${c.severity}`} className="rounded-md border border-subtle p-2 hover:bg-surface-2">
                      <div className="truncate text-xs font-medium text-fg-secondary">{c.scenario}</div>
                      <div className="text-micro capitalize text-fg-muted">{c.severity}</div>
                      <div className="tnum text-sm font-semibold text-danger-fg">{humanizeUsd(c.total_pnl)}</div>
                    </Link>
                  ))}
                </div>
              </CardBody>
            </Card>

            {/* AI-generated scenarios (from the LLM flow — not part of the static grid) */}
            {(data.llm_scenarios?.length ?? 0) > 0 && (
              <Card>
                <div className="flex items-center gap-2 border-b border-subtle px-4 py-2">
                  <span className="rounded bg-cat-violet-tint px-1.5 py-0.5 text-nano font-semibold text-cat-violet-fg">AI</span>
                  <span className="text-sm font-semibold text-fg">AI-generated scenarios</span>
                  <span className="text-micro text-fg-muted">worst loss first · from the LLM scenario flow</span>
                </div>
                <CardBody>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {(data.llm_scenarios ?? []).map((c) => (
                      <Link
                        key={`${c.scenario}-${c.severity}`}
                        to={`/scenarios/${encodeURIComponent(c.scenario)}?severity=${c.severity}`}
                        className="rounded-md border border-cat-violet-fg/20 bg-cat-violet-tint/40 p-3 hover:border-cat-violet-fg/40 hover:shadow-sm"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="truncate text-sm font-medium text-fg">{c.scenario}</div>
                          <span className="shrink-0 rounded bg-cat-violet-tint px-1 text-nano font-semibold text-cat-violet-fg">AI</span>
                        </div>
                        {c.trigger && <div className="truncate text-micro text-fg-muted">{c.trigger}</div>}
                        <div className="mt-1 flex items-end justify-between gap-2">
                          <div className={cn("tnum text-lg font-semibold", (c.total_pnl ?? 0) >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(c.total_pnl ?? 0)}>
                            {humanizeUsd(c.total_pnl)}
                          </div>
                          <div className="text-right text-nano text-fg-muted">
                            {c.world_view_id && <div title="MacroRAG world view">wv {c.world_view_id}</div>}
                            {c.total_cost_usd != null && <div className="tnum">${c.total_cost_usd.toFixed(3)}</div>}
                          </div>
                        </div>
                      </Link>
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}

            <div className="flex items-center gap-4 text-micro text-fg-muted">
              <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-success" />/<span className="inline-block h-2 w-2 rounded-full bg-warning" />/<span className="inline-block h-2 w-2 rounded-full bg-danger" /> plausibility (1st dot) · sign-off (2nd dot)</span>
              <span>capital Δ = ES-under-shock increase</span>
            </div>

            {data.cells.some((c) => c.used_macro) || (
              <Zone status="unknown" label="macro legs: none applied this run (authored/static fallback)" />
            )}
          </div>
        );
      }}
    </QueryState>
  );
}

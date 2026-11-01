import { Link, useParams } from "react-router-dom";
import { useRunTrace } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { humanizeUsd, fmtDuration, fmtTimestamp } from "@/lib/format";
import { cn } from "@/lib/cn";

function statusZone(s: string): ZoneStatus {
  return s === "ok" ? "green" : s === "partial" ? "amber" : s === "failed" ? "red" : "unknown";
}

// S3 Run Trace — the end-to-end request flow for one run (manifest-level in V1).
export function RunTrace() {
  const { runId = "" } = useParams();
  const query = useRunTrace(runId);

  return (
    <div className="space-y-4">
      <nav className="text-xs text-fg-secondary">
        <Link to="/ops/runs" className="hover:underline">Run History</Link>
        <span className="mx-1 text-fg-muted">›</span>
        <span className="font-mono">{runId.slice(0, 8)}</span>
      </nav>

      <QueryState query={query} empty={(d) => !d.available}>
        {(t) => {
          const run = t.run!;
          return (
            <div className="space-y-4">
              <Card>
                <CardBody className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className="rounded bg-surface-0 px-2 py-0.5 text-xs font-medium text-fg-secondary">{run.module}</span>
                    <span className="font-mono text-sm text-fg">{run.run_id.slice(0, 12)}</span>
                    <Zone status={statusZone(run.status)} label={run.status} />
                  </div>
                  <div className="text-xs text-fg-secondary">
                    val {run.date} · {fmtDuration(run.duration_s)} · {fmtTimestamp(run.finished_at)}
                  </div>
                </CardBody>
              </Card>

              {t.trace_level === "span" && t.spans ? (
                <WarningBanner tone="info" title={`Span-level trace · ${t.spans.n_spans} spans`}>
                  Real per-stage and per-trade timings captured during the run (ENG-4 span sink).
                </WarningBanner>
              ) : (
                <WarningBanner tone="info" title="Manifest-level trace">
                  Stages are reconstructed from the run manifest; this run predates the span sink (ENG-4) or is a
                  module that doesn't yet emit spans.
                </WarningBanner>
              )}

              {/* span timings */}
              {t.spans?.available && (
                <Card>
                  <CardHeader title="Stage timings" subtitle="wall-clock per stage (ms)" />
                  <CardBody>
                    {(() => {
                      const maxMs = Math.max(...t.spans.stages.map((s) => s.duration_ms), 1);
                      return (
                        <div className="space-y-1.5">
                          {t.spans.stages.map((s) => (
                            <div key={s.span} className="flex items-center gap-3">
                              <span className="w-36 shrink-0 text-xs text-fg-secondary">{s.span}</span>
                              <div className="relative h-4 flex-1 rounded bg-surface-0">
                                <div className="absolute top-0 h-4 rounded bg-cat-indigo-fg" style={{ width: `${(s.duration_ms / maxMs) * 100}%` }} />
                              </div>
                              <span className="tnum w-20 shrink-0 text-right text-xs text-fg-secondary">{fmtDuration(s.duration_ms / 1000)}</span>
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                    {t.spans.by_asset_class.length > 0 && (
                      <div className="mt-4">
                        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">Pricing by asset class</div>
                        <table className="w-full text-xs">
                          <thead className="text-left text-fg-muted">
                            <tr>
                              <th className="py-1 font-medium">Asset class</th>
                              <th className="py-1 text-right font-medium">Trades</th>
                              <th className="py-1 text-right font-medium">Total</th>
                              <th className="py-1 text-right font-medium">Mean / trade</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border-subtle">
                            {t.spans.by_asset_class.map((a) => (
                              <tr key={a.asset_class}>
                                <td className="py-1 capitalize text-fg-secondary">{a.asset_class}</td>
                                <td className="py-1 text-right tnum text-fg-secondary">{a.n}</td>
                                <td className="py-1 text-right tnum text-fg">{fmtDuration(a.total_ms / 1000)}</td>
                                <td className="py-1 text-right tnum text-fg-secondary">{a.mean_ms.toFixed(1)}ms</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </CardBody>
                </Card>
              )}

              {/* stages */}
              <Card>
                <CardHeader title="Stages" />
                <CardBody>
                  <ol className="relative ml-2 border-l border">
                    {t.stages.map((s, i) => (
                      <li key={i} className="mb-3 ml-4">
                        <span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border-2 border-white bg-fg-muted" />
                        <div className="text-sm font-medium text-fg">{s.stage}</div>
                        <div className="text-xs text-fg-secondary">{s.detail}</div>
                      </li>
                    ))}
                  </ol>
                </CardBody>
              </Card>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {/* provenance */}
                <Card>
                  <CardHeader title="Provenance" />
                  <CardBody className="space-y-2">
                    <ProvenanceBadge items={{ run_id: run.run_id, snapshot_id: t.reproduce.snapshot_id, factor_snapshot_id: t.reproduce.factor_snapshot_id, git_sha: run.git_sha }} />
                  </CardBody>
                </Card>
                {/* reproduce */}
                <Card>
                  <CardHeader title="Reproduce" subtitle="pinned inputs" />
                  <CardBody>
                    <ol className="list-inside list-decimal space-y-1 text-xs text-fg-secondary">
                      {t.reproduce.recipe.map((step, i) => (
                        <li key={i} className="font-mono">{step}</li>
                      ))}
                    </ol>
                  </CardBody>
                </Card>
              </div>

              {/* pricer trades */}
              {t.trades && t.trades.length > 0 && (
                <Card>
                  <CardHeader title={`Priced trades (${t.trades.length})`} />
                  <div className="overflow-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                        <tr>
                          <th className="px-4 py-2 font-medium">Trade</th>
                          <th className="px-4 py-2 font-medium">Asset class</th>
                          <th className="px-4 py-2 text-right font-medium">PV</th>
                          <th className="px-4 py-2 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border-subtle">
                        {t.trades.map((tr) => (
                          <tr key={tr.trade_id} className="hover:bg-surface-2">
                            <td className="px-4 py-1.5">
                              <Link to={`/pricing/trade/${tr.trade_id}`} className="font-mono text-fg hover:underline">{tr.trade_id}</Link>
                            </td>
                            <td className="px-4 py-1.5 capitalize text-fg-secondary">{tr.asset_class}</td>
                            <td className={cn("px-4 py-1.5 text-right tnum", (tr.pv ?? 0) >= 0 ? "text-fg" : "text-danger-fg")}>{humanizeUsd(tr.pv)}</td>
                            <td className="px-4 py-1.5"><Zone status={tr.status === "ok" ? "green" : "amber"} label={tr.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { useRuns, type RunRow } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { categoryClass } from "@/components/shared/CategoryTag";
import { fmtDuration, relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";

// System ▸ Run History (S2): cross-module run timeline from the BFF union of
// data/*/store/runs/. The operational "what ran across the pipeline, and did it pass?".

function statusZone(status: string): ZoneStatus {
  if (status === "ok") return "green";
  if (status === "partial") return "amber";
  if (status === "failed") return "red";
  return "unknown";
}

function countsSummary(r: RunRow): string {
  const c = r.counts;
  if (r.module === "pricer") {
    const parts = [`${c.n_ok ?? 0}/${c.n_trades ?? 0} ok`];
    if (c.n_blocked) parts.push(`${c.n_blocked} blocked`);
    if (c.n_failed) parts.push(`${c.n_failed} failed`);
    if (c.n_drift_alerts) parts.push(`${c.n_drift_alerts} drift`);
    return parts.join(" · ");
  }
  const keys = Object.keys(c);
  return keys.length ? keys.map((k) => `${k.replace(/^n_/, "")} ${c[k]}`).join(" · ") : "—";
}

export function RunHistory({ embedded = false }: { embedded?: boolean }) {
  const [module, setModule] = useState<string | undefined>(undefined);
  const query = useRuns({ limit: 50, module });

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-fg">Run history</h3>
        <div className="flex gap-1">
          {[undefined, "pricer", "risk", "scenariogen", "marketdata"].map((m) => (
            <button
              key={m ?? "all"}
              type="button"
              onClick={() => setModule(m)}
              className={cn(
                "rounded px-2 py-0.5 text-xs",
                module === m ? "bg-primary text-primary-fg" : "text-fg-secondary hover:bg-surface-0",
              )}
            >
              {m ?? "all"}
            </button>
          ))}
        </div>
      </div>
      <div className={cn("overflow-auto", !embedded && "max-h-[70vh]")}>
        <QueryState query={query} empty={(d) => d.runs.length === 0}>
          {(data) => (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface-2 text-left text-xs text-fg-secondary">
                <tr>
                  <th className="px-4 py-2 font-medium">Module</th>
                  <th className="px-4 py-2 font-medium">Date</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Counts</th>
                  <th className="px-4 py-2 font-medium">Duration</th>
                  <th className="px-4 py-2 font-medium">Finished</th>
                  <th className="px-4 py-2 font-medium">Provenance</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {data.runs.map((r) => (
                  <tr key={`${r.module}-${r.run_id}`} className="hover:bg-surface-2">
                    <td className="px-4 py-2">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-xs font-medium",
                          categoryClass(r.module),
                        )}
                      >
                        {r.module}
                      </span>
                    </td>
                    <td className="px-4 py-2 tnum text-fg-secondary">{r.date}</td>
                    <td className="px-4 py-2">
                      <Zone status={statusZone(r.status)} label={r.status} />
                    </td>
                    <td className="px-4 py-2 text-fg-secondary">{countsSummary(r)}</td>
                    <td className="px-4 py-2 tnum text-fg-secondary">{fmtDuration(r.duration_s)}</td>
                    <td className="px-4 py-2 text-fg-secondary" title={r.finished_at ?? ""}>
                      {relativeTime(r.finished_at)}
                    </td>
                    <td className="px-4 py-2">
                      <ProvenanceBadge
                        items={{ run_id: r.run_id, snapshot_id: r.snapshot_id, git_sha: r.git_sha }}
                      />
                    </td>
                    <td className="px-4 py-2">
                      <Link to={`/ops/trace/${r.run_id}`} className="text-xs text-fg-muted hover:text-fg">trace ▸</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </div>
    </Card>
  );
}

import { Link } from "react-router-dom";
import { useDq } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { DqVerdictBadge, staleTone } from "./dq";
import { cn } from "@/lib/cn";

// MD-B5 Data-Quality Dashboard: the post-ingest gate verdicts over the declared files.
export function DqDashboard() {
  const query = useDq();
  return (
    <QueryState query={query}>
      {(data) => (
        <div className="space-y-4">
          <Card>
            <CardBody className="flex flex-wrap items-center gap-3">
              <Zone status={data.counts.block ? "red" : data.counts.warn ? "amber" : "green"} label={data.counts.block ? `${data.counts.block} blocking` : data.counts.warn ? `${data.counts.warn} warnings` : "all clean"} />
              <span className="text-sm text-fg-secondary">
                {data.files.length} gated files · {data.counts.ok ?? 0} ok · {data.counts.warn ?? 0} warn · {data.counts.block ?? 0} block
              </span>
              <span className="ml-auto text-xs text-fg-muted">max staleness {data.max_staleness_days}d</span>
            </CardBody>
          </Card>

          <Card>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">File</th>
                    <th className="px-4 py-2 font-medium">Verdict</th>
                    <th className="px-4 py-2 text-right font-medium">Rows</th>
                    <th className="px-4 py-2 text-right font-medium">Stale</th>
                    <th className="px-4 py-2 font-medium">Checks</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {data.files.map((f) => (
                    <tr key={f.id} className="hover:bg-surface-2">
                      <td className="px-4 py-2">
                        <Link to={`/marketdata/feed/${f.id}`} className="font-mono text-xs text-fg hover:underline">
                          {f.id}
                        </Link>
                      </td>
                      <td className="px-4 py-2"><DqVerdictBadge verdict={f.verdict} /></td>
                      <td className="px-4 py-2 text-right tnum text-fg-secondary">{f.row_count?.toLocaleString() ?? "—"}</td>
                      <td className={cn("px-4 py-2 text-right tnum", staleTone(f.staleness_days))}>
                        {f.staleness_days != null ? `${f.staleness_days}d` : "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-fg-secondary">
                        {f.checks.map((c) => (
                          <span key={c.check} className={cn("mr-2", c.severity === "ok" ? "text-success-fg" : c.severity === "warn" ? "text-warning-fg" : "text-danger-fg")}>
                            {c.check}
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </QueryState>
  );
}

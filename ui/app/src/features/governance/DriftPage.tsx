import { Link } from "react-router-dom";
import { useDrift } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { relativeTime } from "@/lib/format";

// G6 Drift — run-over-run PV-drift alerts from the pricer manifests.
export function DriftPage() {
  const query = useDrift();
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(d) => (
        <div className="space-y-4">
          <Card>
            <CardBody className="flex items-center gap-3">
              <Zone status={(d.latest?.n_drift_alerts ?? 0) > 0 ? "amber" : "green"} label={(d.latest?.n_drift_alerts ?? 0) > 0 ? `${d.latest?.n_drift_alerts} alerts` : "no drift"} />
              <span className="text-sm text-fg-secondary">Latest pricer run: {d.latest?.n_drift_alerts ?? 0} PV-drift alert(s)</span>
            </CardBody>
          </Card>

          <Card>
            <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">Recent runs</div>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Run</th>
                    <th className="px-4 py-2 font-medium">Date</th>
                    <th className="px-4 py-2 font-medium">Finished</th>
                    <th className="px-4 py-2 text-right font-medium">Drift alerts</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {d.timeline.map((r) => (
                    <tr key={r.run_id} className="hover:bg-surface-2">
                      <td className="px-4 py-1.5">
                        <Link to={`/ops/trace/${r.run_id}`} className="font-mono text-xs text-fg hover:underline">{r.run_id.slice(0, 12)}</Link>
                      </td>
                      <td className="px-4 py-1.5 tnum text-fg-secondary">{r.date}</td>
                      <td className="px-4 py-1.5 text-xs text-fg-muted">{relativeTime(r.finished_at)}</td>
                      <td className={r.n_drift_alerts > 0 ? "px-4 py-1.5 text-right tnum text-warning-fg" : "px-4 py-1.5 text-right tnum text-fg-muted"}>{r.n_drift_alerts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <p className="text-xs text-fg-muted">{d.note}</p>
        </div>
      )}
    </QueryState>
  );
}

import { Link } from "react-router-dom";
import { useSignalHealth, type HeldOutTier, type SignalSource } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { fmtTimestamp } from "@/lib/format";
import { cn } from "@/lib/cn";
import { healthZone } from "./waterfall";

// Signal Health (step 15d/15f): per-source coverage, staleness and holds of the newest stress run,
// worst first, plus the tiers the plausibility gate held out and the scenarios stopped fail-loud.
// Reads GET /api/scenarios/signal-health.

/** Overall badge + attention count — shared by this page, the Cockpit and the Control Room. */
export function SignalHealthBadge({ className }: { className?: string }) {
  const q = useSignalHealth();
  const d = q.data;
  if (!d) return null;
  const label = d.overall ? `signals ${d.overall}` : "signals —";
  return (
    <Link to="/scenarios/signal-health" className={cn("inline-flex items-center gap-1.5", className)} title="Scenario signal health">
      <Zone status={healthZone(d.overall)} label={label} />
      {d.attention > 0 && <span className="text-xs text-warning-fg">{d.attention} need attention</span>}
    </Link>
  );
}

function SourceRow({ s }: { s: SignalSource }) {
  const holds = Object.entries(s.holds ?? {});
  return (
    <tr className="hover:bg-surface-2">
      <td className="px-4 py-1.5 font-mono text-xs text-fg">{s.source}</td>
      <td className="px-4 py-1.5"><Zone status={healthZone(s.status)} label={s.status} /></td>
      <td className="px-4 py-1.5 tnum text-xs text-fg-secondary">{s.latest ?? "—"}</td>
      <td className={cn("px-4 py-1.5 text-right tnum text-xs", s.stale ? "text-warning-fg" : "text-fg-secondary")}>
        {s.staleness_days != null ? `${s.staleness_days}d${s.stale ? " · stale" : ""}` : "—"}
      </td>
      <td className="px-4 py-1.5 text-right tnum text-xs text-fg-secondary">
        {s.coverage != null ? `${Math.round(s.coverage * 100)}%` : "—"}
        {s.expected != null && <span className="text-fg-muted"> ({s.covered ?? 0}/{s.expected})</span>}
      </td>
      <td className="px-4 py-1.5 text-xs text-fg-secondary">
        {holds.length === 0 ? <span className="text-fg-muted">none</span> : holds.map(([k, n]) => `${k} ×${n}`).join(" · ")}
      </td>
    </tr>
  );
}

function HeldOutCard({ h }: { h: HeldOutTier }) {
  return (
    <div className="space-y-1 px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/scenarios/${h.scenario}?severity=${h.severity}`} className="text-sm font-medium text-fg hover:underline">
          {h.scenario} · {h.severity}
        </Link>
        {h.n_fail > 0 && <Zone status="red" label={`${h.n_fail} fail`} />}
        {h.n_flag > 0 && <Zone status="amber" label={`${h.n_flag} flag`} />}
        <span className="text-micro text-fg-muted">{h.n_findings} finding(s){h.scenario_version ? ` · v ${h.scenario_version.slice(0, 8)}` : ""}</span>
      </div>
      <ul className="space-y-0.5 text-xs text-fg-secondary">
        {h.findings.map((f, i) => (
          <li key={i}>
            <span className={cn("font-medium", f.severity === "fail" ? "text-danger-fg" : f.severity === "flag" ? "text-warning-fg" : "text-fg-muted")}>
              {f.severity ?? "—"}
            </span>{" "}
            {f.check}{f.factor ? ` · ${f.factor}` : ""}{f.message ? ` — ${f.message}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SignalHealth() {
  const query = useSignalHealth();
  return (
    <QueryState query={query}>
      {(d) => {
        if (!d.run_id) {
          return (
            <WarningBanner tone="info" title="No stress run to read signal health from">
              {d.reason ?? "Run python -m src.scenariogen.runner to populate this view."}
            </WarningBanner>
          );
        }
        return (
          <div className="space-y-5">
            <Card>
              <CardBody className="flex flex-wrap items-center gap-3">
                <Zone status={healthZone(d.overall)} label={`overall ${d.overall ?? "not recorded"}`} className="text-sm" />
                <span className="text-sm text-fg-secondary">
                  {d.attention > 0 ? `${d.attention} item(s) need attention` : "nothing needs attention"}
                </span>
                {d.counts && (
                  <span className="text-xs text-fg-muted">
                    {Object.entries(d.counts).map(([k, n]) => `${n} ${k}`).join(" · ")}
                  </span>
                )}
                <span className="ml-auto text-xs text-fg-muted">
                  val {d.valuation_date} · run {(d.run_id ?? "").slice(0, 8)} · {fmtTimestamp(d.generated_at)}
                </span>
              </CardBody>
            </Card>

            {!d.available && (
              <WarningBanner tone="info" title="This run predates signal health">
                Source status is not recorded; held-out tiers and stopped scenarios are still shown.
              </WarningBanner>
            )}

            {d.available && (
              <Card>
                <CardHeader title="Sources" subtitle="worst first — held-out tiers and stopped scenarios count only in attention, not in overall" />
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                      <tr>
                        <th className="px-4 py-2 font-medium">Source</th>
                        <th className="px-4 py-2 font-medium">Status</th>
                        <th className="px-4 py-2 font-medium">Latest</th>
                        <th className="px-4 py-2 text-right font-medium">Staleness</th>
                        <th className="px-4 py-2 text-right font-medium">Coverage</th>
                        <th className="px-4 py-2 font-medium">Holds</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {d.sources.map((s) => <SourceRow key={s.source} s={s} />)}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <Card>
                <CardHeader title="Held out by the plausibility gate" subtitle={`${d.held_out.length} tier(s)`} />
                {d.held_out.length === 0 ? (
                  <CardBody className="text-xs text-fg-muted">none</CardBody>
                ) : (
                  <div className="divide-y divide-border-subtle">
                    {d.held_out.map((h) => <HeldOutCard key={`${h.scenario}|${h.severity}`} h={h} />)}
                  </div>
                )}
              </Card>
              <Card>
                <CardHeader title="Stopped (fail-loud)" subtitle={`${d.stopped.length} scenario(s)`} />
                {d.stopped.length === 0 ? (
                  <CardBody className="text-xs text-fg-muted">none</CardBody>
                ) : (
                  <div className="divide-y divide-border-subtle">
                    {d.stopped.map((s, i) => (
                      <div key={`${s.scenario}-${i}`} className="px-4 py-2.5">
                        <div className="text-sm font-medium text-fg">{s.scenario}</div>
                        <div className="text-xs text-danger-fg">{s.reason}</div>
                        {s.detail && <div className="text-micro text-fg-muted">{s.detail}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}

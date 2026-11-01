import type { ReplayWindow, ScenarioDetailResponse } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { fmtMove, fmtRawMove } from "./format";
import { cn } from "@/lib/cn";

// The definition of a scenario tier that is not in the date's reports (held out by the gate, stopped
// fail-loud, or not run) and the replay window of a replay scenario. The BFF resolves the drivers and
// horizon with report_views.definition_view (report > version store > config > unresolved replay).

const SOURCE_LABEL: Record<string, string> = {
  report: "from the run report (× severity)",
  version: "replay-resolved spec from the version store",
  config: "authored in scenarios.json",
  unresolved: "replay not resolved — drivers come from the episode at run time",
};

export function ReplayWindowCard({ replay }: { replay: ReplayWindow }) {
  const excluded = Object.entries(replay.excluded ?? {});
  const partial = Object.entries(replay.partial ?? {});
  return (
    <Card>
      <CardHeader
        title="Replay window"
        subtitle={`${replay.start} – ${replay.end}${replay.horizon_days != null ? ` · ${replay.horizon_days} trading days` : ""}${replay.instantaneous ? " · applied as one instantaneous shock" : ""}`}
      />
      <CardBody className="space-y-2 text-sm">
        {replay.n_observed != null && (
          <div className="text-fg-secondary">
            <span className="tnum font-medium text-fg">{replay.n_observed}</span> observed factor move(s) replayed as drivers
          </div>
        )}
        {excluded.length > 0 && (
          <div>
            <div className="text-xs font-semibold text-fg-secondary">Excluded idiosyncratic moves</div>
            <table className="mt-1 w-full text-xs">
              <tbody className="divide-y divide-border-subtle">
                {excluded.map(([f, e]) => (
                  <tr key={f}>
                    <td className="py-1 pr-2 font-mono text-fg-secondary">{f}</td>
                    <td className={cn("py-1 pr-2 text-right tnum", e.move >= 0 ? "text-success-fg" : "text-danger-fg")}>{fmtRawMove(f, e.move)}</td>
                    <td className="py-1 text-fg-muted">{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {partial.length > 0 && (
          <div className="text-micro text-fg-muted">
            History starts inside the window: {partial.map(([f, d]) => `${f} (from ${d})`).join(", ")}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function NotRunDetail({ d }: { d: ScenarioDetailResponse }) {
  const def = d.definition;
  const tone = d.outcome === "stopped" ? "error" : d.outcome === "held_out" ? "warn" : "info";
  const title = d.outcome === "held_out" ? "Held out by the plausibility gate" : d.outcome === "stopped" ? "Scenario stopped" : "Scenario not run";
  const drivers = Object.entries(d.drivers ?? {});
  const overlays = Object.entries(d.overlays ?? {});
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="space-y-1">
          <h1 className="text-lg font-semibold text-fg">{d.scenario} <span className="text-sm font-normal text-fg-muted">· {d.severity}</span></h1>
          {d.narrative && <p className="max-w-2xl text-sm text-fg-secondary">{d.narrative}</p>}
          {d.trigger && <p className="text-xs text-fg-muted">trigger: {d.trigger}</p>}
        </CardBody>
      </Card>

      <WarningBanner tone={tone} title={title}>{d.reason}</WarningBanner>

      {d.outcome === "held_out" && (d.findings ?? []).length > 0 && (
        <Card>
          <CardHeader title="Gate findings" subtitle={d.scenario_version ? `version ${d.scenario_version.slice(0, 8)}` : undefined} />
          <CardBody>
            <ul className="space-y-0.5 text-xs text-fg-secondary">
              {(d.findings ?? []).map((f, i) => (
                <li key={i}>
                  <Zone status={f.severity === "fail" ? "red" : f.severity === "flag" ? "amber" : "unknown"} label={f.severity ?? "—"} />{" "}
                  {f.check}{f.factor ? ` · ${f.factor}` : ""}{f.message ? ` — ${f.message}` : ""}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {def && (
        <Card>
          <CardHeader
            title="Definition"
            subtitle={`${SOURCE_LABEL[def.source] ?? def.source} · horizon ${def.horizon_days != null ? `${def.horizon_days}d` : "resolved at run time"}${d.method ? ` · ${d.method}` : ""}`}
          />
          <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-1">
              <div className="text-xs font-semibold text-fg-secondary">Drivers{def.scaled ? "" : " (before severity scaling)"}</div>
              {drivers.length === 0 && <span className="text-xs text-fg-muted">none resolved</span>}
              {drivers.map(([f, m]) => (
                <div key={f} className="flex justify-between text-sm">
                  <span className="font-mono text-fg-secondary">{f}</span>
                  <span className="tnum font-medium text-cat-indigo-fg">{fmtMove(m)}</span>
                </div>
              ))}
            </div>
            <div className="space-y-1">
              <div className="text-xs font-semibold text-fg-secondary">Overlays</div>
              {overlays.length === 0 && <span className="text-xs text-fg-muted">none</span>}
              {overlays.map(([f, m]) => (
                <div key={f} className="flex justify-between text-sm">
                  <span className="font-mono text-fg-secondary">{f}</span>
                  <span className="tnum font-medium text-warning-fg">{fmtMove(m)}</span>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {d.replay && <ReplayWindowCard replay={d.replay} />}
    </div>
  );
}

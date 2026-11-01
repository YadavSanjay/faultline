import { useState } from "react";
import {
  useJobCatalog,
  useJobs,
  useJobDetail,
  useLaunchJob,
  getOperator,
  type JobRow,
} from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { fmtTimestamp, relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";

// Ops › Jobs: trigger a whitelisted engine run (pricer / risk / scenarios / reverse-stress
// solve / sizing / governance) as a background job, and watch it complete. Gated + audited;
// the list live-polls while anything is running.

function statusZone(s: string): ZoneStatus {
  if (s === "ok") return "green";
  if (s === "failed") return "red";
  if (s === "running") return "amber";
  return "unknown";
}

function duration(job: JobRow): string {
  if (!job.finished_at) return "running…";
  const ms = new Date(job.finished_at).getTime() - new Date(job.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(0)}s` : `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}`;
}

export function Jobs() {
  const catalog = useJobCatalog();
  const jobs = useJobs();
  const launch = useLaunchJob();
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useJobDetail(selected);

  const pending = catalog.data?.jobs.find((j) => j.key === pendingKey) ?? null;

  const submit = () => {
    if (!pendingKey) return;
    launch.mutate({ key: pendingKey, note }, {
      onSuccess: () => {
        setPendingKey(null);
        setNote("");
      },
    });
  };

  return (
    <div className="space-y-5">
      {/* trigger panel */}
      <Card>
        <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
          <h3 className="text-sm font-semibold text-fg">Trigger a run</h3>
          <span className="text-micro text-fg-muted">operator {getOperator()}</span>
        </div>
        <CardBody>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(catalog.data?.jobs ?? []).map((j) => (
              <div key={j.key} className="flex items-center justify-between rounded border border px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-fg">{j.label}</div>
                  <div className="truncate font-mono text-nano text-fg-muted">{j.cmd}</div>
                </div>
                <button
                  type="button"
                  onClick={() => setPendingKey(j.key)}
                  className="ml-2 shrink-0 rounded border border-primary/40 px-2 py-1 text-xs font-medium text-primary hover:bg-primary-tint"
                >
                  Run
                </button>
              </div>
            ))}
          </div>
          <p className="mt-3 text-micro text-fg-muted">
            Runs execute as background subprocesses of the whitelisted runners (no custom commands). Each launch is
            gated + audited; results land in the stores and Run History as usual.
          </p>
        </CardBody>
      </Card>

      {/* recent jobs */}
      <Card>
        <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
          <h3 className="text-sm font-semibold text-fg">Recent jobs</h3>
          {(jobs.data?.n_running ?? 0) > 0 && <Zone status="amber" label={`${jobs.data?.n_running} running`} />}
        </div>
        <QueryState query={jobs}>
          {(data) => (
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Run</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Operator</th>
                    <th className="px-4 py-2 font-medium">Started</th>
                    <th className="px-4 py-2 font-medium">Duration</th>
                    <th className="px-4 py-2 font-medium">rc</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {data.jobs.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-6 text-center text-sm text-fg-muted">No jobs triggered yet.</td></tr>
                  )}
                  {data.jobs.map((j) => (
                    <tr
                      key={j.job_id}
                      onClick={() => setSelected(j.job_id === selected ? null : j.job_id)}
                      className={cn("cursor-pointer hover:bg-surface-2", selected === j.job_id && "bg-surface-2")}
                    >
                      <td className="px-4 py-2 text-fg">{j.label}</td>
                      <td className="px-4 py-2"><Zone status={statusZone(j.status)} label={j.status} /></td>
                      <td className="px-4 py-2 text-xs text-fg-secondary">{j.actor}</td>
                      <td className="px-4 py-2 text-xs text-fg-muted" title={fmtTimestamp(j.started_at)}>{relativeTime(j.started_at)}</td>
                      <td className="px-4 py-2 text-xs text-fg-secondary">{duration(j)}</td>
                      <td className={cn("px-4 py-2 text-xs", j.returncode ? "text-danger-fg" : "text-fg-muted")}>{j.returncode ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>

      {/* selected job log */}
      {selected && (
        <Card>
          <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
            Log tail <span className="font-normal font-mono text-xs text-fg-muted">{selected}</span>
          </div>
          <CardBody>
            {detail.data?.available ? (
              <pre className="max-h-72 overflow-auto rounded bg-surface-code border border-border p-3 text-micro leading-relaxed text-fg-code">
                {detail.data.log_tail || "(no output captured yet)"}
              </pre>
            ) : (
              <div className="text-xs text-fg-muted">Loading…</div>
            )}
          </CardBody>
        </Card>
      )}

      {/* launch confirm */}
      {pending && (
        <ConfirmDialog
          open
          title={`Trigger — ${pending.label}`}
          confirmLabel="Run now"
          busy={launch.isPending}
          error={launch.error ? (launch.error as Error).message : null}
          onConfirm={submit}
          onCancel={() => {
            setPendingKey(null);
            setNote("");
          }}
        >
          <div className="text-xs text-fg-secondary">
            Launches <span className="font-mono">{pending.cmd}</span> as a background job. It may take minutes and
            will write to the platform stores.
          </div>
          <label className="block text-xs">
            <span className="text-fg-secondary">Note</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm"
              placeholder="why are you triggering this run?"
            />
          </label>
          <div className="text-micro text-fg-muted">Recorded to the audit trail under operator {getOperator()}.</div>
        </ConfirmDialog>
      )}
    </div>
  );
}

import { useState } from "react";
import {
  useScenarioVersions,
  useScenarioSignoff,
  useSignoffMutation,
  useAudit,
  useWriteMode,
  getOperator,
  setOperator,
} from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { fmtTimestamp } from "@/lib/format";
import { signoffZone } from "./format";

type Pending = { version: string; name: string; status: "approved" | "rejected" };

function SignoffDialog({ pending, onClose }: { pending: Pending; onClose: () => void }) {
  const mutation = useSignoffMutation();
  const [approver, setApprover] = useState(getOperator);
  const [note, setNote] = useState("");
  const isReject = pending.status === "rejected";

  const submit = () => {
    setOperator(approver);
    mutation.mutate(
      { scenario_version: pending.version, scenario: pending.name, status: pending.status, note },
      { onSuccess: onClose },
    );
  };

  return (
    <ConfirmDialog
      open
      title={`${isReject ? "Reject" : "Approve"} scenario version`}
      confirmLabel={isReject ? "Reject" : "Approve"}
      tone={isReject ? "danger" : "primary"}
      busy={mutation.isPending}
      disabled={!approver.trim()}
      error={mutation.error ? (mutation.error as Error).message : null}
      onConfirm={submit}
      onCancel={onClose}
    >
      <div className="text-xs text-fg-secondary">
        {pending.name} · <span className="font-mono">{pending.version.slice(0, 12)}</span>
      </div>
      <label className="block text-xs">
        <span className="text-fg-secondary">Operator</span>
        <input
          value={approver}
          onChange={(e) => setApprover(e.target.value)}
          className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm"
        />
      </label>
      <label className="block text-xs">
        <span className="text-fg-secondary">Note {isReject && <span className="text-danger-fg">(recommended)</span>}</span>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm"
          placeholder={isReject ? "why is this rejected?" : "optional approval note"}
        />
      </label>
      <div className="text-micro text-fg-muted">Recorded to the sign-off store and the audit trail under this operator.</div>
    </ConfirmDialog>
  );
}

function WriteModeBadge() {
  const mode = useWriteMode();
  const m = mode.data?.mode;
  if (!m) return null;
  return (
    <span
      className="rounded bg-surface-0 px-1.5 py-0.5 text-nano font-medium text-fg-secondary"
      title={m === "token" ? "Writes require a token (set it in Admin)" : "Local-trust: writes allowed, recorded by operator"}
    >
      writes: {m} · operator {getOperator()}
    </span>
  );
}

// SC-B7 Versioning: immutable scenario-definition version history, with gated approve/reject.
export function Versions() {
  const query = useScenarioVersions();
  const [pending, setPending] = useState<Pending | null>(null);

  return (
    <QueryState query={query} empty={(d) => d.versions.length === 0}>
      {(data) => (
        <>
          <Card>
            <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
              <span className="text-xs text-fg-secondary">
                Content-addressed by economic content (drivers + overlays + method + horizon).
              </span>
              <WriteModeBadge />
            </div>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Scenario</th>
                    <th className="px-4 py-2 font-medium">Version</th>
                    <th className="px-4 py-2 font-medium">Method</th>
                    <th className="px-4 py-2 text-right font-medium">Drivers</th>
                    <th className="px-4 py-2 text-right font-medium">Overlays</th>
                    <th className="px-4 py-2 font-medium">Sign-off</th>
                    <th className="px-4 py-2 font-medium">Created</th>
                    <th className="px-4 py-2 font-medium">Decision</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {data.versions.map((v) => (
                    <tr key={v.scenario_version} className="hover:bg-surface-2">
                      <td className="px-4 py-2 font-medium text-fg">{v.name}</td>
                      <td className="px-4 py-2"><ProvenanceBadge items={{ cov_id: v.scenario_version }} /></td>
                      <td className="px-4 py-2 text-xs text-fg-secondary">{v.method}</td>
                      <td className="px-4 py-2 text-right tnum text-fg-secondary">{v.n_drivers}</td>
                      <td className="px-4 py-2 text-right tnum text-fg-secondary">{v.n_overlays}</td>
                      <td className="px-4 py-2"><Zone status={signoffZone(v.signoff)} label={v.signoff} /></td>
                      <td className="px-4 py-2 text-xs text-fg-muted">{fmtTimestamp(v.created_at)}</td>
                      <td className="px-4 py-2">
                        <div className="flex gap-1">
                          <button
                            type="button"
                            onClick={() => setPending({ version: v.scenario_version, name: v.name, status: "approved" })}
                            className="rounded border border-primary/40 px-1.5 py-0.5 text-micro font-medium text-primary hover:bg-primary-tint"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            onClick={() => setPending({ version: v.scenario_version, name: v.name, status: "rejected" })}
                            className="rounded border border-danger/30 px-1.5 py-0.5 text-micro font-medium text-danger-fg hover:bg-danger-tint"
                          >
                            Reject
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          {pending && <SignoffDialog key={`${pending.version}-${pending.status}`} pending={pending} onClose={() => setPending(null)} />}
        </>
      )}
    </QueryState>
  );
}

// SC-B5 Sign-off: the approve/reject decisions (latest per version) + the audit trail.
export function SignoffPage() {
  const query = useScenarioSignoff();
  const versions = useScenarioVersions();
  const audit = useAudit(30);
  const nameByVersion = new Map((versions.data?.versions ?? []).map((v) => [v.scenario_version, v.name]));

  return (
    <div className="space-y-5">
      <QueryState query={query}>
        {(data) => {
          const records = Object.entries(data.records);
          return (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-xs text-fg-secondary">
                Latest decision per scenario version wins. Approve or reject from the Versions tab.
              </div>
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-4 py-2 font-medium">Scenario</th>
                      <th className="px-4 py-2 font-medium">Version</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                      <th className="px-4 py-2 font-medium">Approver</th>
                      <th className="px-4 py-2 font-medium">Note</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {records.length === 0 && (
                      <tr><td colSpan={5} className="px-4 py-6 text-center text-sm text-fg-muted">No sign-off records yet.</td></tr>
                    )}
                    {records.map(([ver, rec]) => (
                      <tr key={ver} className="hover:bg-surface-2">
                        <td className="px-4 py-2 font-medium text-fg">{nameByVersion.get(ver) ?? "—"}</td>
                        <td className="px-4 py-2"><ProvenanceBadge items={{ cov_id: ver }} /></td>
                        <td className="px-4 py-2"><Zone status={signoffZone(rec.status)} label={rec.status} /></td>
                        <td className="px-4 py-2 text-fg-secondary">{rec.approver ?? "—"}</td>
                        <td className="px-4 py-2 text-xs text-fg-secondary">{rec.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          );
        }}
      </QueryState>

      {/* audit trail */}
      <Card>
        <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
          Audit trail <span className="font-normal text-fg-muted">· append-only record of every write</span>
        </div>
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
              <tr>
                <th className="px-4 py-2 font-medium">When</th>
                <th className="px-4 py-2 font-medium">Action</th>
                <th className="px-4 py-2 font-medium">Target</th>
                <th className="px-4 py-2 font-medium">Operator</th>
                <th className="px-4 py-2 font-medium">Outcome</th>
                <th className="px-4 py-2 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {(audit.data?.entries ?? []).length === 0 && (
                <tr><td colSpan={6} className="px-4 py-6 text-center text-sm text-fg-muted">No writes recorded yet.</td></tr>
              )}
              {(audit.data?.entries ?? []).map((e, i) => (
                <tr key={`${e.ts}-${i}`} className="hover:bg-surface-2">
                  <td className="px-4 py-1.5 text-xs text-fg-muted">{fmtTimestamp(e.ts)}</td>
                  <td className="px-4 py-1.5 font-mono text-xs text-fg-secondary">{e.action}</td>
                  <td className="px-4 py-1.5 font-mono text-xs text-fg-secondary">
                    {String(e.details?.status ?? "")} <span className="text-fg-muted">{e.target.slice(0, 12)}</span>
                  </td>
                  <td className="px-4 py-1.5 text-xs text-fg-secondary">{e.actor}</td>
                  <td className="px-4 py-1.5">
                    <span className={e.outcome === "ok" ? "text-success-fg" : "text-danger-fg"}>{e.outcome}</span>
                  </td>
                  <td className="px-4 py-1.5 text-xs text-fg-secondary">{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

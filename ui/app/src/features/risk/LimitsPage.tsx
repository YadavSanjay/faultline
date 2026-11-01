import { useState } from "react";
import { useRiskLimits, useLimitEditMutation, type RiskLimit } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// RK-B6 Limits: the mandate control as a utilisation view — every configured limit with
// observed value, ceiling, and headroom, shown as a gauge even when within mandate. Ceilings
// are editable via a gated, audited write.

function utilTone(u: number): { bar: string; text: string; label: string } {
  if (u > 1) return { bar: "bg-danger", text: "text-danger-fg", label: "breached" };
  if (u >= 0.75) return { bar: "bg-warning", text: "text-warning-fg", label: "near limit" };
  return { bar: "bg-success", text: "text-success-fg", label: "within mandate" };
}

function LimitGauge({ l, onEdit }: { l: RiskLimit; onEdit: () => void }) {
  const tone = utilTone(l.utilization);
  const pct = Math.min(100, l.utilization * 100);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <span className="font-mono text-sm text-fg">{l.name}</span>
          <span className="ml-2 text-micro uppercase tracking-wide text-fg-muted">{l.kind}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className={cn("tnum text-sm font-semibold", tone.text)}>{(l.utilization * 100).toFixed(1)}%</div>
          <button
            type="button"
            onClick={onEdit}
            className="rounded border border px-1.5 py-0.5 text-micro font-medium text-fg-secondary hover:bg-surface-2 hover:text-fg"
          >
            Edit ceiling
          </button>
        </div>
      </div>
      <div className="relative h-3 overflow-hidden rounded bg-surface-0">
        <div className={cn("h-full rounded", tone.bar)} style={{ width: `${pct}%` }} />
        <div className="absolute top-0 h-full border-l border-strong" style={{ left: "75%" }} />
      </div>
      <div className="flex items-center justify-between text-micro text-fg-secondary">
        <span title={exactUsd(l.observed)}>observed {humanizeUsd(l.observed)}</span>
        <span className={tone.text}>{tone.label}</span>
        <span title={exactUsd(l.limit)}>
          ceiling {humanizeUsd(l.limit)} · headroom {humanizeUsd(l.headroom)}
        </span>
      </div>
    </div>
  );
}

function EditLimitDialog({ limit, onClose }: { limit: RiskLimit; onClose: () => void }) {
  const mutation = useLimitEditMutation();
  const [ceiling, setCeiling] = useState(String(limit.limit));
  const [note, setNote] = useState("");
  const parsed = Number(ceiling);
  const valid = Number.isFinite(parsed) && parsed > 0;
  const newUtil = valid && parsed ? limit.observed / parsed : null;

  const submit = () => {
    mutation.mutate(
      { kind: limit.kind, key: limit.key, ceiling: parsed, note },
      { onSuccess: onClose },
    );
  };

  return (
    <ConfirmDialog
      open
      title={`Edit limit — ${limit.name}`}
      confirmLabel="Save ceiling"
      busy={mutation.isPending}
      disabled={!valid}
      error={mutation.error ? (mutation.error as Error).message : null}
      onConfirm={submit}
      onCancel={onClose}
    >
      <div className="text-xs text-fg-secondary">
        observed <span className="tnum" title={exactUsd(limit.observed)}>{humanizeUsd(limit.observed)}</span> · current
        ceiling <span className="tnum" title={exactUsd(limit.limit)}>{humanizeUsd(limit.limit)}</span>
      </div>
      <label className="block text-xs">
        <span className="text-fg-secondary">New ceiling (USD)</span>
        <input
          value={ceiling}
          onChange={(e) => setCeiling(e.target.value)}
          inputMode="decimal"
          className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm tnum"
        />
        {newUtil != null && (
          <span className={cn("ml-1 text-micro", newUtil > 1 ? "text-danger-fg" : "text-fg-muted")}>
            → {(newUtil * 100).toFixed(1)}% utilisation{newUtil > 1 ? " (would breach)" : ""}
          </span>
        )}
      </label>
      <label className="block text-xs">
        <span className="text-fg-secondary">Note</span>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm"
          placeholder="rationale for the mandate change"
        />
      </label>
      <div className="text-micro text-fg-muted">
        Writes <span className="font-mono">risk.limits</span> in the pricer config and records to the audit trail.
      </div>
    </ConfirmDialog>
  );
}

export function LimitsPage() {
  const query = useRiskLimits();
  const [editing, setEditing] = useState<RiskLimit | null>(null);
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(data) => {
        const worst = data.max_utilization;
        const status = data.n_breaches > 0 ? "red" : worst >= 0.75 ? "amber" : "green";
        return (
          <div className="space-y-5">
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
                <h3 className="text-sm font-semibold text-fg">Risk limits · utilisation</h3>
                <div className="flex items-center gap-3">
                  <Zone
                    status={status}
                    label={data.n_breaches > 0 ? `${data.n_breaches} breaches` : "within mandate"}
                  />
                  <span className="text-xs text-fg-secondary">
                    {data.n_limits} limits · peak {(worst * 100).toFixed(1)}% used
                  </span>
                </div>
              </div>
              <CardBody className="space-y-5">
                {data.limits.length === 0 ? (
                  <div className="text-sm text-fg-secondary">No risk limits configured for this book.</div>
                ) : (
                  data.limits.map((l) => <LimitGauge key={l.name} l={l} onEdit={() => setEditing(l)} />)
                )}
              </CardBody>
            </Card>
            <div className="text-xs text-fg-muted">
              Observed values mirror the risk engine's limit monitor (VaR loss per level; absolute aggregate book
              sensitivity). Ceilings are desk mandate from <span className="font-mono">risk.limits</span> in the pricer
              config. Editing a ceiling is a gated, audited write.
            </div>
            {editing && <EditLimitDialog key={editing.name} limit={editing} onClose={() => setEditing(null)} />}
          </div>
        );
      }}
    </QueryState>
  );
}

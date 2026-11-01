import { useState } from "react";
import {
  useSizingRebalance,
  useBookVariant,
  useActiveBook,
  useActivateVariantMutation,
  type RebalanceTrade,
} from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { PROFILE_ORDER, themeFor, signedPct } from "./format";
import { cn } from "@/lib/cn";

// Rebalancing blotter: the sizing engine's target book expressed as executable trades —
// current → target notional, signed delta, and the book-variant download. Read-only: this
// shows what to trade to reach the mandate; it does not place or apply anything.

function DirCell({ t }: { t: RebalanceTrade }) {
  if (t.direction === "hold") return <span className="text-fg-muted">— hold</span>;
  const up = t.direction === "increase";
  return (
    <span className={cn("tnum", up ? "text-success-fg" : "text-fg-secondary")} title={exactUsd(t.delta_notional)}>
      {up ? "▲" : "▼"} {humanizeUsd(Math.abs(t.delta_notional))}
      {t.pct_change != null && <span className="ml-1 text-micro text-fg-muted">{signedPct(t.pct_change, 0)}</span>}
    </span>
  );
}

const COLUMNS: DataColumn<RebalanceTrade>[] = [
  {
    key: "position",
    header: "Position",
    get: (t) => t.trade_id,
    filterable: true,
    render: (t) => (
      <div className="flex items-center gap-2">
        {t.is_hedge && <span className="rounded bg-warning-tint px-1 py-0.5 text-nano font-bold text-warning-fg">HEDGE</span>}
        <span className="font-mono text-micro text-fg-muted">{t.trade_id.replace("TRADE_", "#")}</span>
        <span className="truncate text-fg-secondary" style={{ maxWidth: 260 }} title={t.description}>{t.description}</span>
      </div>
    ),
    csv: (t) => t.trade_id,
  },
  {
    key: "current_notional", header: "Current", align: "right", get: (t) => t.current_notional, csv: (t) => t.current_notional,
    render: (t) => <span className="text-fg-muted" title={exactUsd(t.current_notional)}>{humanizeUsd(t.current_notional)}</span>,
  },
  {
    key: "target_notional", header: "Target", align: "right", get: (t) => t.target_notional, csv: (t) => t.target_notional,
    render: (t) => <span className="text-fg" title={exactUsd(t.target_notional)}>{humanizeUsd(t.target_notional)}</span>,
  },
  {
    key: "delta_notional", header: "Trade (Δ)", align: "right", get: (t) => t.delta_notional, csv: (t) => t.delta_notional,
    render: (t) => <DirCell t={t} />,
  },
  { key: "multiplier", header: "×", align: "right", get: (t) => t.multiplier, render: (t) => <span className="text-fg-secondary">{t.multiplier?.toFixed(2)}×</span> },
  {
    key: "risk_share", header: "Risk share", align: "right", get: (t) => t.risk_share, csv: (t) => t.risk_share,
    render: (t) => (
      <span className={cn(t.risk_share < 0 ? "text-info-fg" : "text-fg-secondary")} title={exactUsd(t.risk_contribution)}>
        {signedPct(t.risk_share, 0)}
      </span>
    ),
  },
];

function ActivateAction({ profile }: { profile: string }) {
  const active = useActiveBook();
  const mutation = useActivateVariantMutation();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const isActive = active.data?.profile === profile;

  const submit = () => {
    mutation.mutate({ profile, note }, { onSuccess: () => setOpen(false) });
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={isActive}
        title={isActive ? "already the active book" : `activate the ${profile} book`}
        className={cn(
          "rounded border px-2.5 py-1 text-xs font-medium",
          isActive
            ? "cursor-default border text-fg-muted"
            : "border-primary/40 text-primary hover:bg-primary-tint",
        )}
      >
        {isActive ? "✓ active book" : "Activate this book"}
      </button>
      {open && (
        <ConfirmDialog
          open
          title={`Activate the ${profile} book`}
          confirmLabel="Activate"
          busy={mutation.isPending}
          error={mutation.error ? (mutation.error as Error).message : null}
          onConfirm={submit}
          onCancel={() => setOpen(false)}
        >
          <div className="text-xs text-fg-secondary">
            Promote <span className="font-medium">portfolio_{profile}.json</span> to the active book
            (<span className="font-mono">portfolio.json</span>). Every pricing, risk and scenario run will price this
            book next. This does not re-run anything now.
          </div>
          {active.data?.profile && (
            <div className="text-micro text-fg-muted">currently active: {active.data.profile}</div>
          )}
          <label className="block text-xs">
            <span className="text-fg-secondary">Note</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-0.5 w-full rounded border border-strong px-2 py-1 text-sm"
              placeholder="rationale for switching the active book"
            />
          </label>
          <div className="text-micro text-fg-muted">Recorded to the audit trail under this operator.</div>
        </ConfirmDialog>
      )}
    </>
  );
}

function ActiveBadge() {
  const active = useActiveBook();
  const p = active.data?.profile;
  return (
    <span className="rounded bg-surface-0 px-1.5 py-0.5 text-micro text-fg-secondary">
      active book: <span className="font-medium text-fg">{p ?? "base / unsized"}</span>
    </span>
  );
}

function DownloadVariant({ profile }: { profile: string }) {
  const variant = useBookVariant(profile);
  const v = variant.data;
  const ready = !!v?.available && !!v.book;
  const onDownload = () => {
    if (!v?.book) return;
    const blob = new Blob([JSON.stringify(v.book, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = v.filename ?? `portfolio_${profile}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      type="button"
      disabled={!ready}
      onClick={onDownload}
      title={ready ? `Download ${v?.filename}` : "book variant not generated yet"}
      className={cn(
        "rounded border px-2.5 py-1 text-xs font-medium",
        ready
          ? "border-strong text-fg hover:bg-surface-2"
          : "cursor-not-allowed border text-fg-muted",
      )}
    >
      ↓ Book variant JSON
    </button>
  );
}

export function RebalanceBlotter() {
  const [profile, setProfile] = useState<string>("aggressive");
  const query = useSizingRebalance(profile);

  return (
    <div className="space-y-4">
      {/* profile selector */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          {PROFILE_ORDER.map((k) => {
            const t = themeFor(k);
            return (
              <button
                key={k}
                type="button"
                onClick={() => setProfile(k)}
                className={cn(
                  "rounded border px-3 py-1 text-xs font-medium",
                  profile === k ? cn("border-primary", t.accent) : "border text-fg-secondary hover:text-fg",
                )}
              >
                {t.label}
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <ActiveBadge />
          <DownloadVariant profile={profile} />
          <ActivateAction profile={profile} />
        </div>
      </div>

      <QueryState query={query} empty={(d) => !d.available}>
        {(data) => {
          const metrics: Metric[] = [
            { label: "Gross turnover", value: humanizeUsd(data.gross_turnover), caption: "Σ |Δ notional| to reach target" },
            { label: "Trades to lift", value: `${data.n_increase} ↑`, caption: `${data.n_decrease} ↓ · ${data.n_hold} hold`, tone: "gain" },
            { label: "Binding constraint", value: (data.binding_constraint ?? "—").replace(/_/g, " "), caption: `VaR budget ${humanizeUsd(data.var_budget)}` },
            { label: "Target VaR 99%", value: humanizeUsd(data.achieved_var99), caption: `gross leverage ${data.gross_leverage?.toFixed(2)}×` },
          ];
          return (
            <div className="space-y-4">
              <Card>
                <CardBody>
                  <MetricGrid metrics={metrics} columns={4} />
                </CardBody>
              </Card>

              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Rebalance blotter · {data.label} <span className="font-normal text-fg-muted">· sortable · CSV export</span>
                </div>
                <DataTable
                  columns={COLUMNS}
                  rows={data.trades}
                  getRowKey={(t) => t.trade_id}
                  pageSize={30}
                  exportName={`rebalance_${profile}`}
                />
                <p className="px-4 py-2 text-micro text-fg-muted">
                  Δ is the notional to trade to move from the current book to the {data.label} target. Risk share is
                  each position's contribution to portfolio VaR (blue = a net diversifier / hedge). Download the full
                  sized book above; applying it is a gated write action (post-V1).
                </p>
              </Card>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
